"""Decides whether a looked-up Instagram account is a brand, a creator, an organiser, or a person."""
from __future__ import annotations

from .models import Profile

BRAND = "brand"
CREATOR = "creator"            # artists, DJs, influencers, public figures
ORGANIZATION = "organization"  # colleges, clubs, fests, non-profits
PERSONAL = "personal"          # ordinary personal accounts
NOT_FOUND = "not found"        # handle doesn't exist (often an OCR misread)
UNVERIFIED = "unverified"      # not looked up, or a plain name with no handle

CREATOR_CATEGORIES = {
    "musician/band", "musician", "artist", "public figure", "personal blog", "digital creator",
    "video creator", "gaming video creator", "athlete", "actor", "blogger", "photographer",
    "dj", "comedian", "writer", "author", "gamer", "entrepreneur", "producer", "singer",
    "dancer", "model", "fitness model", "fitness trainer", "personal trainer", "coach",
    "creator", "editor", "designer", "chef", "journalist", "rapper", "band", "just for fun",
    "news personality", "motivational speaker", "makeup artist", "visual arts", "film director",
}
ORGANIZATION_WORDS = (
    "university", "college", "school", "student", "club", "community",
    "non-profit", "nonprofit", "religious", "government", "festival",
)
ORGANIZATION_CATEGORIES = {"education", "event", "organization", "society & culture website"}
CREATOR_BUSINESS_CATEGORIES = {"creators & celebrities"}
ORGANIZATION_BUSINESS_CATEGORIES = {"non-profits & religious organizations", "local events"}


def classify(profile: Profile | None) -> str:
    if profile is None:
        return UNVERIFIED
    category = profile.category.strip().lower()
    business_category = profile.business_category.strip().lower()

    if business_category in CREATOR_BUSINESS_CATEGORIES or category in CREATOR_CATEGORIES:
        return CREATOR
    if (business_category in ORGANIZATION_BUSINESS_CATEGORIES or category in ORGANIZATION_CATEGORIES
            or any(w in category for w in ORGANIZATION_WORDS)):
        return ORGANIZATION
    if profile.is_business:
        return BRAND
    if profile.is_professional:   # Instagram "creator" account type
        return CREATOR
    return PERSONAL
