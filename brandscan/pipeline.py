"""One scan, end to end: load the profile, scan posts, look up accounts, write reports.
Shared by the CLI and the web service."""
from __future__ import annotations

import logging
import time
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from typing import Callable

from . import classify as C
from .config import Settings
from .graph import GraphClient
from .instagram import InstagramClient, InstagramError, LoginRequired, NotFound, RateLimited
from .report import Entity, aggregate, is_brand_candidate, write_csv, write_json
from .signals import SignalExtractor
from .store import Checkpoint, ProfileCache

log = logging.getLogger("brandscan")

Progress = Callable[[dict], None]


class ScanError(Exception):
    """A problem worth showing to the user as-is."""


@dataclass
class ScanOptions:
    target: str
    limit: int = 50
    ocr: bool = True
    ner: bool = True
    fresh: bool = False
    max_lookups: int | None = None
    gpu: bool = False


@dataclass
class ScanResult:
    target: dict
    brands: list[Entity]
    entities: list[Entity]
    posts_scanned: int
    completed: bool
    message: str
    files: dict[str, str] = field(default_factory=dict)


def normalize_target(target: str) -> str:
    return target.strip().lstrip("@").rstrip("/").split("/")[-1].split("?")[0].lower()


def make_client(settings: Settings, cookies: dict | None = None):
    if settings.source == "graph":
        try:
            return GraphClient(settings.graph_token, settings.graph_ig_user_id, settings.graph_api_version)
        except InstagramError as exc:
            raise ScanError(str(exc))
    if settings.source != "web":
        raise ScanError(f"Unknown source '{settings.source}' (use 'web' or 'graph').")
    return InstagramClient(cookies, proxy=settings.proxy, hourly_cap=settings.hourly_request_cap)


@lru_cache(maxsize=4)
def get_reader(backend: str, api_key: str, gpu: bool):
    """Load an OCR reader once per process (EasyOCR takes seconds to load)."""
    from .ocr import load_reader
    return load_reader(backend, api_key, gpu)


def run_scan(settings: Settings, opts: ScanOptions, client, progress: Progress | None = None,
             should_stop: Callable[[], bool] = lambda: False) -> ScanResult:
    emit = progress or (lambda event: None)
    target = normalize_target(opts.target)
    if not target:
        raise ScanError("No target account given.")

    try:
        profile, raw_user = client.get_profile(target)
    except NotFound:
        extra = " or isn't a business/creator account" if client.source_name == "graph" else ""
        raise ScanError(f"@{target} doesn't exist{extra}.")
    except LoginRequired as exc:
        if client.source_name == "graph":
            raise ScanError(f"The Graph API token was rejected ({exc}).")
        if client.logged_in:
            raise ScanError(f"The saved Instagram session was rejected ({exc}). Log in again.")
        raise ScanError(f"Instagram requires login from this network ({exc}). Log in with an Instagram account.")
    except InstagramError as exc:
        raise ScanError(f"Couldn't load @{target}: {exc}")
    emit({"stage": "profile", "profile": asdict(profile)})
    log.info("Target: @%s (%s), %s followers", profile.username, profile.full_name or "no name", f"{profile.followers:,}")

    reader = get_reader(settings.ocr_backend, settings.ocrspace_api_key, opts.gpu) if opts.ocr else None
    extractor = SignalExtractor(profile, use_ner=opts.ner, image_reader=reader, fetch_image=client.get_bytes)
    checkpoint = Checkpoint(settings.output_dir, profile.username, fresh=opts.fresh)
    if checkpoint.done:
        log.info("Resuming: %d posts already scanned", len(checkpoint.done))

    completed, message = _scan_posts(client, profile, raw_user, extractor, checkpoint, opts.limit, emit, should_stop)

    entities = aggregate(checkpoint.signals, str(settings.known_brands_file))
    cache = ProfileCache(settings.cache_dir / f"profiles_{client.source_name}.json")
    max_lookups = settings.max_lookups if opts.max_lookups is None else opts.max_lookups
    _lookup_profiles(client, entities, cache, max_lookups, emit, should_stop)

    posts_scanned = len(checkpoint.done)
    brands = [e for e in entities if is_brand_candidate(e, posts_scanned)]
    out_dir = settings.output_dir / profile.username
    files = {
        "brands_csv": str(out_dir / f"{profile.username}_brands.csv"),
        "entities_csv": str(out_dir / f"{profile.username}_all_entities.csv"),
        "json": str(out_dir / f"{profile.username}_report.json"),
    }
    write_csv(out_dir / f"{profile.username}_brands.csv", brands)
    write_csv(out_dir / f"{profile.username}_all_entities.csv", entities)
    write_json(out_dir / f"{profile.username}_report.json", asdict(profile), brands, entities, {
        "source": client.source_name, "posts_scanned": posts_scanned, "completed": completed,
        "message": message, "generated_at": int(time.time()),
    })
    emit({"stage": "done", "brands": len(brands), "entities": len(entities)})
    return ScanResult(asdict(profile), brands, entities, posts_scanned, completed, message, files)


def _scan_posts(client, profile, raw_user, extractor, checkpoint, limit, emit, should_stop) -> tuple[bool, str]:
    new = 0
    try:
        for i, post in enumerate(client.iter_posts(profile, raw_user)):
            if i >= limit:
                break
            if should_stop():
                return False, "Stopped by user. Run again to resume."
            emit({"stage": "posts", "done": i + 1, "total": limit})
            if post.code in checkpoint.done:
                continue
            signals = extractor.extract(post)
            checkpoint.add(post.code, signals)
            new += 1
            log.info("Post %d/%d %s: %d signals", i + 1, limit, post.code, len(signals))
            if new % 5 == 0:
                checkpoint.save()
    except (RateLimited, LoginRequired) as exc:
        log.error("Instagram stopped the scan (%s). Progress is saved.", exc)
        return False, f"Instagram stopped the scan ({exc}). Progress is saved; run again later to resume."
    except InstagramError as exc:
        if not checkpoint.done:
            raise ScanError(str(exc))
        return False, str(exc)
    except KeyboardInterrupt:
        log.warning("Interrupted. Progress is saved; run the same command to resume.")
        return False, "Interrupted. Run again to resume."
    finally:
        checkpoint.save()
    log.info("Scanned %d new posts (%d in total for @%s)", new, len(checkpoint.done), profile.username)
    return True, f"Scanned {len(checkpoint.done)} posts."


def _lookup_profiles(client, entities: list[Entity], cache: ProfileCache, max_lookups: int,
                     emit: Progress, should_stop) -> None:
    """Fetch account details for the top handles so brands can be told apart from people."""
    handles = [e for e in entities if e.kind == "handle"]
    lookups = 0
    stopped = False
    for n, e in enumerate(handles, 1):
        hit, profile = cache.get(e.key)
        if not hit and not stopped and lookups < max_lookups and not should_stop():
            emit({"stage": "lookups", "done": n, "total": len(handles)})
            try:
                profile, _ = client.get_profile(e.key)
            except NotFound:
                profile = None
            except (RateLimited, LoginRequired) as exc:
                log.warning("Stopped account lookups (%s); the rest stay 'unverified'.", exc)
                stopped = True
                continue
            except InstagramError as exc:
                log.debug("Lookup of @%s failed: %s", e.key, exc)
                continue
            cache.put(e.key, profile)
            lookups += 1
            hit = True
            if lookups % 10 == 0:
                cache.save()
                log.info("Looked up %d accounts...", lookups)
        if hit:
            e.profile = profile
            e.type = C.classify(profile) if profile else C.NOT_FOUND
    cache.save()
