"""Instagram web API client and response parsers.

Instagram returns posts in two shapes:
  * the legacy GraphQL "edge" shape (web_profile_info, first ~12 posts, works
    without login when Instagram allows it), and
  * the v1 feed "item" shape (/api/v1/feed/user/, paginated, needs login).
  * the Relay "Polaris" shape embedded in the profile web page, the logged-out
    fallback when Instagram refuses the JSON API (latest ~12 posts, no tags).
Both are normalised into models.Post so nothing downstream cares which one was used.
"""
from __future__ import annotations

import json
import logging
import re
import random
import time
from collections import deque
from typing import Iterator

from curl_cffi import requests as http
from curl_cffi.requests.exceptions import RequestException

from . import config
from .models import Media, Post, Profile

log = logging.getLogger(__name__)


class InstagramError(Exception):
    pass


class LoginRequired(InstagramError):
    pass


class RateLimited(InstagramError):
    pass


class NotFound(InstagramError):
    pass


class InstagramClient:
    source_name = "web"

    def __init__(self, cookies: dict[str, str] | None = None, proxy: str | None = None,
                 delay: tuple[float, float] = config.REQUEST_DELAY, hourly_cap: int = 0):
        self.session = http.Session(impersonate="chrome", proxy=proxy or None, timeout=30)
        self.cookies = dict(cookies or {})
        for name, value in self.cookies.items():
            self.session.cookies.set(name, value, domain=".instagram.com")
        self.logged_in = bool(self.cookies.get("sessionid"))
        self.delay = delay
        self.hourly_cap = hourly_cap
        self._last_call = 0.0
        self._calls: deque[float] = deque()

    def _headers(self, referer: str) -> dict[str, str]:
        headers = {
            "X-IG-App-ID": config.IG_APP_ID,
            "X-Requested-With": "XMLHttpRequest",
            "Referer": referer,
        }
        if self.cookies.get("csrftoken"):
            headers["X-CSRFToken"] = self.cookies["csrftoken"]
        return headers

    def _throttle(self) -> None:
        wait = self._last_call + random.uniform(*self.delay) - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        if self.hourly_cap:
            while self._calls and time.monotonic() - self._calls[0] > 3600:
                self._calls.popleft()
            if len(self._calls) >= self.hourly_cap:
                pause = 3600 - (time.monotonic() - self._calls[0]) + 1
                log.warning("Hourly request cap (%d) reached, pausing %d min", self.hourly_cap, pause // 60 + 1)
                time.sleep(pause)
                self._calls.popleft()
            self._calls.append(time.monotonic())
        self._last_call = time.monotonic()

    def get_json(self, path: str, params: dict | None = None, referer: str = config.IG_BASE + "/") -> dict:
        backoffs = list(config.RATE_LIMIT_BACKOFF) if self.logged_in else []
        for attempt in range(len(backoffs) + 1):
            self._throttle()
            try:
                resp = self.session.get(config.IG_BASE + path, params=params,
                                        headers=self._headers(referer), allow_redirects=False)
            except RequestException as exc:
                if attempt < len(backoffs):
                    log.warning("Network error (%s), retrying in 10s", exc)
                    time.sleep(10)
                    continue
                raise InstagramError(f"Network error: {exc}") from exc

            if resp.status_code == 200:
                try:
                    return resp.json()
                except ValueError:
                    # An HTML page instead of JSON means we were bounced to login.
                    raise LoginRequired("Instagram returned a web page instead of data")
            if resp.status_code == 404:
                raise NotFound(path)
            if resp.status_code in (301, 302, 303, 307, 308):
                raise LoginRequired("Instagram redirected to the login page")

            try:
                body = resp.json()
            except ValueError:
                body = {}
            message = str(body.get("message", ""))
            rate_limited = resp.status_code == 429 or "wait a few minutes" in message.lower()

            if not self.logged_in and (body.get("require_login") or resp.status_code in (401, 403)):
                raise LoginRequired(message or f"HTTP {resp.status_code}")
            if rate_limited:
                if attempt < len(backoffs):
                    log.warning("Rate limited by Instagram, waiting %ss before retrying", backoffs[attempt])
                    time.sleep(backoffs[attempt])
                    continue
                raise RateLimited(message or "HTTP 429")
            if resp.status_code in (401, 403) or message == "login_required":
                raise LoginRequired(message or f"HTTP {resp.status_code}")
            raise InstagramError(f"HTTP {resp.status_code}: {message or resp.text[:200]}")
        raise RateLimited("Retries exhausted")

    def get_bytes(self, url: str) -> bytes | None:
        """Download a CDN image (no API throttle needed)."""
        try:
            resp = self.session.get(url)
        except RequestException as exc:
            log.debug("Image download failed: %s", exc)
            return None
        return resp.content if resp.status_code == 200 else None

    def get_profile(self, username: str) -> tuple[Profile, dict]:
        """Return the parsed profile and the raw user object (which holds the first posts)."""
        data = self.get_json("/api/v1/users/web_profile_info/", {"username": username},
                             referer=f"{config.IG_BASE}/{username}/")
        user = (data.get("data") or {}).get("user")
        if not user:
            raise NotFound(username)
        return parse_profile(user), user

    def get_profile_page(self, username: str) -> tuple[Profile, dict]:
        """Logged-out fallback: read the profile and its latest posts from the profile web page."""
        self._throttle()
        try:
            resp = self.session.get(f"{config.IG_BASE}/{username}/", allow_redirects=False)
        except RequestException as exc:
            raise InstagramError(f"Network error: {exc}") from exc
        if resp.status_code in (301, 302, 303, 307, 308):
            raise LoginRequired("Instagram redirected the profile page to login")
        if resp.status_code == 404:
            raise NotFound(username)
        if resp.status_code != 200:
            raise InstagramError(f"HTTP {resp.status_code} loading the profile page")
        user = parse_profile_page(resp.text)
        if not user:
            raise NotFound(username)
        return parse_profile(user), user

    def iter_posts(self, profile: Profile, raw_user: dict) -> Iterator[Post]:
        """Yield the profile's posts, newest first, fetching pages lazily."""
        if profile.is_private and not profile.followed_by_viewer:
            raise InstagramError(private_message(profile.username, self.logged_in))

        if not self.logged_in:
            edges = (raw_user.get("edge_owner_to_timeline_media") or {}).get("edges") or []
            page_edges = (raw_user.get("polaris_ordered_timeline_connection") or {}).get("edges") or []
            log.warning("Not logged in: Instagram only exposes the latest %d posts. "
                        "Use --login to scan further back.", len(edges) or len(page_edges))
            for edge in edges:
                yield parse_graphql_node(edge["node"])
            if not edges:
                for edge in page_edges:
                    yield parse_polaris_node(edge["node"])
            return

        max_id = None
        while True:
            params = {"count": config.PAGE_SIZE}
            if max_id:
                params["max_id"] = max_id
            data = self.get_json(f"/api/v1/feed/user/{profile.user_id}/", params,
                                 referer=f"{config.IG_BASE}/{profile.username}/")
            for item in data.get("items") or []:
                yield parse_feed_item(item)
            max_id = data.get("next_max_id")
            if not data.get("more_available") or not max_id:
                return


def private_message(username: str, logged_in: bool) -> str:
    if logged_in:
        return f"@{username} is a private account, and the connected Instagram account doesn't follow it."
    return f"@{username} is a private account. Connect an Instagram account that follows it to scan it."


# --- Parsers ---------------------------------------------------------------

def _usernames(values) -> list[str]:
    seen: list[str] = []
    for v in values:
        if v and v not in seen:
            seen.append(v)
    return seen


def parse_profile(user: dict) -> Profile:
    links = user.get("bio_links") or []
    return Profile(
        username=user.get("username") or "",
        user_id=str(user.get("id") or user.get("pk") or ""),
        full_name=user.get("full_name") or "",
        is_private=bool(user.get("is_private")),
        is_verified=bool(user.get("is_verified")),
        is_business=bool(user.get("is_business_account")),
        is_professional=bool(user.get("is_professional_account")),
        category=user.get("category_name") or user.get("category") or "",
        business_category=user.get("business_category_name") or "",
        followers=int((user.get("edge_followed_by") or {}).get("count") or user.get("follower_count") or 0),
        external_url=user.get("external_url") or (links[0].get("url") or "" if links else ""),
        followed_by_viewer=bool(user.get("followed_by_viewer")),
    )


def _best_candidate(item: dict) -> str | None:
    candidates = (item.get("image_versions2") or {}).get("candidates") or []
    if not candidates:
        return None
    return max(candidates, key=lambda c: c.get("width") or 0).get("url")


def parse_feed_item(item: dict) -> Post:
    """Parse a v1 feed item (/api/v1/feed/user/)."""
    def tags_of(obj: dict) -> list[str]:
        return [(t.get("user") or {}).get("username") for t in (obj.get("usertags") or {}).get("in") or []]

    children = item.get("carousel_media") or [item]
    media = [Media(image_url=_best_candidate(c),
                   alt_text=c.get("accessibility_caption") or "",
                   tagged=_usernames(tags_of(c)))
             for c in children]
    sponsors = _usernames((t.get("sponsor") or {}).get("username") for t in item.get("sponsor_tags") or [])
    return Post(
        code=item.get("code") or str(item.get("pk")),
        caption=(item.get("caption") or {}).get("text") or "",
        timestamp=item.get("taken_at"),
        media=media,
        tagged=_usernames(tags_of(item) + [u for m in media for u in m.tagged]),
        coauthors=_usernames(u.get("username") for u in item.get("coauthor_producers") or []),
        sponsors=sponsors,
        is_paid_partnership=bool(item.get("is_paid_partnership")) or bool(sponsors),
    )


def parse_graphql_node(node: dict) -> Post:
    """Parse a legacy GraphQL timeline node (web_profile_info)."""
    def tags_of(obj: dict) -> list[str]:
        edges = (obj.get("edge_media_to_tagged_user") or {}).get("edges") or []
        return [((e.get("node") or {}).get("user") or {}).get("username") for e in edges]

    caption_edges = (node.get("edge_media_to_caption") or {}).get("edges") or []
    children = [e["node"] for e in (node.get("edge_sidecar_to_children") or {}).get("edges") or []] or [node]
    media = [Media(image_url=c.get("display_url"),
                   alt_text=c.get("accessibility_caption") or "",
                   tagged=_usernames(tags_of(c)))
             for c in children]
    sponsor_edges = (node.get("edge_media_to_sponsor_user") or {}).get("edges") or []
    sponsors = _usernames(((e.get("node") or {}).get("sponsor") or {}).get("username") for e in sponsor_edges)
    return Post(
        code=node["shortcode"],
        caption=caption_edges[0]["node"].get("text", "") if caption_edges else "",
        timestamp=node.get("taken_at_timestamp"),
        media=media,
        tagged=_usernames(tags_of(node) + [u for m in media for u in m.tagged]),
        coauthors=_usernames(u.get("username") for u in node.get("coauthor_producers") or []),
        sponsors=sponsors,
        is_paid_partnership=bool(node.get("is_paid_partnership")) or bool(sponsors),
    )


_PAGE_JSON = re.compile(r'<script type="application/json"[^>]*>(.*?)</script>', re.S)


def parse_profile_page(html: str) -> dict | None:
    """Pull the profile and its timeline out of the Relay data embedded in a profile page.

    The page carries the account in two `xig_user_by_username` objects (details, and
    the post timeline); they're merged into one user dict for parse_profile.
    """
    found: list[dict] = []

    def walk(obj) -> None:
        if isinstance(obj, dict):
            user = obj.get("xig_user_by_username")
            if isinstance(user, dict):
                found.append(user)
            for value in obj.values():
                walk(value)
        elif isinstance(obj, list):
            for value in obj:
                walk(value)

    for blob in _PAGE_JSON.findall(html):
        if "xig_user_by_username" in blob:
            try:
                walk(json.loads(blob))
            except ValueError:
                continue
    user: dict = {}
    for part in found:
        user.update({k: v for k, v in part.items() if v is not None})
    if not user.get("username"):
        return None
    user["id"] = user.get("pk") or user.get("id")  # "id" on this page is a different (FB) id
    return user


def parse_polaris_node(node: dict) -> Post:
    """Parse a post from the profile page timeline (only the cover image, no tags)."""
    return Post(
        code=node.get("code") or str(node.get("pk")),
        caption=(node.get("caption") or {}).get("text") or "",
        timestamp=node.get("taken_at"),
        media=[Media(image_url=node.get("display_uri"), alt_text=node.get("accessibility_caption") or "")],
    )
