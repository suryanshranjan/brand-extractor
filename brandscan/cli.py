"""Command line.

    brandscan scan TARGET [-n 50] [--login USER] ...   scan an account
    brandscan login USER                               log in once (opens a browser)
    brandscan import-cookies FILE --as USER            use cookies exported from a browser
    brandscan serve [--host 0.0.0.0] [--port 8000]     run the web dashboard + API

`brandscan TARGET ...` (no subcommand) is the same as `brandscan scan TARGET ...`,
and running with no arguments at all asks questions interactively.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from . import __version__, auth
from .config import Settings
from .pipeline import ScanError, ScanOptions, make_client, normalize_target, run_scan

log = logging.getLogger("brandscan")
COMMANDS = {"scan", "login", "import-cookies", "serve"}


def setup_logging(settings: Settings, verbose: bool = False) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.DEBUG if verbose else logging.INFO)
    console.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
    logfile = logging.FileHandler(settings.log_file, encoding="utf-8")
    logfile.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.handlers[:] = [console, logfile]
    for noisy in ("urllib3", "PIL", "easyocr", "httpx", "httpcore", "multipart"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="brandscan",
                                description="Find the brands that sponsor or partner with an Instagram account.")
    p.add_argument("--version", action="version", version=f"brandscan {__version__}")
    sub = p.add_subparsers(dest="command")

    s = sub.add_parser("scan", help="scan an account")
    s.add_argument("target", nargs="?", help="Instagram handle or profile URL (asked for if omitted)")
    s.add_argument("-n", "--limit", type=int, default=None, help="recent posts to scan (default 50)")
    s.add_argument("--login", metavar="USER", help="Instagram account to scan with (web source)")
    s.add_argument("--relogin", action="store_true", help="discard the saved session and log in again")
    ocr = s.add_mutually_exclusive_group()
    ocr.add_argument("--ocr", dest="ocr", action="store_true", default=None, help="read text in images")
    ocr.add_argument("--no-ocr", dest="ocr", action="store_false")
    s.add_argument("--gpu", action="store_true", help="run EasyOCR on the GPU")
    s.add_argument("--no-ner", action="store_true", help="skip NLP name detection in captions")
    s.add_argument("--max-lookups", type=int, help="max accounts to look up (default from settings, 80)")
    s.add_argument("--source", choices=["web", "graph"], help="data source (default from settings: web)")
    s.add_argument("--proxy", help="proxy URL, e.g. http://user:pass@host:port")
    s.add_argument("--fresh", action="store_true", help="ignore saved progress and rescan")
    s.add_argument("-v", "--verbose", action="store_true")

    l = sub.add_parser("login", help="log in to Instagram in a browser and save the session")
    l.add_argument("username")

    i = sub.add_parser("import-cookies", help="save a session from a browser cookie export (JSON)")
    i.add_argument("file", type=Path)
    i.add_argument("--as", dest="username", required=True, help="Instagram username the cookies belong to")

    v = sub.add_parser("serve", help="run the web dashboard and API")
    v.add_argument("--host", default="127.0.0.1")
    v.add_argument("--port", type=int, default=8000)
    return p


def prompt_missing(args: argparse.Namespace) -> None:
    """The interactive flow used when run without arguments."""
    if not args.target:
        args.target = input("Target Instagram handle: ").strip()
        if args.limit is None:
            args.limit = int(input("Posts to scan [50]: ").strip() or 50)
        if args.ocr is None:
            args.ocr = input("Read text in images (OCR, slower)? [Y/n]: ").strip().lower() != "n"
        if not args.login:
            args.login = input("Your Instagram username (Enter to skip; needed for >12 posts): ").strip() or None


def cmd_scan(args: argparse.Namespace, settings: Settings) -> int:
    prompt_missing(args)
    if args.source:
        settings.source = args.source
    if args.proxy:
        settings.proxy = args.proxy
    cookies = None
    if settings.source == "web":
        cookies = auth.session_from_settings(settings, args.login or "", interactive=True, relogin=args.relogin)

    opts = ScanOptions(target=normalize_target(args.target or ""), limit=args.limit or 50,
                       ocr=True if args.ocr is None else args.ocr, ner=not args.no_ner,
                       fresh=args.fresh, max_lookups=args.max_lookups, gpu=args.gpu)
    try:
        result = run_scan(settings, opts, make_client(settings, cookies))
    except ScanError as exc:
        log.error("%s", exc)
        return 1

    print("\n" + "-" * 64)
    print(f"TOP BRANDS FOR @{result.target['username']}  "
          f"({len(result.brands)} brands, {len(result.entities)} entities, {result.posts_scanned} posts)")
    print("-" * 64)
    for i, e in enumerate(result.brands[:15], 1):
        label = "@" + e.handle if e.handle else e.display
        print(f"{i:>2}. {label:<30} {e.type:<11} score {e.score:>5.0f}  {len(e.posts)} posts  {e.primary_role}")
    print(f"\n{result.message}\nReports: {Path(result.files['brands_csv']).parent}")
    return 0 if result.completed else 1


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or (argv[0] not in COMMANDS and argv[0] not in ("-h", "--help", "--version")):
        argv.insert(0, "scan")
    args = build_parser().parse_args(argv)
    settings = Settings.from_env()
    setup_logging(settings, getattr(args, "verbose", False))

    if args.command == "scan":
        return cmd_scan(args, settings)
    if args.command == "login":
        auth.session_from_settings(settings, args.username, interactive=True, relogin=True)
        print(f"Session for @{args.username} saved in {settings.sessions_dir}")
        return 0
    if args.command == "import-cookies":
        auth.import_cookies(settings.sessions_dir, args.username, args.file)
        print(f"Session for @{args.username} saved. Scan with --login {args.username} (or set IG_LOGIN).")
        return 0
    if args.command == "serve":
        from .server import serve
        serve(settings, args.host, args.port)
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
