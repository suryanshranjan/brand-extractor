"""Official Instagram Graph API source (Business Discovery).

Uses your own Instagram professional account + a Meta app token to read *other*
business/creator accounts: profile, captions and images. No scraping, no ban risk.

Limitations compared with the web source:
  * only business/creator accounts can be read (personal accounts return "not found");
  * photo tags, collab co-authors and paid-partnership labels are not exposed;
  * business vs creator can't be told apart, so every account found is treated as a business.
"""
from __future__ import annotations

import logging
import re
import time
from typing import Iterator

from curl_cffi import requests as http
from curl_cffi.requests.exceptions import RequestException

from .instagram import InstagramError, LoginRequired, NotFound, RateLimited
from .models import Media, Post, Profile

log = logging.getLogger(__name__)

MEDIA_FIELDS = "id,caption,media_type,media_url,thumbnail_url,permalink,timestamp,children{media_type,media_url,thumbnail_url}"
PROFILE_FIELDS = "id,username,name,followers_count,media_count,website"
RATE_LIMIT_CODES = {4, 17, 32, 613, 80002}
NOT_FOUND_SUBCODES = {2207013}
PERMALINK_CODE_RE = re.compile(r"instagram\.com/(?:[\w.]+/)?(?:p|reel|tv)/([\w-]+)")


class GraphClient:
    source_name = "graph"
    logged_in = True   # tokens always paginate fully

    def __init__(self, token: str, ig_user_id: str, version: str = "v21.0", page_size: int = 25):
        if not token or not ig_user_id:
            raise InstagramError("Graph source needs IG_GRAPH_TOKEN and IG_GRAPH_USER_ID")
        self.token = token
        self.ig_user_id = ig_user_id
        self.base = f"https://graph.facebook.com/{version}"
        self.page_size = page_size
        self.session = http.Session(timeout=30)

    def _discover(self, username: str, media: str = "") -> dict:
        fields = f"business_discovery.username({username}){{{PROFILE_FIELDS}{media}}}"
        for attempt in range(3):
            try:
                resp = self.session.get(f"{self.base}/{self.ig_user_id}",
                                        params={"fields": fields, "access_token": self.token})
            except RequestException as exc:
                if attempt < 2:
                    time.sleep(5)
                    continue
                raise InstagramError(f"Network error: {exc}") from exc
            data = resp.json() if resp.content else {}
            if resp.status_code == 200 and "business_discovery" in data:
                return data["business_discovery"]
            err = data.get("error") or {}
            code, subcode = err.get("code"), err.get("error_subcode")
            if code == 190:
                raise LoginRequired("Graph API token is invalid or expired")
            if code in RATE_LIMIT_CODES:
                if attempt < 2:
                    log.warning("Graph API rate limit, waiting 60s")
                    time.sleep(60)
                    continue
                raise RateLimited(err.get("message", "Graph API rate limit"))
            if subcode in NOT_FOUND_SUBCODES or code == 110:
                raise NotFound(username)
            raise InstagramError(err.get("message") or f"HTTP {resp.status_code}")
        raise RateLimited("Retries exhausted")

    def get_profile(self, username: str) -> tuple[Profile, dict]:
        user = self._discover(username)
        return Profile(
            username=user.get("username") or username,
            user_id=str(user.get("id") or ""),
            full_name=user.get("name") or "",
            is_business=True,
            is_professional=True,
            category="",
            followers=int(user.get("followers_count") or 0),
            external_url=user.get("website") or "",
        ), user

    def iter_posts(self, profile: Profile, raw_user: dict) -> Iterator[Post]:
        after = None
        while True:
            paging = f".after({after})" if after else ""
            user = self._discover(profile.username, f",media{paging}.limit({self.page_size}){{{MEDIA_FIELDS}}}")
            media = user.get("media") or {}
            for item in media.get("data") or []:
                yield parse_graph_media(item)
            after = ((media.get("paging") or {}).get("cursors") or {}).get("after")
            if not after or not (media.get("paging") or {}).get("next"):
                return

    def get_bytes(self, url: str) -> bytes | None:
        try:
            resp = self.session.get(url)
        except RequestException:
            return None
        return resp.content if resp.status_code == 200 else None


def parse_graph_media(item: dict) -> Post:
    def image_of(m: dict) -> str | None:
        return m.get("thumbnail_url") if m.get("media_type") == "VIDEO" else m.get("media_url")

    children = (item.get("children") or {}).get("data") or [item]
    match = PERMALINK_CODE_RE.search(item.get("permalink") or "")
    timestamp = None
    if item.get("timestamp"):
        try:
            timestamp = int(time.mktime(time.strptime(item["timestamp"][:19], "%Y-%m-%dT%H:%M:%S")))
        except ValueError:
            pass
    return Post(
        code=match.group(1) if match else str(item.get("id")),
        caption=item.get("caption") or "",
        timestamp=timestamp,
        media=[Media(image_url=image_of(c)) for c in children],
    )
