"""
Anti-detection utilities for LinkedIn Auto Connector Bot.

This module provides various techniques to avoid bot detection including
browser fingerprinting, human-like behavior simulation, and stealth measures.
"""

import os
import sys
import shutil
import random
import time
import logging
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from selenium import webdriver
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.edge.options import Options as EdgeOptions
from selenium.webdriver.firefox.options import Options as FirefoxOptions
from selenium.webdriver.firefox.service import Service as FirefoxService
from selenium.webdriver.common.by import By
from selenium.common.exceptions import NoSuchElementException, StaleElementReferenceException
from dotenv import load_dotenv
import numpy as np
import json
import uuid

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

logger = logging.getLogger(__name__)


def _resolve_repo_path(raw_path: str) -> Path:
    """Resolve a path relative to the repository root if not already absolute."""
    p = Path(os.path.expanduser(raw_path.strip()))
    if not p.is_absolute():
        p = (PROJECT_ROOT / p).resolve()
    return p


def detect_browser_binary(browser_type: str = "edge") -> Optional[str]:
    """
    Locate browser executable via `.env` override first, then PATH, then
    standard installation directories across Windows, macOS, and Linux.
    """
    btype = browser_type.strip().lower()
    env_map = {
        "edge": "EDGE_BINARY_PATH",
        "chrome": "CHROME_BINARY_PATH",
        "firefox": "FIREFOX_BINARY_PATH",
    }
    env_key = env_map.get(btype)
    if env_key:
        env_val = os.getenv(env_key, "").strip()
        if env_val:
            resolved = _resolve_repo_path(env_val)
            if resolved.exists():
                return str(resolved)
            if shutil.which(env_val):
                return shutil.which(env_val)

    which_names = {
        "edge": ["msedge", "microsoft-edge", "microsoft-edge-stable"],
        "chrome": ["google-chrome", "google-chrome-stable", "chrome", "chromium", "chromium-browser"],
        "firefox": ["firefox"],
    }.get(btype, [])
    for name in which_names:
        found = shutil.which(name)
        if found:
            return found

    candidates: List[Path] = []
    if sys.platform == "win32":
        pf = os.environ.get("ProgramFiles", r"C:\Program Files")
        pfx86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
        local_app = os.environ.get("LOCALAPPDATA", "")
        if btype == "edge":
            candidates.extend([
                Path(pfx86) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
                Path(pf) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
                Path(local_app) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
            ])
        elif btype == "chrome":
            candidates.extend([
                Path(pf) / "Google" / "Chrome" / "Application" / "chrome.exe",
                Path(pfx86) / "Google" / "Chrome" / "Application" / "chrome.exe",
                Path(local_app) / "Google" / "Chrome" / "Application" / "chrome.exe",
            ])
        elif btype == "firefox":
            candidates.extend([
                Path(pf) / "Mozilla Firefox" / "firefox.exe",
                Path(pfx86) / "Mozilla Firefox" / "firefox.exe",
            ])
    elif sys.platform == "darwin":
        mac_map = {
            "edge": Path("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
            "chrome": Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
            "firefox": Path("/Applications/Firefox.app/Contents/MacOS/firefox"),
        }
        if btype in mac_map:
            candidates.append(mac_map[btype])
    else:
        linux_map = {
            "edge": [Path("/usr/bin/microsoft-edge"), Path("/usr/bin/microsoft-edge-stable")],
            "chrome": [Path("/usr/bin/google-chrome"), Path("/usr/bin/google-chrome-stable"), Path("/usr/bin/chromium")],
            "firefox": [Path("/usr/bin/firefox"), Path("/snap/bin/firefox")],
        }
        candidates.extend(linux_map.get(btype, []))

    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return None


class CDPWebElement:
    """Lightweight WebElement backed by a single Chrome DevTools Protocol WebSocket session."""

    def __init__(self, driver: "CDPWebDriver", cdp_id: str, meta: Optional[Dict] = None):
        self.driver = driver
        self.cdp_id = cdp_id
        self._meta = meta or {}

    def _js_call(self, fn_body: str, *args):
        script = f"""
        (function(cdpId, extraArgs) {{
            function findByCdpId(id, root) {{
                root = root || document;
                try {{
                    const direct = root.querySelector('[data-cdp-id="' + id + '"]');
                    if (direct) return direct;
                    const all = root.querySelectorAll('*');
                    for (let i = 0; i < all.length; i++) {{
                        if (all[i].shadowRoot) {{
                            const found = findByCdpId(id, all[i].shadowRoot);
                            if (found) return found;
                        }}
                        if (all[i].tagName === 'IFRAME' && all[i].contentDocument) {{
                            const found = findByCdpId(id, all[i].contentDocument);
                            if (found) return found;
                        }}
                    }}
                }} catch (e) {{}}
                return null;
            }}
            const el = findByCdpId(cdpId, document);
            if (!el) return {{ __stale: true }};
            {fn_body}
        }})({json.dumps(self.cdp_id)}, {json.dumps(list(args))})
        """
        res = self.driver._eval_js(script)
        if isinstance(res, dict) and res.get("__stale"):
            raise StaleElementReferenceException(f"Element {self.cdp_id} is no longer attached to the DOM")
        return res

    @property
    def tag_name(self) -> str:
        try:
            return self._js_call("return (el.tagName || '').toLowerCase();") or self._meta.get("tag", "").lower()
        except Exception:
            return self._meta.get("tag", "").lower()

    @property
    def text(self) -> str:
        try:
            return self._js_call("return (el.innerText || el.textContent || '').trim();") or ""
        except Exception:
            return self._meta.get("text", "")

    def get_attribute(self, name: str) -> Optional[str]:
        try:
            return self._js_call(
                "const n = extraArgs[0]; return n === 'href' ? (el.href || el.getAttribute(n)) : el.getAttribute(n);",
                name,
            )
        except Exception:
            return self._meta.get("attrs", {}).get(name)

    def is_displayed(self) -> bool:
        try:
            return bool(
                self._js_call(
                    "const r = el.getBoundingClientRect(); const s = window.getComputedStyle(el); "
                    "return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';"
                )
            )
        except Exception:
            return False

    def is_enabled(self) -> bool:
        try:
            return bool(self._js_call("return !el.disabled && el.getAttribute('aria-disabled') !== 'true';"))
        except Exception:
            return False

    @property
    def location_once_scrolled_into_view(self) -> Dict[str, int]:
        try:
            return self._js_call(
                "el.scrollIntoView({block: 'center', inline: 'nearest'}); "
                "const r = el.getBoundingClientRect(); return {x: Math.round(r.left), y: Math.round(r.top)};"
            ) or {"x": 100, "y": 100}
        except Exception:
            return {"x": 100, "y": 100}

    @property
    def size(self) -> Dict[str, int]:
        try:
            return self._js_call(
                "const r = el.getBoundingClientRect(); return {width: Math.round(r.width), height: Math.round(r.height)};"
            ) or {"width": 50, "height": 25}
        except Exception:
            return {"width": 50, "height": 25}

    def click(self):
        self._js_call(
            "el.scrollIntoView({block: 'center', inline: 'nearest'}); "
            "el.click(); return true;"
        )

    def clear(self):
        self._js_call(
            "el.focus(); "
            "const proto = el.tagName === 'TEXTAREA' ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype; "
            "const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set; "
            "if (setter) setter.call(el, ''); else el.value = ''; "
            "el.dispatchEvent(new Event('input', {bubbles: true})); return true;"
        )

    def send_keys(self, *values):
        text = "".join(str(v) for v in values)
        if text == "\ue003":  # Backspace
            self._js_call(
                "el.focus(); const v = (el.value || '').slice(0, -1); "
                "const proto = el.tagName === 'TEXTAREA' ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype; "
                "const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set; "
                "if (setter) setter.call(el, v); else el.value = v; "
                "el.dispatchEvent(new Event('input', {bubbles: true})); return true;"
            )
            return
        self._js_call(
            "el.focus(); const v = (el.value || '') + extraArgs[0]; "
            "const proto = el.tagName === 'TEXTAREA' ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype; "
            "const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set; "
            "if (setter) setter.call(el, v); else el.value = v; "
            "el.dispatchEvent(new Event('input', {bubbles: true})); "
            "el.dispatchEvent(new Event('change', {bubbles: true})); return true;",
            text,
        )

    def find_elements(self, by: str = By.XPATH, value: str = "") -> List["CDPWebElement"]:
        return self.driver.find_elements(by, value, root_cdp_id=self.cdp_id)

    def find_element(self, by: str = By.XPATH, value: str = "") -> "CDPWebElement":
        els = self.find_elements(by, value)
        if not els:
            raise NoSuchElementException(f"No child element found for {by}={value}")
        return els[0]


class CDPWebDriver:
    """
    Persistent Chrome DevTools Protocol driver for Edge 136+ remote debugging.
    Talks to a persistent local daemon (configurable via CDP_DAEMON_HOST / CDP_DAEMON_PORT)
    that keeps a SINGLE WebSocket connection open to Edge indefinitely so Edge never re-prompts 'Allow'.
    """

    def __init__(self, ws_url: str):
        import requests
        import subprocess
        import sys

        host = os.getenv("CDP_DAEMON_HOST", "127.0.0.1").strip() or "127.0.0.1"
        port = os.getenv("CDP_DAEMON_PORT", "9229").strip() or "9229"
        self.daemon_url = f"http://{host}:{port}"
        self.ws_url = ws_url
        self._attached_to_existing = True
        self._is_cdp_driver = True

        # Check if persistent daemon is already running; if not, spawn it cleanly without a console window
        if not self._daemon_alive():
            daemon_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cdp_daemon.py")
            creationflags = (
                getattr(subprocess, "CREATE_NO_WINDOW", 0)
                | getattr(subprocess, "DETACHED_PROCESS", 0)
                | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            )
            subprocess.Popen(
                [sys.executable, daemon_script],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                creationflags=creationflags,
                close_fds=True,
            )
            for _ in range(40):
                if self._daemon_alive():
                    break
                time.sleep(0.25)
            else:
                raise RuntimeError(f"Timed out starting local CDP bridge on {self.daemon_url}")

    def _daemon_alive(self) -> bool:
        import requests

        try:
            r = requests.get(f"{self.daemon_url}/status", timeout=1.5)
            return r.status_code == 200 and r.json().get("ok") is True
        except Exception:
            return False

    def _send_cdp(self, method: str, params: Optional[Dict] = None, use_session: bool = True) -> Dict:
        import requests

        r = requests.post(
            f"{self.daemon_url}/cdp",
            json={"method": method, "params": params or {}, "use_session": use_session},
            timeout=125,
        )
        data = r.json()
        if "error" in data:
            raise RuntimeError(f"CDP error in {method}: {data['error']}")
        return data.get("result", {})

    def _eval_js(self, expression: str):
        res = self._send_cdp(
            "Runtime.evaluate",
            {
                "expression": expression,
                "returnByValue": True,
                "awaitPromise": True,
            },
        )
        if "exceptionDetails" in res:
            desc = res["exceptionDetails"].get("text", "JS evaluation error")
            raise RuntimeError(desc)
        return res.get("result", {}).get("value")

    @property
    def current_url(self) -> str:
        try:
            return self._eval_js("window.location.href") or ""
        except Exception:
            return ""

    @property
    def title(self) -> str:
        try:
            return self._eval_js("document.title") or ""
        except Exception:
            return ""

    def get(self, url: str):
        curr = self.current_url
        if curr == url:
            logger.info("Already on target URL, skipping reload")
            return
        self._send_cdp("Page.navigate", {"url": url})
        time.sleep(2.5)
        for _ in range(20):
            try:
                state = self._eval_js("document.readyState")
                if state in ("interactive", "complete"):
                    break
            except Exception:
                pass
            time.sleep(0.5)

    def execute_script(self, script: str, *args):
        mapped_args = []
        for a in args:
            if isinstance(a, CDPWebElement):
                mapped_args.append({"__cdp_el": a.cdp_id})
            else:
                mapped_args.append(a)
        js = f"""
        (function(rawArgs) {{
            function findByCdpId(id, root) {{
                root = root || document;
                try {{
                    const direct = root.querySelector('[data-cdp-id="' + id + '"]');
                    if (direct) return direct;
                    const all = root.querySelectorAll('*');
                    for (let i = 0; i < all.length; i++) {{
                        if (all[i].shadowRoot) {{
                            const found = findByCdpId(id, all[i].shadowRoot);
                            if (found) return found;
                        }}
                        if (all[i].tagName === 'IFRAME' && all[i].contentDocument) {{
                            const found = findByCdpId(id, all[i].contentDocument);
                            if (found) return found;
                        }}
                    }}
                }} catch (e) {{}}
                return null;
            }}
            const resolvedArgs = rawArgs.map(a => (a && a.__cdp_el) ? findByCdpId(a.__cdp_el, document) : a);
            return (function() {{ {script} }}).apply(null, resolvedArgs);
        }})({json.dumps(mapped_args)})
        """
        return self._eval_js(js)

    def find_elements(self, by: str = By.XPATH, value: str = "", root_cdp_id: Optional[str] = None) -> List[CDPWebElement]:
        js = f"""
        (function(by, value, rootId) {{
            function findByCdpId(id, r) {{
                r = r || document;
                try {{
                    const d = r.querySelector('[data-cdp-id="' + id + '"]');
                    if (d) return d;
                    const all = r.querySelectorAll('*');
                    for (let i = 0; i < all.length; i++) {{
                        if (all[i].shadowRoot) {{
                            const f = findByCdpId(id, all[i].shadowRoot);
                            if (f) return f;
                        }}
                        if (all[i].tagName === 'IFRAME' && all[i].contentDocument) {{
                            const f = findByCdpId(id, all[i].contentDocument);
                            if (f) return f;
                        }}
                    }}
                }} catch (e) {{}}
                return null;
            }}
            function collectRoots(r, list) {{
                list = list || [r];
                try {{
                    const all = r.querySelectorAll('*');
                    for (let i = 0; i < all.length; i++) {{
                        if (all[i].shadowRoot) {{
                            list.push(all[i].shadowRoot);
                            collectRoots(all[i].shadowRoot, list);
                        }}
                        if (all[i].tagName === 'IFRAME' && all[i].contentDocument) {{
                            list.push(all[i].contentDocument);
                            collectRoots(all[i].contentDocument, list);
                        }}
                    }}
                }} catch (e) {{}}
                return list;
            }}
            const root = rootId ? findByCdpId(rootId, document) : document;
            if (!root) return [];
            const roots = rootId ? [root] : collectRoots(document);
            let nodes = [];
            if (by === 'xpath') {{
                for (const r of roots) {{
                    const doc = r.ownerDocument || r;
                    if (doc && doc.evaluate && (r.nodeType === 9 || r.nodeType === 1)) {{
                        try {{
                            const snap = doc.evaluate(value, r, null, XPathResult.ORDERED_NODE_SNAPSHOT_TYPE, null);
                            for (let i = 0; i < snap.snapshotLength; i++) nodes.push(snap.snapshotItem(i));
                        }} catch (e) {{}}
                    }}
                }}
            }} else if (by === 'id') {{
                for (const r of roots) {{
                    const el = r.getElementById ? r.getElementById(value) : r.querySelector('#' + CSS.escape(value));
                    if (el) nodes.push(el);
                }}
            }} else if (by === 'class name') {{
                for (const r of roots) {{
                    nodes.push(...Array.from(r.querySelectorAll('.' + CSS.escape(value))));
                }}
            }} else if (by === 'css selector') {{
                for (const r of roots) {{
                    try {{ nodes.push(...Array.from(r.querySelectorAll(value))); }} catch (e) {{}}
                }}
            }} else if (by === 'tag name') {{
                for (const r of roots) {{
                    try {{ nodes.push(...Array.from(r.querySelectorAll(value))); }} catch (e) {{}}
                }}
            }}
            return nodes.map(el => {{
                let id = el.getAttribute('data-cdp-id');
                if (!id) {{
                    id = 'cdp-' + Math.random().toString(36).slice(2, 11) + '-' + Date.now().toString(36);
                    el.setAttribute('data-cdp-id', id);
                }}
                return {{
                    id: id,
                    tag: (el.tagName || '').toLowerCase(),
                    text: (el.innerText || el.textContent || '').trim(),
                    attrs: {{
                        'aria-label': el.getAttribute('aria-label'),
                        'href': el.href || el.getAttribute('href')
                    }}
                }};
            }});
        }})({json.dumps(by)}, {json.dumps(value)}, {json.dumps(root_cdp_id)})
        """
        items = self._eval_js(js) or []
        return [CDPWebElement(self, item["id"], item) for item in items]

    def find_element(self, by: str = By.XPATH, value: str = "") -> CDPWebElement:
        els = self.find_elements(by, value)
        if not els:
            raise NoSuchElementException(f"No element found for {by}={value}")
        return els[0]

    def get_cookies(self) -> List[Dict]:
        try:
            res = self._send_cdp("Network.getCookies", {"urls": ["https://www.linkedin.com", "https://linkedin.com"]})
            return res.get("cookies", [])
        except Exception:
            return []

    def add_cookie(self, cookie_dict: Dict):
        try:
            self._send_cdp("Network.setCookie", cookie_dict)
        except Exception:
            pass

    def delete_all_cookies(self):
        try:
            self._send_cdp("Network.clearBrowserCookies", {})
        except Exception:
            pass

    def refresh(self):
        try:
            self._send_cdp("Page.reload", {})
            time.sleep(2.5)
        except Exception:
            pass

    def quit(self):
        # Do NOT close the persistent daemon WebSocket so Edge never re-prompts
        pass

    def close(self):
        pass


class StealthBrowser:
    """
    Creates and configures a stealth browser instance with anti-detection features.
    """

    def __init__(self, browser_type: str = "chrome", config: Optional[Dict] = None):
        """
        Initialize StealthBrowser.

        Args:
            browser_type: Type of browser ('chrome' or 'firefox')
            config: Optional configuration dictionary
        """
        self.browser_type = browser_type.lower()
        self.config = config or {}
        self.user_agents = self._load_user_agents()

    def _load_user_agents(self) -> List[str]:
        """Load list of realistic user agents."""
        return [
            # Chrome on Windows
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
            # Chrome on Mac
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
            # Firefox on Windows
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:120.0) Gecko/20100101 Firefox/120.0",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:119.0) Gecko/20100101 Firefox/119.0",
            # Firefox on Mac
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:120.0) Gecko/20100101 Firefox/120.0",
            # Safari on Mac
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15"
        ]

    def create_driver(self, headless: bool = False) -> webdriver.Chrome:
        """
        Create a configured WebDriver with anti-detection features.

        Args:
            headless: Whether to run in headless mode

        Returns:
            Configured WebDriver instance
        """
        if self.browser_type == "chrome":
            return self._create_chrome_driver(headless)
        elif self.browser_type == "edge":
            return self._create_edge_driver(headless)
        elif self.browser_type == "firefox":
            return self._create_firefox_driver(headless)
        else:
            raise ValueError(f"Unsupported browser type: {self.browser_type}")

    def _start_cdp_http_bridge(self, edge_port: str, edge_path: str) -> int:
        """
        Start a lightweight local HTTP server that bridges msedgedriver's /json/version
        request to Edge 136+'s direct CDP WebSocket endpoint without opening an extra WebSocket.
        """
        import json
        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer
        import websocket

        ws_browser_url = f"ws://127.0.0.1:{edge_port}{edge_path}"

        # Pre-fetch Target.getTargets once before msedgedriver connects
        ws = websocket.create_connection(ws_browser_url, timeout=30)
        ws.send(json.dumps({"id": 1, "method": "Target.getTargets"}))
        targets = json.loads(ws.recv()).get("result", {}).get("targetInfos", [])
        ws.close()

        out_targets = []
        for t in targets:
            if t.get("type") == "page" and not t.get("url", "").startswith("edge://"):
                tid = t["targetId"]
                out_targets.append({
                    "description": "",
                    "devtoolsFrontendUrl": f"/devtools/inspector.html?ws=127.0.0.1:{edge_port}/devtools/page/{tid}",
                    "id": tid,
                    "title": t.get("title", ""),
                    "type": "page",
                    "url": t.get("url", ""),
                    "webSocketDebuggerUrl": f"ws://127.0.0.1:{edge_port}/devtools/page/{tid}",
                })
        targets_body = json.dumps(out_targets).encode("utf-8")

        version_body = json.dumps({
            "Browser": "Edg/157.0.4341.42",
            "Protocol-Version": "1.3",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/157.0.0.0 Safari/537.36 Edg/157.0.0.0",
            "V8-Version": "15.7.0",
            "WebKit-Version": "537.36 (@7b1486be4607d53d9f93841b7c776bc525ca74b7)",
            "webSocketDebuggerUrl": ws_browser_url,
        }).encode("utf-8")

        class CDPBridgeHandler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                pass

            def do_GET(self):
                if self.path.startswith("/json/version"):
                    body = version_body
                elif self.path.startswith("/json"):
                    body = targets_body
                else:
                    self.send_response(404)
                    self.end_headers()
                    return

                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        srv = HTTPServer(("127.0.0.1", 0), CDPBridgeHandler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        return srv.server_port

    def _create_edge_driver(self, headless: bool) -> webdriver.Edge:
        """Create stealth Microsoft Edge driver (supports existing signed-in Edge profile or debugger port)."""
        from utils.cdp_daemon import find_devtools_active_port_file

        options = EdgeOptions()
        options.set_capability("ms:loggingPrefs", {"performance": "ALL"})

        edge_bin = os.getenv("EDGE_BINARY_PATH", "").strip()
        if edge_bin:
            resolved_bin = _resolve_repo_path(edge_bin)
            if resolved_bin.exists():
                options.binary_location = str(resolved_bin)

        # Check if connecting to an already-running Edge instance with remote debugging
        debugger_addr = os.getenv("EDGE_DEBUGGER_ADDRESS", "").strip()
        if debugger_addr.lower() == "auto":
            port_file = find_devtools_active_port_file()
            if port_file and port_file.exists():
                lines = [ln.strip() for ln in port_file.read_text(encoding="utf-8").splitlines() if ln.strip()]
                if len(lines) >= 2 and lines[0].isdigit():
                    ws_url = f"ws://127.0.0.1:{lines[0]}{lines[1]}"
                    driver = CDPWebDriver(ws_url)
                    logger.info(f"Attached to running Edge browser via single-connection CDP ({ws_url})")
                    return driver
                elif lines and lines[0].isdigit():
                    debugger_addr = f"127.0.0.1:{lines[0]}"
            else:
                debugger_addr = ""

        if debugger_addr:
            options.add_experimental_option("debuggerAddress", debugger_addr)
            driver = webdriver.Edge(options=options)
            driver._attached_to_existing = True
            for handle in driver.window_handles:
                driver.switch_to.window(handle)
                if "linkedin.com" in driver.current_url:
                    break
            try:
                driver.execute_cdp_cmd("Network.enable", {})
            except Exception:
                pass
            logger.info(f"Attached to running Edge browser via {debugger_addr}")
            return driver

        width = random.randint(1200, 1920)
        height = random.randint(800, 1080)
        options.add_argument(f'--window-size={width},{height}')

        options.add_argument('--disable-blink-features=AutomationControlled')
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option('useAutomationExtension', False)
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--no-sandbox")

        # Support using a persistent Edge profile directory if configured (relative paths resolve to repo root)
        edge_user_data = os.getenv("EDGE_USER_DATA_DIR", "").strip()
        edge_profile = os.getenv("EDGE_PROFILE_DIRECTORY", "Default").strip() or "Default"
        if edge_user_data:
            resolved_profile_dir = _resolve_repo_path(edge_user_data)
            resolved_profile_dir.mkdir(parents=True, exist_ok=True)
            options.add_argument(f"--user-data-dir={resolved_profile_dir}")
            options.add_argument(f"--profile-directory={edge_profile}")

        if headless:
            options.add_argument('--headless=new')
            options.add_argument('--disable-gpu')

        driver = webdriver.Edge(options=options)
        try:
            driver.execute_cdp_cmd("Network.enable", {})
        except Exception:
            pass
        driver.execute_cdp_cmd('Page.addScriptToEvaluateOnNewDocument', {
            'source': '''
                Object.defineProperty(navigator, 'webdriver', {
                    get: () => undefined
                });
            '''
        })
        logger.info(f"Created stealth Edge driver with size {width}x{height}")
        return driver

    def _create_chrome_driver(self, headless: bool) -> webdriver.Chrome:
        """Create stealth Chrome driver (supports optional persistent profile directory via CHROME_USER_DATA_DIR)."""
        options = ChromeOptions()
        options.set_capability("goog:loggingPrefs", {"performance": "ALL"})

        chrome_bin = os.getenv("CHROME_BINARY_PATH", "").strip()
        if chrome_bin:
            resolved_bin = _resolve_repo_path(chrome_bin)
            if resolved_bin.exists():
                options.binary_location = str(resolved_bin)

        # Randomize window size
        width = random.randint(1200, 1920)
        height = random.randint(800, 1080)
        options.add_argument(f'--window-size={width},{height}')

        # Anti-detection arguments
        options.add_argument('--disable-blink-features=AutomationControlled')
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option('useAutomationExtension', False)

        # Disable webdriver flag
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--no-sandbox")

        chrome_user_data = os.getenv("CHROME_USER_DATA_DIR", "").strip()
        chrome_profile = os.getenv("CHROME_PROFILE_DIRECTORY", "Default").strip() or "Default"
        if chrome_user_data:
            resolved_profile_dir = _resolve_repo_path(chrome_user_data)
            resolved_profile_dir.mkdir(parents=True, exist_ok=True)
            options.add_argument(f"--user-data-dir={resolved_profile_dir}")
            options.add_argument(f"--profile-directory={chrome_profile}")
        else:
            # Random user agent when running stateless Chrome
            user_agent = random.choice(self.user_agents)
            options.add_argument(f'user-agent={user_agent}')

            prefs = {
                "credentials_enable_service": False,
                "profile.password_manager_enabled": False,
                "profile.default_content_setting_values.notifications": 2,
                "profile.default_content_setting_values.geolocation": 2
            }
            options.add_experimental_option("prefs", prefs)

        if headless:
            options.add_argument('--headless=new')
            options.add_argument('--disable-gpu')
            options.add_argument('--disable-software-rasterizer')

        # Create driver
        driver = webdriver.Chrome(options=options)
        try:
            driver.execute_cdp_cmd("Network.enable", {})
        except Exception:
            pass

        # Execute CDP commands to override navigator properties
        driver.execute_cdp_cmd('Page.addScriptToEvaluateOnNewDocument', {
            'source': '''
                // Override webdriver property
                Object.defineProperty(navigator, 'webdriver', {
                    get: () => undefined
                });

                // Override plugins to look more realistic
                Object.defineProperty(navigator, 'plugins', {
                    get: () => [
                        {
                            0: {type: "application/x-google-chrome-pdf", suffixes: "pdf", description: "Portable Document Format"},
                            description: "Portable Document Format",
                            filename: "internal-pdf-viewer",
                            length: 1,
                            name: "Chrome PDF Plugin"
                        }
                    ]
                });

                // Override permissions
                const originalQuery = window.navigator.permissions.query;
                window.navigator.permissions.query = (parameters) => (
                    parameters.name === 'notifications' ?
                        Promise.resolve({ state: Notification.permission }) :
                        originalQuery(parameters)
                );

                // Override chrome runtime
                window.chrome = {
                    runtime: {},
                    loadTimes: function() {},
                    csi: function() {},
                    app: {}
                };

                // Override language and platform if needed
                Object.defineProperty(navigator, 'language', {
                    get: () => 'en-US'
                });
                Object.defineProperty(navigator, 'languages', {
                    get: () => ['en-US', 'en']
                });
            '''
        })

        logger.info(f"Created stealth Chrome driver with size {width}x{height}")
        return driver

    def _create_firefox_driver(self, headless: bool) -> webdriver.Firefox:
        """Create stealth Firefox driver (supports optional FIREFOX_BINARY_PATH and GECKODRIVER_PATH)."""
        options = FirefoxOptions()

        firefox_bin = os.getenv("FIREFOX_BINARY_PATH", "").strip()
        if firefox_bin:
            resolved_ff = _resolve_repo_path(firefox_bin)
            if resolved_ff.exists():
                options.binary_location = str(resolved_ff)

        # Randomize window size
        width = random.randint(1200, 1920)
        height = random.randint(800, 1080)
        options.add_argument(f'--width={width}')
        options.add_argument(f'--height={height}')

        # Set user agent
        user_agent = random.choice(self.user_agents)
        options.set_preference("general.useragent.override", user_agent)

        # Disable telemetry and tracking
        options.set_preference("toolkit.telemetry.unified", False)
        options.set_preference("privacy.trackingprotection.enabled", True)

        # Disable WebDriver flags
        options.set_preference("dom.webdriver.enabled", False)
        options.set_preference('useAutomationExtension', False)

        if headless:
            options.add_argument('--headless')

        geckodriver_env = os.getenv("GECKODRIVER_PATH", "").strip()
        if geckodriver_env and _resolve_repo_path(geckodriver_env).exists():
            service = FirefoxService(executable_path=str(_resolve_repo_path(geckodriver_env)))
            driver = webdriver.Firefox(service=service, options=options)
        else:
            driver = webdriver.Firefox(options=options)

        # Execute script to override navigator properties
        driver.execute_script("""
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });
        """)

        logger.info(f"Created stealth Firefox driver with size {width}x{height}")
        return driver


class HumanBehaviorSimulator:
    """
    Simulates human-like behavior for browser interactions.
    """

    def __init__(self, driver):
        """
        Initialize HumanBehaviorSimulator.

        Args:
            driver: Selenium WebDriver instance
        """
        self.driver = driver
        self.actions = None if getattr(driver, "_is_cdp_driver", False) else ActionChains(driver)

    def human_like_mouse_movement(self, element, duration: float = 1.0):
        """
        Move mouse to element with human-like curve.

        Args:
            element: Target WebElement
            duration: Duration of movement in seconds
        """
        try:
            # Get current mouse position (approximate)
            viewport_width = self.driver.execute_script("return window.innerWidth")
            viewport_height = self.driver.execute_script("return window.innerHeight")

            start_x = random.randint(100, viewport_width - 100)
            start_y = random.randint(100, viewport_height - 100)

            # Get element position
            location = element.location_once_scrolled_into_view
            size = element.size
            end_x = location['x'] + size['width'] // 2
            end_y = location['y'] + size['height'] // 2

            # Generate curved path
            points = self._generate_bezier_curve(start_x, start_y, end_x, end_y)

            # Move mouse along path
            action_chains = ActionChains(self.driver)
            for x, y in points:
                action_chains.move_by_offset(x - start_x, y - start_y)
                start_x, start_y = x, y

            action_chains.perform()
            time.sleep(random.uniform(0.1, 0.3))

        except Exception as e:
            logger.error(f"Error in mouse movement: {str(e)}")
            # Fallback to simple move
            ActionChains(self.driver).move_to_element(element).perform()

    def human_like_typing(self, element, text: str):
        """
        Type text with human-like speed and patterns.

        Args:
            element: Input element
            text: Text to type
        """
        element.click()
        element.clear()

        for i, char in enumerate(text):
            element.send_keys(char)

            # Variable typing speed
            base_delay = random.uniform(0.05, 0.15)

            # Occasionally longer pauses (thinking)
            if random.random() < 0.1:
                time.sleep(random.uniform(0.5, 1.5))
            # Faster for common words
            elif i > 0 and text[i-1] == ' ':
                time.sleep(base_delay * 0.5)
            else:
                time.sleep(base_delay)

            # Occasional typos and corrections
            if random.random() < 0.02 and i < len(text) - 1:
                wrong_char = random.choice('abcdefghijklmnopqrstuvwxyz')
                element.send_keys(wrong_char)
                time.sleep(0.3)
                element.send_keys('\ue003')  # Backspace

    def random_scrolling(self):
        """Perform random scrolling behavior."""
        # Decide scroll direction and amount
        scroll_amount = random.randint(100, 500)
        direction = random.choice([1, -1])

        # Smooth scrolling
        steps = random.randint(3, 7)
        for _ in range(steps):
            self.driver.execute_script(
                f"window.scrollBy(0, {(scroll_amount // steps) * direction})"
            )
            time.sleep(random.uniform(0.1, 0.3))

        # Random pause after scrolling
        time.sleep(random.uniform(1, 3))

    def random_mouse_movement(self):
        """Perform random mouse movements."""
        try:
            action_chains = ActionChains(self.driver)
            for _ in range(random.randint(1, 3)):
                x = random.randint(-30, 30)
                y = random.randint(-30, 30)
                action_chains.move_by_offset(x, y)
                action_chains.pause(random.uniform(0.1, 0.3))
            action_chains.perform()
        except Exception:
            pass

    def simulate_reading(self, duration: float = None):
        """
        Simulate reading behavior with scrolling and pauses.

        Args:
            duration: Reading duration in seconds (random if None)
        """
        if duration is None:
            duration = random.uniform(3, 10)

        start_time = time.time()

        while time.time() - start_time < duration:
            # Random scroll
            if random.random() < 0.3:
                self.random_scrolling()

            # Random mouse movement
            if random.random() < 0.2:
                self.random_mouse_movement()

            # Random pause
            time.sleep(random.uniform(0.5, 2))

    def _generate_bezier_curve(self, x1: float, y1: float, x2: float, y2: float, num_points: int = 20) -> List[Tuple[float, float]]:
        """
        Generate points along a Bezier curve for smooth movement.

        Args:
            x1, y1: Start coordinates
            x2, y2: End coordinates
            num_points: Number of points to generate

        Returns:
            List of (x, y) coordinates
        """
        # Generate control points for natural curve
        cx1 = x1 + random.uniform(-100, 100)
        cy1 = y1 + random.uniform(-100, 100)
        cx2 = x2 + random.uniform(-100, 100)
        cy2 = y2 + random.uniform(-100, 100)

        points = []
        for i in range(num_points + 1):
            t = i / num_points

            # Cubic Bezier curve formula
            x = (1-t)**3 * x1 + 3*(1-t)**2 * t * cx1 + 3*(1-t) * t**2 * cx2 + t**3 * x2
            y = (1-t)**3 * y1 + 3*(1-t)**2 * t * cy1 + 3*(1-t) * t**2 * cy2 + t**3 * y2

            points.append((x, y))

        return points


class ProxyManager:
    """
    Manages proxy rotation and configuration for browsers.
    """

    def __init__(self, proxy_list: Optional[List[str]] = None):
        """
        Initialize ProxyManager.

        Args:
            proxy_list: Optional list of proxy addresses
        """
        self.proxy_list = proxy_list or []
        self.current_proxy_index = 0

    def get_next_proxy(self) -> Optional[str]:
        """
        Get next proxy from the list.

        Returns:
            Proxy address or None if no proxies available
        """
        if not self.proxy_list:
            return None

        proxy = self.proxy_list[self.current_proxy_index]
        self.current_proxy_index = (self.current_proxy_index + 1) % len(self.proxy_list)

        logger.info(f"Using proxy: {proxy}")
        return proxy

    def configure_chrome_proxy(self, options: ChromeOptions, proxy: str):
        """
        Configure Chrome options with proxy.

        Args:
            options: Chrome options object
            proxy: Proxy address
        """
        if proxy:
            options.add_argument(f'--proxy-server={proxy}')
            logger.info(f"Configured Chrome with proxy: {proxy}")

    def configure_firefox_proxy(self, options: FirefoxOptions, proxy: str):
        """
        Configure Firefox options with proxy.

        Args:
            options: Firefox options object
            proxy: Proxy address (format: host:port)
        """
        if proxy and ':' in proxy:
            host, port = proxy.split(':')
            options.set_preference("network.proxy.type", 1)
            options.set_preference("network.proxy.http", host)
            options.set_preference("network.proxy.http_port", int(port))
            options.set_preference("network.proxy.ssl", host)
            options.set_preference("network.proxy.ssl_port", int(port))
            logger.info(f"Configured Firefox with proxy: {proxy}")

    def test_proxy(self, proxy: str) -> bool:
        """
        Test if proxy is working.

        Args:
            proxy: Proxy address to test

        Returns:
            True if proxy works, False otherwise
        """
        import requests

        try:
            response = requests.get(
                'http://httpbin.org/ip',
                proxies={'http': proxy, 'https': proxy},
                timeout=10
            )
            if response.status_code == 200:
                logger.info(f"Proxy {proxy} is working")
                return True
        except Exception as e:
            logger.error(f"Proxy {proxy} failed: {str(e)}")

        return False


class BehaviorRandomizer:
    """
    Randomizes various behavioral parameters to avoid patterns.
    """

    @staticmethod
    def get_random_delay(min_seconds: float = 1, max_seconds: float = 5) -> float:
        """Get randomized delay duration."""
        return random.uniform(min_seconds, max_seconds)

    @staticmethod
    def get_random_window_size() -> Tuple[int, int]:
        """Get randomized window dimensions."""
        widths = [1280, 1366, 1440, 1536, 1920]
        heights = [720, 768, 900, 1080]

        width = random.choice(widths)
        height = random.choice(heights)

        # Add small random variation
        width += random.randint(-50, 50)
        height += random.randint(-50, 50)

        return width, height

    @staticmethod
    def should_perform_action(probability: float = 0.5) -> bool:
        """Randomly decide whether to perform an action."""
        return random.random() < probability

    @staticmethod
    def get_random_timezone() -> str:
        """Get random timezone for browser."""
        timezones = [
            'America/New_York',
            'America/Chicago',
            'America/Denver',
            'America/Los_Angeles',
            'Europe/London',
            'Europe/Paris',
            'Europe/Berlin',
            'Asia/Tokyo',
            'Asia/Shanghai',
            'Australia/Sydney'
        ]
        return random.choice(timezones)

    @staticmethod
    def get_random_language() -> str:
        """Get random language setting."""
        languages = [
            'en-US',
            'en-GB',
            'en-CA',
            'en-AU',
            'es-ES',
            'fr-FR',
            'de-DE',
            'it-IT',
            'pt-BR',
            'ja-JP'
        ]
        return random.choice(languages)


# Example usage
if __name__ == "__main__":
    # Configure logging for testing
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    # Create stealth browser
    stealth = StealthBrowser(browser_type="chrome")

    # Example of behavior randomizer
    randomizer = BehaviorRandomizer()
    print(f"Random delay: {randomizer.get_random_delay()} seconds")
    print(f"Random window size: {randomizer.get_random_window_size()}")
    print(f"Should perform action: {randomizer.should_perform_action(0.7)}")