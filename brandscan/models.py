"""Data types shared across the pipeline."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Media:
    """One image or video inside a post (a carousel has several)."""
    image_url: str | None = None   # full image, or the cover frame for a video
    alt_text: str = ""
    tagged: list[str] = field(default_factory=list)


@dataclass
class Post:
    """A post normalised from either of Instagram's API shapes."""
    code: str
    caption: str = ""
    timestamp: int | None = None
    media: list[Media] = field(default_factory=list)
    tagged: list[str] = field(default_factory=list)      # photo tags across all media
    coauthors: list[str] = field(default_factory=list)   # collab post co-authors
    sponsors: list[str] = field(default_factory=list)    # "Paid partnership with" handles
    is_paid_partnership: bool = False

    @property
    def url(self) -> str:
        return f"https://www.instagram.com/p/{self.code}/"


@dataclass
class Signal:
    """One piece of evidence that an entity is associated with a post."""
    key: str          # handle (lowercase, no @) or normalised name
    display: str      # how to show it
    kind: str         # "handle" or "name"
    source: str       # key into config.WEIGHTS
    role: str
    weight: float
    post_code: str
    evidence: str = ""


@dataclass
class Profile:
    username: str
    user_id: str = ""
    full_name: str = ""
    is_private: bool = False
    is_verified: bool = False
    is_business: bool = False
    is_professional: bool = False
    category: str = ""
    business_category: str = ""
    followers: int = 0
    external_url: str = ""
    followed_by_viewer: bool = False
