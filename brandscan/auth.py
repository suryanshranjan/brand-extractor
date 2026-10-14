"""Instagram login sessions for the web source.

Three ways to get one:
  * `brandscan login USER`: a browser window opens and you log in yourself (2FA works);
  * `brandscan import-cookies FILE --as USER`: a cookie export (e.g. the Cookie-Editor
    extension's JSON) from a browser that is already logged in; this is the option for servers;
  * the IG_SESSIONID setting: a raw sessionid cookie value.
The dashboard can do the first and a sessionid paste too, and marks the account it connected as the
default (sessions/active_login.txt), used when neither IG_LOGIN nor --login names one.
The script never sees your password. Sessions live in <data dir>/sessions/<user>.json.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from urllib.parse import unquote

from . import config

log = logging.getLogger(__name__)

ACTIVE_FILE = "active_login.txt"
KEEP_COOKIES = {"sessionid", "csrftoken", "ds_user_id", "mid", "ig_did", "rur", "datr"}


def _files(sessions_dir: Path, username: str) -> list[Path]:
    # <user>_cookies.json is the Playwright cookie list written by older versions.
    return [sessions_dir / f"{username}.json", sessions_dir / f"{username}_cookies.json"]


def parse_cookie_data(data) -> dict[str, str]:
    """Accept {name: value} or a list of cookie objects (Playwright / browser-extension export)."""
    if isinstance(data, list):
        data = {c["name"]: c["value"] for c in data
                if "instagram.com" in c.get("domain", "instagram.com") and "name" in c}
    return {k: unquote(str(v)) for k, v in (data or {}).items() if k in KEEP_COOKIES}


def load_cookies(sessions_dir: Path, username: str) -> dict[str, str] | None:
    for path in _files(sessions_dir, username):
        if not path.exists():
            continue
        try:
            cookies = parse_cookie_data(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError, KeyError, TypeError):
            continue
        if cookies.get("sessionid"):
            return cookies
    return None


def save_cookies(sessions_dir: Path, username: str, cookies: dict[str, str]) -> None:
    sessions_dir.mkdir(parents=True, exist_ok=True)
    (sessions_dir / f"{username}.json").write_text(json.dumps(cookies), encoding="utf-8")


def delete_session(sessions_dir: Path, username: str) -> None:
    for path in _files(sessions_dir, username):
        path.unlink(missing_ok=True)


def active_login(sessions_dir: Path) -> str:
    path = sessions_dir / ACTIVE_FILE
    return path.read_text(encoding="utf-8").strip() if path.is_file() else ""


def set_active_login(sessions_dir: Path, username: str | None) -> None:
    path = sessions_dir / ACTIVE_FILE
    if username:
        sessions_dir.mkdir(parents=True, exist_ok=True)
        path.write_text(username, encoding="utf-8")
    else:
        path.unlink(missing_ok=True)


def import_cookies(sessions_dir: Path, username: str, export_file: Path) -> None:
    cookies = parse_cookie_data(json.loads(Path(export_file).read_text(encoding="utf-8")))
    if not cookies.get("sessionid"):
        raise SystemExit("That file has no Instagram 'sessionid' cookie. Export cookies while logged in to instagram.com.")
    save_cookies(sessions_dir, username, cookies)


def browser_login(username: str, proxy: str = "") -> dict[str, str]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise SystemExit("Logging in needs Playwright:\n  pip install playwright\n  playwright install chromium\n"
                         "Or use `import-cookies` / the IG_SESSIONID setting instead.")

    print(f"[*] A browser window will open. Log in to Instagram as @{username} there "
          f"(2FA is fine). Waiting up to {config.LOGIN_TIMEOUT // 60} minutes...")
    with sync_playwright() as pw:
        # Same proxy as the scans, so the session never changes IP.
        browser = pw.chromium.launch(headless=False, proxy={"server": proxy} if proxy else None)
        context = browser.new_context()
        page = context.new_page()
        page.goto(config.IG_BASE + "/accounts/login/")
        deadline = time.monotonic() + config.LOGIN_TIMEOUT
        cookies: dict[str, str] = {}
        while time.monotonic() < deadline:
            try:
                cookies = {c["name"]: c["value"] for c in context.cookies(config.IG_BASE)}
            except Exception:  # window closed by the user
                break
            if cookies.get("sessionid"):
                break
            time.sleep(2)
        browser.close()

    if not cookies.get("sessionid"):
        raise SystemExit("[!] Login was not completed.")
    return parse_cookie_data(cookies)


def session_from_settings(settings: config.Settings, login: str = "",
                          interactive: bool = False, relogin: bool = False) -> dict[str, str] | None:
    """Cookies for the web source, or None to run without logging in."""
    if settings.ig_sessionid:
        sessionid = unquote(settings.ig_sessionid)
        cookies = {"sessionid": sessionid}
        if ":" in sessionid:
            cookies["ds_user_id"] = sessionid.split(":", 1)[0]
        return cookies

    login = login or settings.ig_login or active_login(settings.sessions_dir)
    if not login:
        return None
    if relogin:
        delete_session(settings.sessions_dir, login)
    cookies = load_cookies(settings.sessions_dir, login)
    if cookies:
        log.info("Using saved session for @%s", login)
        return cookies
    if not interactive:
        raise SystemExit(f"No saved session for @{login}. Run `brandscan login {login}` "
                         f"or `brandscan import-cookies FILE --as {login}` first.")
    cookies = browser_login(login, settings.proxy)
    save_cookies(settings.sessions_dir, login, cookies)
    print("[*] Logged in. Session saved for next time.")
    return cookies
