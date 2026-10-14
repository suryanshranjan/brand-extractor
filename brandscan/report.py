"""Aggregates signals into ranked entities and writes the CSV reports."""
from __future__ import annotations

import csv
import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from . import classify as C
from .lexicon import known_brand_keys
from .models import Profile, Signal

SOURCE_LABELS = {
    "paid_partnership": "Paid partnership",
    "collab_author": "Collab post",
    "role_phrase": "Sponsor phrase",
    "sponsor_board": "Sponsor poster",
    "domain_mention": "Website in caption",
    "caption_mention": "Caption @mention",
    "photo_tag": "Photo tag",
    "image_mention": "@mention in image",
    "name_mention": "Name in caption",
}
# Roles from explicit wording outrank generic ones when picking the primary role.
GENERIC_ROLES = {"Mentioned", "Tagged", "Sponsored Mention", "Mentioned in Image", "Website Mentioned"}
# Roles that alone don't prove sponsorship for an unverified name (OCR can misread posters).
WEAK_ROLES = GENERIC_ROLES | {"Listed Sponsor"}


@dataclass
class Entity:
    key: str
    display: str
    kind: str                       # "handle" or "name"
    score: float = 0.0
    roles: Counter = field(default_factory=Counter)
    sources: Counter = field(default_factory=Counter)
    posts: dict[str, float] = field(default_factory=dict)   # post code -> points from it
    aliases: set[str] = field(default_factory=set)
    profile: Profile | None = None
    type: str = C.UNVERIFIED
    known: bool = False            # in the known-brands list

    def add(self, s: Signal) -> None:
        self.score += s.weight
        self.roles[s.role] += 1
        self.sources[s.source] += 1
        self.posts[s.post_code] = self.posts.get(s.post_code, 0) + s.weight

    def absorb(self, other: "Entity") -> None:
        self.score += other.score
        self.roles.update(other.roles)
        self.sources.update(other.sources)
        for code, pts in other.posts.items():
            self.posts[code] = self.posts.get(code, 0) + pts
        self.aliases.add(other.display)

    @property
    def handle(self) -> str:
        return self.key if self.kind == "handle" else ""

    @property
    def primary_role(self) -> str:
        specific = [(n, r) for r, n in self.roles.items() if r not in GENERIC_ROLES]
        pool = specific or [(n, r) for r, n in self.roles.items()]
        return max(pool)[1] if pool else ""

    @property
    def has_explicit_role(self) -> bool:
        return any(r not in WEAK_ROLES for r in self.roles)


def aggregate(signals: list[Signal], known_brands_file: str | None = None) -> list[Entity]:
    entities: dict[tuple[str, str], Entity] = {}
    for s in signals:
        ent = entities.get((s.kind, s.key))
        if ent is None:
            ent = entities[(s.kind, s.key)] = Entity(s.key, s.display, s.kind)
        ent.add(s)

    # Fold plain names ("Red Bull") into a matching handle (@redbullindia) when exactly one fits.
    handles = [e for e in entities.values() if e.kind == "handle"]
    for name_ent in [e for e in entities.values() if e.kind == "name"]:
        if len(name_ent.key) < 4:
            continue
        matches = [h for h in handles
                   if h.key.replace("_", "").replace(".", "").startswith(name_ent.key)]
        if len(matches) == 1:
            matches[0].absorb(name_ent)
            del entities[("name", name_ent.key)]

    known = known_brand_keys(known_brands_file)
    for e in entities.values():
        e.known = e.key in known or e.key.replace("_", "").replace(".", "") in known
    return sorted(entities.values(), key=lambda e: e.score, reverse=True)


def is_brand_candidate(e: Entity, posts_scanned: int = 0) -> bool:
    if e.type == C.BRAND:
        return True
    if e.type != C.UNVERIFIED:
        return False
    if e.known:
        return True
    # Text on most posts is the account's own branding / watermark, not a sponsor.
    if posts_scanned >= 4 and len(e.posts) > 0.6 * posts_scanned:
        return False
    # Unverified: keep only with explicit sponsor wording or repeat appearances.
    return e.has_explicit_role or len(e.posts) >= 2


COLUMNS = ["Rank", "Name", "Handle", "Type", "Score", "Posts", "Primary Role", "All Roles",
           "Evidence", "Category", "Followers", "Verified", "Known Brand", "Profile URL", "Top Posts",
           "Also Seen As"]


def _row(rank: int, e: Entity) -> list:
    p = e.profile
    top_posts = sorted(e.posts, key=e.posts.get, reverse=True)[:3]
    return [
        rank,
        (p.full_name if p and p.full_name else e.display.lstrip("@")),
        "@" + e.handle if e.handle else "",
        e.type,
        round(e.score),
        len(e.posts),
        e.primary_role,
        "; ".join(f"{r} ({n})" for r, n in e.roles.most_common()),
        "; ".join(f"{SOURCE_LABELS.get(s, s)} ({n})" for s, n in e.sources.most_common()),
        (p.category or p.business_category) if p else "",
        p.followers if p else "",
        ("yes" if p.is_verified else "no") if p else "",
        "yes" if e.known else "",
        f"https://www.instagram.com/{e.handle}/" if e.handle else "",
        " ".join(f"https://www.instagram.com/p/{c}/" for c in top_posts),
        "; ".join(sorted(e.aliases)),
    ]


def write_csv(path: Path, entities: list[Entity]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # utf-8-sig so Excel shows non-English names correctly.
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(COLUMNS)
        for i, e in enumerate(entities, 1):
            writer.writerow(_row(i, e))


def to_dict(e: Entity) -> dict:
    row = dict(zip(COLUMNS, _row(0, e)))
    row.pop("Rank")
    return {
        "name": row["Name"], "handle": e.handle, "type": e.type, "score": round(e.score),
        "posts": sorted(e.posts, key=e.posts.get, reverse=True), "primary_role": e.primary_role,
        "roles": dict(e.roles), "sources": dict(e.sources), "known_brand": e.known,
        "category": row["Category"], "followers": e.profile.followers if e.profile else None,
        "verified": e.profile.is_verified if e.profile else None,
        "profile_url": row["Profile URL"], "aliases": sorted(e.aliases),
    }


def write_json(path: Path, target: dict, brands: list[Entity], entities: list[Entity], meta: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"target": target, **meta,
            "brands": [to_dict(e) for e in brands],
            "entities": [to_dict(e) for e in entities]}
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
