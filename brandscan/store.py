"""On-disk state: per-target checkpoints and the shared profile lookup cache."""
from __future__ import annotations

import json
import os
import time
from dataclasses import asdict
from pathlib import Path

from . import config
from .models import Profile, Signal


def _write_json(path: Path, data) -> None:
    """Write atomically so a crash mid-save never corrupts the file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


class Checkpoint:
    """Remembers which posts of one target were already scanned, and what they yielded."""

    def __init__(self, output_dir: Path, target: str, fresh: bool = False):
        self.path = output_dir / target / "checkpoint.json"
        self.done: set[str] = set()
        self.signals: list[Signal] = []
        if not fresh and self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                self.done = set(data.get("done", []))
                self.signals = [Signal(**s) for s in data.get("signals", [])]
            except (OSError, ValueError, TypeError):
                pass

    def add(self, post_code: str, signals: list[Signal]) -> None:
        self.done.add(post_code)
        self.signals.extend(signals)

    def save(self) -> None:
        _write_json(self.path, {"done": sorted(self.done), "signals": [asdict(s) for s in self.signals]})


class ProfileCache:
    """Looked-up profiles, shared across targets. A None value means the handle doesn't exist."""

    def __init__(self, path: Path, ttl: float = config.PROFILE_CACHE_TTL):
        self.path = path
        self.ttl = ttl
        self.entries: dict[str, dict] = {}
        if path.exists():
            try:
                self.entries = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pass

    def get(self, username: str) -> tuple[bool, Profile | None]:
        """Returns (hit, profile)."""
        entry = self.entries.get(username)
        if not entry or time.time() - entry.get("fetched_at", 0) > self.ttl:
            return False, None
        return True, Profile(**entry["profile"]) if entry.get("profile") else None

    def put(self, username: str, profile: Profile | None) -> None:
        self.entries[username] = {"fetched_at": time.time(), "profile": asdict(profile) if profile else None}

    def save(self) -> None:
        _write_json(self.path, self.entries)
