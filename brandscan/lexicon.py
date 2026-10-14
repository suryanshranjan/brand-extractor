"""Word lists: generic words that are never brands, and the known-brands dictionary."""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

BUILTIN_KNOWN_BRANDS = Path(__file__).parent / "data" / "known_brands.txt"

# Never brands on their own (event / poster vocabulary, places, pronouns).
GENERIC_NAMES = {
    "us", "you", "we", "our", "the", "team", "teams", "everyone", "all", "them", "more",
    "many more", "and more", "partner", "partners", "sponsor", "sponsors", "brand", "brands",
    "company", "companies", "instagram", "link", "bio", "link in bio", "the team", "our team",
    "our partners", "our sponsors", "fest", "festival", "event", "events", "college",
    "university", "students", "student", "guest", "guests", "speaker", "speakers", "judge",
    "judges", "winner", "winners", "congratulations", "prize", "prizes", "prize pool",
    "hackathon", "round", "finals", "final", "registration", "register", "register now",
    "registrations open", "coming soon", "stay tuned", "save the date", "venue", "date",
    "time", "day", "night", "live", "tickets", "entry", "free entry", "contact", "query",
    "queries", "follow", "follow us", "like", "share", "comment", "swipe", "swipe left",
    "certificate", "certificates", "goodies", "swags", "swag", "workshop", "seminar",
    "competition", "competitions", "india", "delhi", "new delhi", "mumbai", "bangalore",
    "bengaluru", "hyderabad", "pune", "chennai", "kolkata", "noida", "gurugram", "gurgaon",
    "community", "club", "society", "council", "committee", "organised by", "organized by",
    "presents", "powered by", "in association with", "thank you", "thanks", "special thanks",
    "edm", "pronite", "concert", "performances", "performance", "lineup", "line up",
    "glimpses", "recap", "highlights", "announcement", "announcing", "introducing",
    "welcome", "join us", "see you", "see you there", "don't miss", "dont miss",
    "title", "gold", "silver", "platinum", "bronze", "diamond", "associate", "official",
}

# Words that, in a full name, describe the organiser rather than identify it.
GENERIC_NAME_TOKENS = {
    "official", "university", "college", "institute", "technology", "technological",
    "festival", "society", "council", "union", "students", "student", "india", "delhi",
    "school", "club", "committee", "events", "cultural", "annual", "chapter", "community",
}

# Link / mail / social domains that are never sponsors.
IGNORED_DOMAINS = {
    "instagram", "facebook", "fb", "twitter", "x", "linkedin", "youtube", "youtu", "whatsapp",
    "wa", "t", "telegram", "linktr", "linktree", "bit", "tinyurl", "cutt", "rb", "forms",
    "docs", "drive", "gmail", "yahoo", "outlook", "hotmail", "google", "goo", "canva",
    "lu", "msha", "beacons", "taplink", "bio", "www",
}


def name_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _read_list(path: Path) -> set[str]:
    if not path.is_file():
        return set()
    keys = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            keys.add(name_key(line))
    return keys - {""}


@lru_cache(maxsize=8)
def known_brand_keys(extra_file: str | None = None) -> frozenset[str]:
    keys = _read_list(BUILTIN_KNOWN_BRANDS)
    if extra_file:
        keys |= _read_list(Path(extra_file))
    return frozenset(keys)
