"""Settings (from environment / .env) and scoring constants."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# --- Instagram web client ---
IG_BASE = "https://www.instagram.com"
IG_APP_ID = "936619743392459"
PAGE_SIZE = 12
REQUEST_DELAY = (1.5, 3.5)          # random pause between API calls (seconds)
RATE_LIMIT_BACKOFF = (60, 180)      # waits before retrying a rate-limited call
PROFILE_CACHE_TTL = 14 * 24 * 3600  # re-check a looked-up profile after 14 days
LOGIN_TIMEOUT = 300                 # seconds to wait for a manual browser login

# --- Scoring ---
# Points one post contributes to an entity, per signal source.
# A source counts once per post per entity.
WEIGHTS = {
    "paid_partnership": 50,   # Instagram "Paid partnership with X" label
    "collab_author": 30,      # X is a co-author of a collab post
    "role_phrase": 25,        # "powered by X", "gold sponsor: X" in caption / image text
    "sponsor_board": 12,      # X listed on an "Our sponsors" poster
    "caption_mention": 10,    # @X in the caption
    "photo_tag": 8,           # X tagged in the photo
    "image_mention": 8,       # @X read from the image (OCR) or Instagram alt text
    "domain_mention": 6,      # x.com / x.in in the caption
    "name_mention": 3,        # organisation name spotted by NLP in the caption
}
# Mentions in a post carrying #ad / #sponsored etc. count this much more.
SPONSORED_POST_MULTIPLIER = 1.5
SPONSORED_HASHTAGS = {
    "ad", "ads", "advertisement", "sponsored", "sponsor", "paidpartnership",
    "paidpromotion", "partner", "partnership", "brandpartner", "collab", "gifted",
}

# Category words accepted in "<word> partner/sponsor" phrases
# (title / associate / presenting / tiers have their own patterns in signals.py).
PARTNER_CATEGORIES = {
    "official", "exclusive", "event",
    "beverage", "drink", "drinks", "energy", "hydration", "coffee", "dairy", "food",
    "snacking", "snack", "nutrition", "media", "radio", "broadcast", "streaming",
    "ott", "music", "audio", "entertainment", "gaming", "esports", "travel",
    "mobility", "hospitality", "venue", "banking", "payment", "payments", "fintech",
    "investment", "insurance", "fashion", "style", "apparel", "footwear", "merch",
    "merchandise", "lifestyle", "beauty", "grooming", "fragrance", "wellness",
    "health", "fitness", "sports", "gifting", "ticketing", "education", "learning",
    "knowledge", "coding", "technology", "tech", "digital", "internet", "telecom",
    "social", "online", "community", "outreach", "printing", "photography",
    "logistics", "auto", "automobile", "sustainability", "innovation", "career",
    "hiring", "platform", "cloud", "web3", "blockchain", "ai",
}


def _load_dotenv(path: Path) -> None:
    """Minimal .env reader (KEY=value lines); real environment variables win."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


@dataclass
class Settings:
    data_dir: Path = field(default_factory=Path.cwd)
    source: str = "web"                 # "web" (Instagram web API) or "graph" (official Graph API)
    ig_login: str = ""                  # account whose saved session the web source uses
    ig_sessionid: str = ""              # or a raw sessionid cookie
    proxy: str = ""
    hourly_request_cap: int = 200       # max Instagram API calls per hour (web source)
    ocr_backend: str = "auto"           # auto | ocrspace | easyocr | none
    ocrspace_api_key: str = ""
    graph_token: str = ""
    graph_ig_user_id: str = ""
    graph_api_version: str = "v21.0"
    max_lookups: int = 80
    api_keys: list[str] = field(default_factory=list)

    @classmethod
    def from_env(cls) -> "Settings":
        _load_dotenv(Path.cwd() / ".env")
        data_dir = Path(_env("BRANDSCAN_DATA_DIR") or Path.cwd()).resolve()
        return cls(
            data_dir=data_dir,
            source=_env("BRANDSCAN_SOURCE", "web").lower(),
            ig_login=_env("IG_LOGIN"),
            ig_sessionid=_env("IG_SESSIONID"),
            proxy=_env("BRANDSCAN_PROXY"),
            hourly_request_cap=int(_env("BRANDSCAN_HOURLY_CAP", "200")),
            ocr_backend=_env("BRANDSCAN_OCR", "auto").lower(),
            ocrspace_api_key=_env("OCRSPACE_API_KEY"),
            graph_token=_env("IG_GRAPH_TOKEN"),
            graph_ig_user_id=_env("IG_GRAPH_USER_ID"),
            graph_api_version=_env("IG_GRAPH_VERSION", "v21.0"),
            max_lookups=int(_env("BRANDSCAN_MAX_LOOKUPS", "80")),
            api_keys=[k.strip() for k in _env("BRANDSCAN_API_KEYS").split(",") if k.strip()],
        )

    @property
    def sessions_dir(self) -> Path:
        return self.data_dir / "sessions"

    @property
    def output_dir(self) -> Path:
        return self.data_dir / "output"

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "brandscan.db"

    @property
    def log_file(self) -> Path:
        return self.data_dir / "brandscan.log"

    @property
    def known_brands_file(self) -> Path:
        return self.data_dir / "known_brands.txt"
