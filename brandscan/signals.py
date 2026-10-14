"""Turns a Post into Signals: who is associated with the post, how, and how strongly."""
from __future__ import annotations

import logging
import re
from collections import defaultdict
from typing import Callable

from . import config
from .lexicon import GENERIC_NAME_TOKENS, GENERIC_NAMES, IGNORED_DOMAINS, name_key
from .models import Post, Profile, Signal

log = logging.getLogger(__name__)

# @handle not preceded by a word char, dot or @ (so emails don't match).
HANDLE_RE = re.compile(r"(?<![\w.@])@([A-Za-z0-9_](?:[A-Za-z0-9_.]{0,28}[A-Za-z0-9_])?)")
HASHTAG_RE = re.compile(r"#(\w+)")
ALT_TEXT_SAYS_RE = re.compile(r"text that says ['\"‘“](.+)['\"’”]", re.S)
DOMAIN_RE = re.compile(r"(?<![@\w.])(?:https?://)?(?:www\.)?([a-z0-9][a-z0-9-]{1,40})"
                       r"\.(?:com|in|co\.in|io|ai|app|co|tech|org|xyz|club|store|shop|live)\b", re.I)
DATE_RE = re.compile(r"^(?:\d{1,2}(?:st|nd|rd|th)?\s+)?(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?(?:\s+\d{1,4})?")

TIERS = r"title|platinum|diamond|gold|silver|bronze|associate|co"
# A poster line that announces sponsor logos below it.
BOARD_HEADER_RE = re.compile(
    rf"\b(?:(?:our|event|official|{TIERS})[\s-]+)?(?:sponsors?|partners?)\b"
    r"|\b(?:sponsored|supported|powered)\s+by\b|\bin\s+association\s+with\b", re.I)
# A caption that says the images show sponsors.
BOARD_CAPTION_RE = re.compile(
    r"\b(?:our|the|event|fest|official)\s+(?:sponsors|partners)\b"
    r"|\bthank(?:s|\s+you)\s+(?:to\s+)?(?:all\s+)?(?:our\s+)?(?:sponsors|partners)\b"
    r"|\b(?:sponsors?|partners?)\s+(?:reveal|announcement|lineup)\b", re.I)

_CATEGORY = "|".join(sorted(config.PARTNER_CATEGORIES, key=len, reverse=True))


def _phrase(pattern: str) -> re.Pattern:
    # The phrase, optional "is" / separator (may span a line break, as on posters), then the name.
    return re.compile(rf"(?<!\w)(?:{pattern})(?:\s+(?:is|are|was|were))?\s*[:\-–—|]*\s*"
                      r"(?P<target>[^\n,;|!?•()\[\]]{2,60})", re.I)


# Order matters: within a post the first role found for an entity wins.
ROLE_PATTERNS: list[tuple[re.Pattern, str]] = [
    (_phrase(r"title\s+(?:sponsor|partner)s?(?:ed)?(?:\s+by)?"), "Title Sponsor"),
    (_phrase(r"presented\s+by|presenting\s+(?:sponsor|partner)s?"), "Presenting Sponsor"),
    (_phrase(r"(?P<tier>platinum|diamond|gold|silver|bronze)\s+(?:sponsor|partner)s?"), "{tier} Sponsor"),
    (_phrase(r"co[-\s]?sponsor(?:s|ed\s+by)?"), "Co-Sponsor"),
    (_phrase(r"co[-\s]?powered\s+by"), "Co-Powered By"),
    (_phrase(r"powered\s+by"), "Powered By"),
    (_phrase(r"associate\s+(?:sponsor|partner)s?|in\s+association\s+with"), "Associate Partner"),
    (_phrase(r"sponsored\s+by|brought\s+to\s+you\s+by|supported\s+by|funded\s+by"), "Sponsor"),
    (_phrase(r"special\s+thanks\s+to"), "Supporter"),
    (_phrase(r"in\s+(?:collaboration|collab|partnership)\s+with|partnered\s+with"), "Collaborator"),
    (_phrase(rf"(?P<cat>{_CATEGORY})\s+(?:partner|sponsor)s?"), "{cat} Partner"),
]
_SPLIT_RE = re.compile(r"\s+(?:&|and|x|×|\+)\s+|\s*/\s*", re.I)
_TRAILING_RE = re.compile(r"\s+(?:for|at|in|on|to|with|is|are|was|were|presents?|brings?|this|who|which|will)\b.*$", re.I)
_LEADING_RE = re.compile(r"^(?:(?:the|our|us|amazing|awesome|incredible|very|own|one and only)\s+)+", re.I)


def handle_key(handle: str) -> str:
    return handle.strip().lstrip("@").lower().rstrip(".")


def tidy_name(name: str) -> str:
    name = re.sub(r"\s+", " ", name).strip()
    name = re.sub(r"^[^\w]+|[^\w.!&+')]+$", "", name)
    name = " ".join(name.split()[:4])
    return name.title() if name.isupper() and len(name) > 3 else name


def is_plausible_name(name: str) -> bool:
    low = name.lower().strip(" !.'&+")
    return (2 <= len(name) <= 40
            and len(name.split()) <= 4
            and low not in GENERIC_NAMES
            and re.search(r"[a-z]", low) is not None
            and not re.search(r"https?://|www\.|\.com\b", low)
            and not DATE_RE.match(low))


def is_board_name(name: str) -> bool:
    """Stricter check for logo text on sponsor posters (OCR is noisy)."""
    if not is_plausible_name(name) or len(name) < 3 or len(name.split()) > 3:
        return False
    letters = sum(c.isalpha() for c in name)
    if letters / len(name.replace(" ", "")) < 0.6:
        return False
    if re.search(r"\b(?:19|20)\d\d\b|\d{3,}|[@#:]", name):
        return False
    words = name.split()
    # Sentences ("join us on") rather than names: several lowercase words.
    return sum(w.islower() for w in words) < 2


def role_phrases(text: str) -> list[tuple[str, str, str]]:
    """Find "powered by X"-style phrases. Returns (kind, value, role) tuples."""
    results = []
    for pattern, role_tpl in ROLE_PATTERNS:
        for m in pattern.finditer(text):
            groups = defaultdict(str, {k: (v or "").title() for k, v in m.groupdict().items()})
            role = role_tpl.format_map(groups)
            target = m.group("target")
            handles = HANDLE_RE.findall(target)
            if handles:
                results.extend(("handle", h, role) for h in handles)
                continue
            target = _TRAILING_RE.sub("", target)
            for part in _SPLIT_RE.split(target)[:3]:
                part = tidy_name(_LEADING_RE.sub("", part.strip()))
                if is_plausible_name(part):
                    results.append(("name", part, role))
    return results


def board_names(image_text: str, caption_says_sponsors: bool) -> list[str]:
    """Names listed on an "Our sponsors" poster: the lines after a sponsor header
    (or every line, when the caption says the post shows sponsors)."""
    lines = [l.strip() for l in image_text.splitlines() if l.strip()]
    start = next((i + 1 for i, l in enumerate(lines) if BOARD_HEADER_RE.search(l)), None)
    if start is None:
        if not caption_says_sponsors:
            return []
        start = 0
    names = []
    for line in lines[start:]:
        if BOARD_HEADER_RE.search(line):
            continue
        for part in re.split(r"\s*[|•·,]\s*|\s{3,}", line):
            if re.search(r"[@#]|www\.|\.com\b", part, re.I):   # handles are read separately
                continue
            name = tidy_name(part)
            if is_board_name(name) and name not in names:
                names.append(name)
    return names[:25]


class Exclusions:
    """Keeps the scanned account itself (its handle, name, and branding) out of the results."""

    def __init__(self, target: Profile):
        self.handle = target.username.lower()
        self.own_keys = {name_key(target.username), name_key(target.full_name)} - {""}
        words = f"{target.username.replace('_', ' ').replace('.', ' ')} {target.full_name}".lower()
        self.prefixes = {t for t in re.findall(r"[a-z0-9]+", words) if len(t) >= 5 and t not in GENERIC_NAME_TOKENS}

    def __call__(self, key: str) -> bool:
        flat = name_key(key)
        if key == self.handle or flat in self.own_keys or (len(self.handle) >= 4 and self.handle in key):
            return True
        # Part of the account's own name ("With India" inside "Hack With India MMMUT").
        if len(flat) >= 4 and any(flat in own for own in self.own_keys):
            return True
        return any(flat.startswith(p) for p in self.prefixes)


class SignalExtractor:
    def __init__(self, target: Profile, use_ner: bool = True, image_reader=None,
                 fetch_image: Callable[[str], bytes | None] | None = None, max_images: int = 6):
        self.excluded = Exclusions(target)
        self.image_reader = image_reader
        self.fetch_image = fetch_image
        self.max_images = max_images
        self.nlp = _load_nlp() if use_ner else None

    def extract(self, post: Post) -> list[Signal]:
        found: dict[tuple[str, str], Signal] = {}

        def add(kind: str, raw: str, source: str, role: str, evidence: str = "", boost: float = 1.0,
                display: str = ""):
            if kind == "handle":
                key = handle_key(raw)
                display = "@" + key
            else:
                key = name_key(raw)
                display = display or tidy_name(raw)
            if not key or self.excluded(key):
                return
            weight = config.WEIGHTS[source] * boost
            existing = found.get((key, source))
            if existing is None or existing.weight < weight:
                found[(key, source)] = Signal(key, display, kind, source, role, weight, post.code, evidence)

        caption = post.caption
        hashtags = {h.lower() for h in HASHTAG_RE.findall(caption)}
        sponsored = post.is_paid_partnership or bool(hashtags & config.SPONSORED_HASHTAGS)
        boost = config.SPONSORED_POST_MULTIPLIER if sponsored else 1.0

        for h in post.sponsors:
            add("handle", h, "paid_partnership", "Paid Partnership")
        for h in post.coauthors:
            add("handle", h, "collab_author", "Collab Post")
        for h in post.tagged:
            add("handle", h, "photo_tag", "Tagged", boost=boost)
        for h in HANDLE_RE.findall(caption):
            add("handle", h, "caption_mention", "Sponsored Mention" if sponsored else "Mentioned", boost=boost)
        for m in DOMAIN_RE.finditer(caption):
            if m.group(1).lower() not in IGNORED_DOMAINS:
                add("name", m.group(1), "domain_mention", "Website Mentioned", display=m.group(0).split("//")[-1])

        image_texts = []
        for m in post.media:
            said = ALT_TEXT_SAYS_RE.search(m.alt_text or "")
            if said:
                image_texts.append(("alt text", said.group(1)))
        image_texts.extend(("image text", t) for t in self._read_images(post))

        for origin, text in [("caption", caption)] + image_texts:
            for kind, value, role in role_phrases(text):
                add(kind, value, "role_phrase", role, evidence=origin)

        caption_says_sponsors = bool(BOARD_CAPTION_RE.search(caption))
        for origin, text in image_texts:
            for h in HANDLE_RE.findall(text):
                add("handle", h, "image_mention", "Mentioned in Image", evidence=origin)
            for name in board_names(text, caption_says_sponsors):
                add("name", name, "sponsor_board", "Listed Sponsor", evidence=origin)

        if self.nlp and caption:
            for ent in self.nlp(HASHTAG_RE.sub(" ", HANDLE_RE.sub(" ", caption))).ents:
                text = ent.text.strip()
                if (ent.label_ in ("ORG", "PRODUCT") and not text.islower() and is_plausible_name(text)):
                    add("name", text, "name_mention", "Mentioned")

        return list(found.values())

    def _read_images(self, post: Post) -> list[str]:
        if not (self.image_reader and self.fetch_image):
            return []
        texts = []
        for m in post.media[: self.max_images]:
            if not m.image_url:
                continue
            data = self.fetch_image(m.image_url)
            if not data:
                continue
            try:
                text = self.image_reader.read(data)
            except Exception as exc:  # a bad image shouldn't stop the scan
                log.debug("OCR failed on %s: %s", post.code, exc)
                continue
            if text:
                texts.append(text)
        return texts


def _load_nlp():
    try:
        import spacy
        return spacy.load("en_core_web_sm", disable=["lemmatizer"])
    except (ImportError, OSError):
        log.warning("Name detection disabled: run  python -m spacy download en_core_web_sm")
        return None
