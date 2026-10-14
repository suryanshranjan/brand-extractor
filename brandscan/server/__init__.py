"""Web dashboard + REST API.

    GET  /                                 dashboard
    GET  /api/health                       status (no key needed)
    POST /api/scans                        {"target": "engifest_dtu", "limit": 50, "ocr": true}
    GET  /api/scans                        your recent scans
    GET  /api/scans/{id}                   status, progress, and (when finished) the brands
    POST /api/scans/{id}/cancel
    POST /api/scans/{id}/retry             {"fresh": false}: run a finished scan again (resumes unless fresh)
    DELETE /api/scans/{id}                 remove a finished scan from the list
    GET  /api/scans/{id}/files/{name}      brands.csv | all_entities.csv | report.json
    GET  /api/instagram                    which Instagram account scans use
    POST /api/instagram/session            {"username": "...", "sessionid": "..."}   (local mode only)
    POST /api/instagram/browser-login      {"username": "..."}: opens a login window  (local mode only)
    DELETE /api/instagram                  forget the connected account               (local mode only)

Authenticate with an `X-API-Key` header (or `?api_key=` for download links).
Keys come from BRANDSCAN_API_KEYS as comma-separated `name:key` pairs (or bare keys);
each key only sees its own scans.
"""
import hashlib
import hmac
import importlib.util
import json
import logging
import re
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from .. import __version__, auth
from ..config import Settings
from .jobs import ACTIVE, JobStore, Worker

log = logging.getLogger("brandscan.server")
STATIC = Path(__file__).parent / "static"
FILES = {"brands.csv": "brands_csv", "all_entities.csv": "entities_csv", "report.json": "json"}
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
USERNAME = re.compile(r"^[a-z0-9._]{1,30}$")


def parse_keys(raw: list[str]) -> dict[str, str]:
    """{key: owner name}. A bare key gets a stable name derived from its hash."""
    keys = {}
    for item in raw:
        name, _, key = item.partition(":") if ":" in item else ("", "", item)
        keys[key] = name or "key-" + hashlib.sha256(key.encode()).hexdigest()[:8]
    return keys


def create_app(settings: Settings, start_worker: bool = True):
    from fastapi import Depends, FastAPI, HTTPException, Request
    from fastapi.responses import FileResponse
    from pydantic import BaseModel, Field

    keys = parse_keys(settings.api_keys)
    store = JobStore(settings.db_path)
    worker = Worker(store, settings)

    @asynccontextmanager
    async def lifespan(app):
        if start_worker:
            worker.start()
        yield
        worker.stop()

    app = FastAPI(title="Brand Extractor", version=__version__, lifespan=lifespan,
                  docs_url="/api/docs", redoc_url=None)
    app.state.store = store
    app.state.worker = worker

    def owner_of(request: Request) -> str:
        if not keys:
            return "local"
        given = request.headers.get("x-api-key") or request.query_params.get("api_key") or ""
        for key, name in keys.items():
            if hmac.compare_digest(given, key):
                return name
        raise HTTPException(401, "Missing or invalid API key")

    class ScanRequest(BaseModel):
        target: str = Field(min_length=1, max_length=100)
        limit: int = Field(50, ge=1, le=1000)
        ocr: bool = True
        fresh: bool = False

    def public(job: dict) -> dict:
        job = {k: v for k, v in job.items() if k not in ("owner", "files")}
        job["files"] = sorted(FILES) if job["status"] in ("done", "partial") else []
        if job["status"] == "queued":
            job["queue_position"] = store.queue_position(job)
        return job

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/api/health")
    def health():
        return {"status": "ok", "version": __version__, "source": settings.source,
                "auth_required": bool(keys), "ocr": settings.ocr_backend,
                "worker_alive": worker.is_alive()}

    # --- Instagram connection (web source) ---
    # The session is shared by every scan, so only a local, keyless server lets the dashboard change it.
    login_state = {"status": "idle", "username": "", "error": ""}

    class SessionRequest(BaseModel):
        username: str = Field(min_length=1, max_length=60)
        sessionid: str = Field(min_length=10, max_length=1000)

    class LoginRequest(BaseModel):
        username: str = Field(min_length=1, max_length=60)

    def manager(request: Request) -> None:
        owner_of(request)
        if keys:
            raise HTTPException(403, "Connect Instagram on the server with `brandscan login` or `brandscan import-cookies`.")

    def clean_username(raw: str) -> str:
        user = raw.strip().lstrip("@").lower()
        if not USERNAME.match(user):
            raise HTTPException(422, "That doesn't look like an Instagram username.")
        return user

    def use_login(user: str | None) -> None:
        auth.set_active_login(settings.sessions_dir, user)
        settings.ig_login = user or ""

    def connection() -> dict:
        info = {"source": settings.source, "connected": False, "username": "", "method": "",
                "manageable": not keys, "browser_login": not keys and importlib.util.find_spec("playwright") is not None,
                "login": dict(login_state)}
        if settings.source == "graph":
            info.update(connected=bool(settings.graph_token and settings.graph_ig_user_id), method="graph")
        elif settings.ig_sessionid:
            info.update(connected=True, method="env")
        else:
            user = settings.ig_login or auth.active_login(settings.sessions_dir)
            if user and auth.load_cookies(settings.sessions_dir, user):
                info.update(connected=True, username=user, method="saved")
        return info

    @app.get("/api/instagram")
    def get_connection(owner: str = Depends(owner_of)):
        return connection()

    @app.post("/api/instagram/session")
    def connect_session(body: SessionRequest, _: None = Depends(manager)):
        user = clean_username(body.username)
        sessionid = body.sessionid.strip().strip('"')
        if "sessionid=" in sessionid:  # pasted a whole cookie string
            sessionid = sessionid.split("sessionid=", 1)[1].split(";", 1)[0]
        cookies = auth.parse_cookie_data({"sessionid": sessionid})
        if ":" in cookies["sessionid"]:
            cookies["ds_user_id"] = cookies["sessionid"].split(":", 1)[0]
        auth.save_cookies(settings.sessions_dir, user, cookies)
        use_login(user)
        return connection()

    @app.post("/api/instagram/browser-login", status_code=202)
    def browser_login(body: LoginRequest, _: None = Depends(manager)):
        user = clean_username(body.username)
        if login_state["status"] == "waiting":
            raise HTTPException(409, "A login window is already open.")
        if importlib.util.find_spec("playwright") is None:
            raise HTTPException(501, "Browser login needs Playwright: pip install playwright && playwright install chromium")
        login_state.update(status="waiting", username=user, error="")

        def run() -> None:
            try:
                cookies = auth.browser_login(user, settings.proxy)
                auth.save_cookies(settings.sessions_dir, user, cookies)
                use_login(user)
                login_state.update(status="idle")
            except BaseException as exc:  # browser_login reports failures with SystemExit
                login_state.update(status="error", error=str(exc).replace("[!]", "").strip() or "Login failed.")

        threading.Thread(target=run, name="ig-login", daemon=True).start()
        return connection()

    @app.delete("/api/instagram")
    def disconnect(_: None = Depends(manager)):
        user = settings.ig_login or auth.active_login(settings.sessions_dir)
        if user:
            auth.delete_session(settings.sessions_dir, user)
        use_login(None)
        login_state.update(status="idle", username="", error="")
        return connection()

    @app.post("/api/scans", status_code=202)
    def create_scan(body: ScanRequest, owner: str = Depends(owner_of)):
        from ..pipeline import normalize_target
        target = normalize_target(body.target)
        if not target:
            raise HTTPException(422, "Invalid target")
        options = {"limit": body.limit, "ocr": body.ocr, "fresh": body.fresh}
        job = store.create(owner, target, options)
        worker.wake.set()
        return public(job)

    @app.get("/api/scans")
    def list_scans(owner: str = Depends(owner_of), limit: int = 50):
        return [public(j) for j in store.list(owner, min(limit, 200))]

    def own_job(job_id: str, owner: str) -> dict:
        job = store.get(job_id, owner)
        if job is None:
            raise HTTPException(404, "Scan not found")
        return job

    @app.get("/api/scans/{job_id}")
    def get_scan(job_id: str, owner: str = Depends(owner_of), entities: bool = False):
        job = own_job(job_id, owner)
        result = public(job)
        report_path = job["files"].get("json")
        if report_path and Path(report_path).is_file():
            report = json.loads(Path(report_path).read_text(encoding="utf-8"))
            result["brands"] = report["brands"]
            if entities:
                result["entities"] = report["entities"]
        return result

    @app.post("/api/scans/{job_id}/cancel")
    def cancel_scan(job_id: str, owner: str = Depends(owner_of)):
        job = own_job(job_id, owner)
        if job["status"] not in ACTIVE:
            raise HTTPException(409, f"Scan is already {job['status']}")
        if job["status"] == "queued":
            store.update(job_id, status="cancelled", message="Cancelled.")
        worker.cancel(job_id)
        return public(store.get(job_id))

    class RetryRequest(BaseModel):
        fresh: bool = False

    @app.post("/api/scans/{job_id}/retry", status_code=202)
    def retry_scan(job_id: str, body: RetryRequest | None = None, owner: str = Depends(owner_of)):
        job = own_job(job_id, owner)
        if job["status"] in ACTIVE:
            raise HTTPException(409, f"Scan is already {job['status']}")
        options = dict(job["options"], fresh=bool(body and body.fresh))
        worker.cancelled.discard(job_id)
        store.requeue(job_id, options)
        worker.wake.set()
        return public(store.get(job_id))

    @app.delete("/api/scans/{job_id}", status_code=204)
    def delete_scan(job_id: str, owner: str = Depends(owner_of)):
        job = own_job(job_id, owner)
        if job["status"] in ACTIVE:
            raise HTTPException(409, "Cancel the scan before deleting it.")
        store.delete(job_id)

    @app.get("/api/scans/{job_id}/files/{name}")
    def download(job_id: str, name: str, owner: str = Depends(owner_of)):
        job = own_job(job_id, owner)
        path = job["files"].get(FILES.get(name, ""), "")
        if not path or not Path(path).is_file():
            raise HTTPException(404, "File not available")
        return FileResponse(path, filename=f"{job['target']}_{name}")

    return app


def serve(settings: Settings, host: str = "127.0.0.1", port: int = 8000) -> None:
    try:
        import uvicorn
    except ImportError:
        raise SystemExit("The web service needs:  pip install 'brandscan[server]'  (fastapi, uvicorn)")
    if not settings.api_keys and host not in LOCAL_HOSTS:
        raise SystemExit("Refusing to serve on a public address without API keys. "
                         "Set BRANDSCAN_API_KEYS (e.g. 'alice:long-random-key'), or use --host 127.0.0.1.")
    if not settings.api_keys:
        log.warning("No API keys set: anyone on this machine can use the service.")
    log.info("Dashboard: http://%s:%d   API docs: http://%s:%d/api/docs", host, port, host, port)
    uvicorn.run(create_app(settings), host=host, port=port, log_level="warning")
