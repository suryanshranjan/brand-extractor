"""Reading text in images. Two backends:

* OCR.space: cloud API, nothing heavy to install (good for servers). Free key: https://ocr.space/ocrapi
* EasyOCR: runs locally, no key, but needs PyTorch (~2 GB).
"""
from __future__ import annotations

import base64
import logging
import time

from curl_cffi import requests as http
from curl_cffi.requests.exceptions import RequestException

log = logging.getLogger(__name__)


class OcrSpaceReader:
    URL = "https://api.ocr.space/parse/image"
    MAX_BYTES = 1024 * 1024   # free-tier file size limit

    def __init__(self, api_key: str, min_interval: float = 0.6):
        self.api_key = api_key
        self.min_interval = min_interval
        self.session = http.Session(timeout=60)
        self._last = 0.0

    def read(self, image_bytes: bytes) -> str:
        if len(image_bytes) > self.MAX_BYTES:
            log.debug("Image too large for OCR.space free tier (%d bytes), skipped", len(image_bytes))
            return ""
        payload = {
            "base64Image": "data:image/jpeg;base64," + base64.b64encode(image_bytes).decode(),
            "apikey": self.api_key,
            "language": "eng",
            "OCREngine": "2",
            "scale": "true",
            "detectOrientation": "true",
        }
        for attempt in range(3):
            wait = self._last + self.min_interval - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
            try:
                resp = self.session.post(self.URL, data=payload)
            except RequestException as exc:
                log.debug("OCR.space request failed: %s", exc)
                time.sleep(5)
                continue
            if resp.status_code in (403, 429):
                log.warning("OCR.space rate limit, waiting %ds", 5 * (attempt + 1))
                time.sleep(5 * (attempt + 1))
                continue
            try:
                data = resp.json()
            except ValueError:
                return ""
            if data.get("IsErroredOnProcessing"):
                log.debug("OCR.space error: %s", data.get("ErrorMessage"))
                return ""
            return "\n".join(r.get("ParsedText", "") for r in data.get("ParsedResults") or []).strip()
        return ""


class EasyOcrReader:
    def __init__(self, gpu: bool = False):
        import cv2  # noqa: F401  (raise ImportError early if missing)
        import easyocr
        log.info("Loading EasyOCR model (first run downloads ~100 MB)...")
        self.reader = easyocr.Reader(["en"], gpu=gpu, verbose=False)

    def read(self, image_bytes: bytes) -> str:
        import cv2
        import numpy as np
        img = cv2.imdecode(np.frombuffer(image_bytes, np.uint8), cv2.IMREAD_GRAYSCALE)
        if img is None:
            return ""
        # One line per detected text box, low-confidence boxes dropped; keeps
        # "POWERED BY" next to its brand and logo names on their own lines.
        return "\n".join(text for _, text, conf in self.reader.readtext(img) if conf >= 0.4)


def load_reader(backend: str = "auto", ocrspace_api_key: str = "", gpu: bool = False):
    """Return an OCR reader, or None when OCR is off or unavailable."""
    if backend == "none":
        return None
    if backend in ("auto", "ocrspace") and ocrspace_api_key:
        log.info("OCR: OCR.space")
        return OcrSpaceReader(ocrspace_api_key)
    if backend == "ocrspace":
        log.warning("OCR disabled: set OCRSPACE_API_KEY (free key at https://ocr.space/ocrapi)")
        return None
    try:
        reader = EasyOcrReader(gpu=gpu)
        log.info("OCR: EasyOCR")
        return reader
    except ImportError:
        log.warning("OCR disabled: set OCRSPACE_API_KEY, or  pip install easyocr")
        return None
