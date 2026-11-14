"""
Persistent Single-WebSocket CDP Daemon for ConnectPilot.

Keeps a single authorized WebSocket connection to Edge's DevToolsActivePort open
so Edge never re-prompts the user with "Allow remote debugging?" across bot runs.
All paths, hosts, and ports are configurable via `.env`.
"""

import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv
import websocket

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")


def resolve_project_path(raw_path: str) -> Path:
    """Resolve a path string relative to the repository root if not absolute."""
    p = Path(os.path.expanduser(raw_path.strip()))
    if not p.is_absolute():
        p = (PROJECT_ROOT / p).resolve()
    return p


def find_devtools_active_port_file() -> Optional[Path]:
    """
    Locate Edge's DevToolsActivePort file across Windows, macOS, Linux,
    or custom `.env` overrides (EDGE_DEVTOOLS_ACTIVE_PORT_FILE / EDGE_USER_DATA_DIR).
    """
    custom_port_file = os.getenv("EDGE_DEVTOOLS_ACTIVE_PORT_FILE", "").strip()
    if custom_port_file:
        candidate = resolve_project_path(custom_port_file)
        if candidate.exists():
            return candidate

    edge_user_data = os.getenv("EDGE_USER_DATA_DIR", "").strip()
    if edge_user_data:
        candidate = resolve_project_path(edge_user_data) / "DevToolsActivePort"
        if candidate.exists():
            return candidate

    candidates = []
    if sys.platform == "win32":
        local_app_data = os.environ.get("LOCALAPPDATA", "")
        if local_app_data:
            candidates.append(Path(local_app_data) / "Microsoft" / "Edge" / "User Data" / "DevToolsActivePort")
    elif sys.platform == "darwin":
        candidates.append(Path.home() / "Library" / "Application Support" / "Microsoft Edge" / "DevToolsActivePort")
    else:
        candidates.append(Path.home() / ".config" / "microsoft-edge" / "DevToolsActivePort")

    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


class PersistentCDPSession:
    def __init__(self):
        self.lock = threading.Lock()
        self.ws = None
        self.msg_id = 0
        self.session_id = None
        self.target_id = None

    def _get_ws_url(self) -> str:
        port_file = find_devtools_active_port_file()
        if not port_file:
            raise FileNotFoundError(
                "Could not locate Edge DevToolsActivePort file. "
                "Set EDGE_DEVTOOLS_ACTIVE_PORT_FILE or EDGE_USER_DATA_DIR in .env."
            )
        lines = [ln.strip() for ln in port_file.read_text(encoding="utf-8").splitlines() if ln.strip()]
        if len(lines) < 2:
            raise ValueError(f"Invalid DevToolsActivePort format in {port_file}")
        return f"ws://127.0.0.1:{lines[0]}{lines[1]}"

    def _ensure_connected(self):
        if self.ws is not None and self.ws.connected and self.session_id:
            return
        ws_url = self._get_ws_url()
        self.ws = websocket.create_connection(ws_url, timeout=120)
        self._attach_linkedin_target()

    def _send_raw(self, method: str, params: dict = None, use_session: bool = True) -> dict:
        self.msg_id += 1
        mid = self.msg_id
        payload = {"id": mid, "method": method, "params": params or {}}
        if use_session and self.session_id:
            payload["sessionId"] = self.session_id
        self.ws.send(json.dumps(payload))
        while True:
            raw = self.ws.recv()
            data = json.loads(raw)
            if data.get("id") == mid:
                return data

    def _attach_linkedin_target(self):
        res = self._send_raw("Target.getTargets", {}, use_session=False)
        targets = res.get("result", {}).get("targetInfos", [])
        chosen = None
        for t in targets:
            if t.get("type") == "page" and "linkedin.com" in t.get("url", ""):
                chosen = t
                break
        if not chosen:
            for t in targets:
                if t.get("type") == "page" and not t.get("url", "").startswith("edge://"):
                    chosen = t
                    break
        if not chosen:
            raise RuntimeError("No open page target found in Edge")

        self.target_id = chosen["targetId"]
        attach = self._send_raw(
            "Target.attachToTarget",
            {"targetId": self.target_id, "flatten": True},
            use_session=False,
        )
        self.session_id = attach["result"]["sessionId"]
        self._send_raw("Runtime.enable", {})
        self._send_raw("Page.enable", {})

    def execute(self, method: str, params: dict = None, use_session: bool = True) -> dict:
        with self.lock:
            try:
                self._ensure_connected()
                resp = self._send_raw(method, params, use_session=use_session)
                if "error" in resp and resp["error"].get("code") in (-32001, -32602):
                    self._attach_linkedin_target()
                    resp = self._send_raw(method, params, use_session=use_session)
                return resp
            except Exception:
                try:
                    if self.ws:
                        self.ws.close()
                except Exception:
                    pass
                self.ws = None
                self.session_id = None
                self._ensure_connected()
                return self._send_raw(method, params, use_session=use_session)


def main():
    cdp = PersistentCDPSession()
    host = os.getenv("CDP_DAEMON_HOST", "127.0.0.1").strip() or "127.0.0.1"
    port = int(os.getenv("CDP_DAEMON_PORT", "9229").strip() or "9229")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        def do_GET(self):
            if self.path == "/status":
                body = json.dumps({
                    "ok": True,
                    "connected": bool(cdp.ws and cdp.ws.connected),
                    "session_id": cdp.session_id,
                    "target_id": cdp.target_id,
                }).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_response(404)
                self.end_headers()

        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length)
            req = json.loads(raw.decode("utf-8"))
            try:
                resp = cdp.execute(
                    req["method"],
                    req.get("params") or {},
                    use_session=req.get("use_session", True),
                )
                status = 200
            except Exception as e:
                resp = {"error": {"message": str(e)}}
                status = 500
            body = json.dumps(resp).encode("utf-8")
            try:
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except (ConnectionAbortedError, BrokenPipeError, OSError):
                pass

    srv = HTTPServer((host, port), Handler)
    srv.serve_forever()


if __name__ == "__main__":
    main()
