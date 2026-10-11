"""HTTP server: serves catalogues to Calorify, accepts uploads, and refreshes fetch sources on a schedule.

Environment:
    DATA_DIR                 where catalogues, inputs, and status live (default ./data)
    PORT                     listen port (default 8080)
    UPLOAD_TOKEN             required to upload or refresh; uploads are disabled when unset
    READ_TOKEN               optional; when set, catalogue downloads need ?token=<READ_TOKEN>
    REFRESH_INTERVAL_HOURS   how often fetch sources are re-downloaded (default 168, weekly)
    PUBLIC_BASE_URL          optional base URL shown on the index page (default: from the request)
"""

from __future__ import annotations

import datetime as dt
import hmac
import html
import json
import os
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .common import CatalogError
from .sources import SOURCES
from .store import Store

MAX_UPLOAD_BYTES = 60 * 1024 * 1024
RETRY_AFTER_FAILURE_SECONDS = 6 * 60 * 60


class Config:
    def __init__(self) -> None:
        self.data_dir = Path(os.environ.get("DATA_DIR", "data"))
        self.port = int(os.environ.get("PORT", "8080"))
        self.upload_token = os.environ.get("UPLOAD_TOKEN") or None
        self.read_token = os.environ.get("READ_TOKEN") or None
        self.refresh_interval = float(os.environ.get("REFRESH_INTERVAL_HOURS", "168")) * 3600
        self.public_base_url = (os.environ.get("PUBLIC_BASE_URL") or "").rstrip("/") or None


def tokens_match(supplied: str | None, expected: str | None) -> bool:
    return bool(expected) and bool(supplied) and hmac.compare_digest(supplied.encode(), expected.encode())


def parse_time(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(value).timestamp()
    except ValueError:
        return None


class Scheduler(threading.Thread):
    """Re-downloads fetch sources when they're older than the refresh interval."""

    def __init__(self, store: Store, config: Config):
        super().__init__(daemon=True, name="scheduler")
        self.store = store
        self.config = config

    def due(self, status: dict) -> bool:
        now = time.time()
        last_success = parse_time(status.get("updatedAt"))
        last_attempt = parse_time(status.get("lastAttemptAt"))
        if status.get("lastError") and last_attempt and now - last_attempt < RETRY_AFTER_FAILURE_SECONDS:
            return False
        return last_success is None or now - last_success >= self.config.refresh_interval

    def run(self) -> None:
        while True:
            for source in SOURCES.values():
                if source.kind != "fetch":
                    continue
                if not self.store.read_catalog(source.catalog_id) or self.due(self.store.status().get(source.key, {})):
                    try:
                        summary = self.store.refresh(source)
                        print(f"[refresh] {source.key}: {summary['foodCount']} foods", flush=True)
                    except Exception as error:
                        print(f"[refresh] {source.key} failed: {error}", flush=True)
            time.sleep(60)


def make_handler(store: Store, config: Config):
    class Handler(BaseHTTPRequestHandler):
        server_version = "calorify-menu-catalogs"
        sys_version = ""

        def log_message(self, format, *args):  # noqa: A002 - signature from BaseHTTPRequestHandler
            # Never log query strings, which may carry the read token.
            print(f"[http] {self.command} {urlsplit(self.path).path} {args[1] if len(args) > 1 else ''}", flush=True)

        # MARK: Responses

        def send_body(self, status: int, body: bytes, content_type: str, headers: dict | None = None) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            for name, value in (headers or {}).items():
                self.send_header(name, value)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def send_json(self, status: int, payload: object) -> None:
            self.send_body(status, json.dumps(payload).encode("utf-8"), "application/json", {"Cache-Control": "no-store"})

        def bearer_token(self) -> str | None:
            header = self.headers.get("Authorization", "")
            return header[7:].strip() if header.startswith("Bearer ") else None

        # MARK: Routes

        def do_HEAD(self):  # noqa: N802
            self.do_GET()

        def do_GET(self):  # noqa: N802
            url = urlsplit(self.path)
            if url.path == "/healthz":
                return self.send_body(200, b"ok\n", "text/plain")
            if url.path == "/":
                return self.send_body(200, self.index_page().encode("utf-8"), "text/html; charset=utf-8",
                                      {"Cache-Control": "no-store", "Content-Security-Policy": CSP})
            if url.path.startswith("/catalogs/") and url.path.endswith(".catalog.json"):
                return self.serve_catalog(url.path[len("/catalogs/"):-len(".catalog.json")], parse_qs(url.query))
            if url.path == "/api/status":
                return self.send_json(200, self.status_payload())
            self.send_json(404, {"error": "Not found"})

        def do_POST(self):  # noqa: N802
            parts = urlsplit(self.path).path.strip("/").split("/")
            if len(parts) != 4 or parts[:2] != ["api", "sources"] or parts[3] not in ("upload", "refresh"):
                return self.send_json(404, {"error": "Not found"})
            if not config.upload_token:
                return self.send_json(403, {"error": "Uploads are disabled. Set UPLOAD_TOKEN on the server."})
            if not tokens_match(self.bearer_token(), config.upload_token):
                return self.send_json(401, {"error": "Wrong upload token."})
            source = SOURCES.get(parts[2])
            if not source:
                return self.send_json(404, {"error": f"Unknown source {parts[2]}."})

            try:
                if parts[3] == "refresh":
                    summary = store.refresh(source)
                else:
                    if source.kind != "upload":
                        return self.send_json(400, {"error": f"{source.name} updates automatically; use refresh."})
                    length = int(self.headers.get("Content-Length") or 0)
                    if length <= 0 or length > MAX_UPLOAD_BYTES:
                        return self.send_json(413 if length else 400, {"error": "Upload is empty or larger than 60 MB."})
                    summary = store.upload(source, self.rfile.read(length))
            except CatalogError as error:
                return self.send_json(422, {"error": str(error)})
            except Exception as error:  # surface source failures (network, format changes) to the person
                return self.send_json(502, {"error": f"{type(error).__name__}: {error}"})
            self.send_json(200, {"source": source.key, **summary})

        def serve_catalog(self, catalog_id: str, query: dict) -> None:
            if config.read_token and not tokens_match((query.get("token") or [None])[0], config.read_token):
                return self.send_json(401, {"error": "Missing or wrong token."})
            found = store.read_catalog(catalog_id)
            if not found:
                return self.send_json(404, {"error": "Catalogue not built yet."})
            data, etag = found
            headers = {"ETag": etag, "Cache-Control": "no-cache"}
            if etag in [tag.strip() for tag in self.headers.get("If-None-Match", "").split(",")]:
                self.send_response(HTTPStatus.NOT_MODIFIED)
                for name, value in headers.items():
                    self.send_header(name, value)
                self.end_headers()
                return
            self.send_body(200, data, "application/json; charset=utf-8", headers)

        # MARK: Pages

        def status_payload(self) -> list[dict]:
            status = store.status()
            return [{
                "source": source.key,
                "name": source.name,
                "kind": source.kind,
                "catalogPath": f"/catalogs/{source.catalog_id}.catalog.json",
                "available": store.read_catalog(source.catalog_id) is not None,
                **{k: v for k, v in status.get(source.key, {}).items() if k != "notes"},
                "notes": status.get(source.key, {}).get("notes", []),
            } for source in SOURCES.values()]

        def base_url(self) -> str:
            if config.public_base_url:
                return config.public_base_url
            proto = self.headers.get("X-Forwarded-Proto", "http")
            return f"{proto}://{self.headers.get('Host', 'localhost')}"

        def index_page(self) -> str:
            rows = []
            for entry in self.status_payload():
                source = SOURCES[entry["source"]]
                url = self.base_url() + entry["catalogPath"] + ("?token=…" if config.read_token else "")
                updated = entry.get("updatedAt") or "never"
                state = (f'<p class="error">Last attempt failed: {html.escape(entry["lastError"])}</p>'
                         if entry.get("lastError") else "")
                notes = "".join(f"<li>{html.escape(note)}</li>" for note in entry["notes"][:5])
                action = (f'<input type="file" accept="{html.escape(source.accept)}" data-upload="{source.key}">'
                          if source.kind == "upload" else
                          f'<button data-refresh="{source.key}">Refresh now</button>')
                rows.append(f"""
                <section>
                  <h2>{html.escape(source.name)}</h2>
                  <p class="meta">{entry.get("foodCount", 0)} foods · updated {html.escape(updated)}</p>
                  {'<p class="url"><code>' + html.escape(url) + '</code></p>' if entry["available"] else '<p class="meta">Not built yet.</p>'}
                  {state}
                  {'<ul class="notes">' + notes + '</ul>' if notes else ''}
                  <p class="how">{html.escape(source.how_to)}</p>
                  <div class="actions">{action}<span class="result" id="result-{source.key}"></span></div>
                </section>""")
            uploads = ('<label>Upload token <input type="password" id="token" autocomplete="off"></label>'
                       if config.upload_token else '<p class="error">Uploads are disabled until UPLOAD_TOKEN is set.</p>')
            return PAGE.replace("{{UPLOADS}}", uploads).replace("{{SECTIONS}}", "".join(rows))

    return Handler


CSP = "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'; form-action 'none'; frame-ancestors 'none'"

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Menu catalogues</title>
<style>
  :root { color-scheme: light dark; --ink: #2c2c2a; --paper: #fbf8f0; --card: #fff; --accent: #1d9e75; --error: #b3261e; }
  @media (prefers-color-scheme: dark) { :root { --ink: #ececec; --paper: #1c1e21; --card: #2a2d31; } }
  body { margin: 0; font: 16px/1.5 ui-rounded, -apple-system, system-ui, sans-serif; color: var(--ink); background: var(--paper); }
  main { max-width: 720px; margin: 0 auto; padding: 24px 16px 48px; }
  h1 { margin: 0 0 4px; } h2 { margin: 0 0 4px; font-size: 20px; }
  section { background: var(--card); border: 2px solid var(--ink); border-radius: 14px; box-shadow: 3px 3px 0 var(--ink); padding: 16px; margin: 18px 0; }
  .meta, .how { opacity: .75; margin: 4px 0; } .error { color: var(--error); font-weight: 600; }
  code { word-break: break-all; font-size: 13px; } .url { margin: 6px 0; }
  .actions { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; margin-top: 8px; }
  button { font: inherit; font-weight: 600; padding: 6px 14px; border: 2px solid var(--ink); border-radius: 999px; background: #9fe1cb; color: #04342c; cursor: pointer; }
  input[type=password] { font: inherit; padding: 4px 8px; margin-left: 6px; }
  .notes { margin: 4px 0; padding-left: 20px; font-size: 14px; opacity: .8; }
</style></head>
<body><main>
<h1>Menu catalogues</h1>
<p class="meta">Add a catalogue in Calorify with Settings → Food Catalogues → Add from URL.</p>
{{UPLOADS}}
{{SECTIONS}}
</main>
<script>
  const token = () => (document.getElementById("token") || {}).value || "";
  async function send(source, action, body) {
    const result = document.getElementById("result-" + source);
    result.textContent = "Working…";
    try {
      const response = await fetch(`/api/sources/${source}/${action}`, {
        method: "POST", body, headers: { "Authorization": "Bearer " + token() } });
      const payload = await response.json();
      if (!response.ok) { result.textContent = payload.error || response.statusText; result.className = "result error"; return; }
      result.textContent = `Done: ${payload.foodCount} foods`; result.className = "result";
      setTimeout(() => location.reload(), 1200);
    } catch (error) { result.textContent = String(error); result.className = "result error"; }
  }
  document.querySelectorAll("[data-upload]").forEach(input => input.addEventListener("change", () => {
    if (input.files[0]) send(input.dataset.upload, "upload", input.files[0]);
  }));
  document.querySelectorAll("[data-refresh]").forEach(button => button.addEventListener("click", () => send(button.dataset.refresh, "refresh")));
</script>
</body></html>
"""


def serve() -> None:
    config = Config()
    store = Store(config.data_dir)
    store.rebuild_uploaded()
    Scheduler(store, config).start()
    server = ThreadingHTTPServer(("0.0.0.0", config.port), make_handler(store, config))
    print(f"[serve] listening on :{config.port}, data in {config.data_dir}, uploads "
          f"{'enabled' if config.upload_token else 'disabled'}", flush=True)
    server.serve_forever()
