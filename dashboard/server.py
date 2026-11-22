"""Wave 1 pipeline dashboard, served from the migration workbench over HTTPS.

- One login (username + password) kept in Secrets Manager as capstone/dashboard-login. Sessions are random
  tokens in an HttpOnly, Secure, SameSite=Strict cookie; they expire after 8 hours.
- Five failed logins from one address lock that address out for 15 minutes.
- Live data is rebuilt every 30 seconds by collector.build() and injected into the page.
- Standard library only (plus boto3/yaml that collector uses). Listens on 443 with the certificate in /etc/capstone-dashboard.
"""
import hmac, html, http.server, json, os, secrets, ssl, threading, time, traceback, urllib.parse

import archgen
import assessment
import collector
import discovery  # after collector: it puts migration_flow (target_spec) on the path
import rulescheck
import chat  # after discovery: needs migration_flow on the path

HERE = os.path.dirname(os.path.abspath(__file__))
CERT_DIR = os.environ.get("DASHBOARD_CERT_DIR", "/etc/capstone-dashboard")
PORT = int(os.environ.get("DASHBOARD_PORT", "443"))
SESSION_TTL = 8 * 3600
LOCK_AFTER, LOCK_FOR = 5, 15 * 60
REFRESH = 10   # seconds; GitLab status is re-read this often (finished runs are cached)

_sessions = {}            # token -> expiry
_fails = {}               # ip -> [count, first_fail_time]
_status = {"value": None, "error": None}
_lock = threading.Lock()
_arch_lock = threading.Lock()   # one Eraser drawing at a time
_rules_lock = threading.Lock()  # one rules check (Claude call) at a time
ANTHROPIC_SECRET = "capstone/anthropic-api-key"
ERASER_SECRET = "capstone/eraser-api-key"


def _clean_token(s):
    """Tolerate a secret saved with leftovers from the console's key/value editor: use the one long token in it."""
    if not s:
        return None
    import re
    runs = re.findall(r"[A-Za-z0-9_.\-]{16,}", s)
    return runs[0] if len(runs) == 1 else s.strip()


def load_snapshot():
    return json.load(open(os.path.join(HERE, "snapshot.json")))


def refresher():
    snap = load_snapshot()
    while True:
        try:
            v = collector.build(snap)
            with _lock:
                _status["value"], _status["error"] = v, None
            snap = {"data": v["data"], "apps": v["apps"]}
        except Exception as e:  # keep serving the last good data
            with _lock:
                _status["error"] = f"{type(e).__name__}: {e}"
            traceback.print_exc()
        time.sleep(REFRESH)


def credentials():
    raw = collector._secrets.get_secret_value(SecretId="capstone/dashboard-login")["SecretString"]
    d = json.loads(raw)
    return d["username"], d["password"]


LOGIN_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Sign in · Wave 1 App Pipelines</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;600&family=IBM+Plex+Sans+Condensed:wght@600&display=swap">
<style>
:root{--bg:#f3f5f8;--panel:#fff;--line:#d9dee7;--fg:#172033;--muted:#5b6577;--accent:#2f4fd8;--bad:#b42323}
@media (prefers-color-scheme:dark){:root{--bg:#0f1420;--panel:#161d2c;--line:#2a3348;--fg:#e4e8f1;--muted:#98a2b6;--accent:#8ea4ff;--bad:#ff8a80;color-scheme:dark}}
*{box-sizing:border-box}body{margin:0;min-height:100vh;display:grid;place-items:center;background:var(--bg);color:var(--fg);font:14px "IBM Plex Sans",system-ui,sans-serif;padding:16px}
form{width:100%;max-width:360px;background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:28px;display:grid;gap:14px}
h1{font:600 24px "IBM Plex Sans Condensed","Arial Narrow",sans-serif;margin:0}.sub{color:var(--muted);margin:-6px 0 6px}
label{display:grid;gap:6px;font-weight:600;font-size:13px}input{font:inherit;padding:10px 12px;border:1px solid var(--line);border-radius:8px;background:var(--bg);color:var(--fg)}
input:focus{outline:2px solid var(--accent);outline-offset:1px}button{font:inherit;font-weight:600;padding:10px;border:0;border-radius:8px;background:var(--accent);color:#fff;cursor:pointer}
.err{color:var(--bad);font-size:13px;margin:0}
</style></head><body><form method="post" action="/login">
<div><div class="sub" style="margin:0 0 4px;font-size:12px;letter-spacing:.08em;text-transform:uppercase">Migration workbench</div><h1>Wave 1 App Pipelines</h1></div>
<p class="sub">Sign in to see pipeline stages, security gates and approvals.</p>
__ERR__<label for="u">Username<input id="u" name="username" autocomplete="username" required></label>
<label for="p">Password<input id="p" name="password" type="password" autocomplete="current-password" required></label>
<button type="submit">Sign in</button></form></body></html>"""


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "workbench"
    sys_version = ""
    timeout = 20  # seconds; drops idle or half-open connections so they cannot pile up

    def handle(self):
        try:
            self.connection.do_handshake()  # runs in this connection's own thread, bounded by `timeout`
            super().handle()
        except (ssl.SSLError, ConnectionError, TimeoutError):
            pass  # bad or abandoned client; nothing to answer

    def log_message(self, fmt, *args):  # no query strings or bodies in logs
        print(f"{self.client_address[0]} {self.command} {urllib.parse.urlsplit(self.path).path} {args[1] if len(args) > 1 else ''}", flush=True)

    def _headers(self, code, ctype, extra=None, nonce=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Strict-Transport-Security", "max-age=86400")
        script = f"'nonce-{nonce}'" if nonce else "'none'"
        self.send_header("Content-Security-Policy",
                         f"default-src 'self'; script-src {script}; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
                         "font-src https://fonts.gstatic.com; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; form-action 'self'")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()

    def _session_ok(self):
        c = self.headers.get("Cookie", "")
        tok = next((p.split("=", 1)[1] for p in c.split("; ") if p.startswith("wb_session=")), None)
        now = time.time()
        with _lock:
            for t in [t for t, e in _sessions.items() if e < now]:
                _sessions.pop(t, None)
            return bool(tok) and tok in _sessions

    def _login_page(self, err=""):
        body = LOGIN_PAGE.replace("__ERR__", f'<p class="err" role="alert">{html.escape(err)}</p>' if err else "").encode()
        self._headers(401 if err else 200, "text/html; charset=utf-8")
        self.wfile.write(body)

    def do_GET(self):
        path = urllib.parse.urlsplit(self.path).path
        if path == "/healthz":
            self._headers(200, "text/plain")
            self.wfile.write(b"ok")
            return
        if path == "/login":
            return self._login_page()
        if not self._session_ok():
            self._headers(303, "text/plain", {"Location": "/login"})
            return
        if path == "/logout":
            return self._logout()
        if path in ("/", "/index.html"):
            return self._page()
        if path == "/discovery":
            return self._render("discovery.html", "__DISC__", discovery.build(), "discovery")
        if path == "/assessment":
            data = assessment.status()
            data["rules"] = rulescheck.status()
            data["arch"] = {"generated": archgen.status(), "wave1": discovery.build().get("wave1", []), "eraser": ["CLM-FRAU1"]}
            return self._render("assessment.html", "__ASSESS__", data, "assessment")
        if path == "/chat/status":
            return self._json(200, chat.usage())
        if path == "/assessment/rules":
            q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            job, fmt = q.get("job", [""])[0], q.get("fmt", ["html"])[0]
            body = rulescheck.read(job, "md" if fmt == "md" else "html")
            if body is None:
                self._headers(404, "text/plain")
                self.wfile.write(b"not found")
                return
            if fmt == "md":
                self._headers(200, "text/markdown; charset=utf-8", {"Content-Disposition": f'attachment; filename="rules-check-{job}.md"'})
            else:
                self._headers(200, "text/html; charset=utf-8")
            self.wfile.write(body.encode())
            return
        if path == "/assessment/doc":
            q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            frag = assessment.read_doc(q.get("job", [""])[0], q.get("path", [""])[0])
            if frag is None:
                self._headers(404, "text/plain")
                self.wfile.write(b"not found")
                return
            self._headers(200, "text/html; charset=utf-8")
            self.wfile.write(frag.encode())
            return
        parts = path.split("/")
        if len(parts) == 4 and parts[1] == "arch":   # /arch/<APP>/<file>: generated diagrams
            f = archgen.file_path(parts[2], parts[3])
            if f:
                ctype = {"svg": "image/svg+xml", "png": "image/png"}.get(f.rsplit(".", 1)[-1], "text/plain; charset=utf-8")
                self._headers(200, ctype, None if ctype.startswith("image/") else {"Content-Disposition": f'attachment; filename="{parts[2]}-{parts[3]}"'})
                self.wfile.write(open(f, "rb").read())
                return
        if path.startswith("/arch/") and path.endswith(".svg") and "/" not in path[6:]:
            f = os.path.join(HERE, "arch", os.path.basename(path))
            if os.path.isfile(f):
                self._headers(200, "image/svg+xml")
                self.wfile.write(open(f, "rb").read())
                return
        self._headers(404, "text/plain")
        self.wfile.write(b"not found")

    def do_POST(self):
        if urllib.parse.urlsplit(self.path).path == "/assessment/scan":
            return self._start_scan()
        if urllib.parse.urlsplit(self.path).path == "/assessment/architecture":
            return self._make_arch()
        if urllib.parse.urlsplit(self.path).path == "/assessment/rules":
            return self._make_rules()
        if urllib.parse.urlsplit(self.path).path == "/chat":
            return self._chat()
        if urllib.parse.urlsplit(self.path).path != "/login":
            self._headers(404, "text/plain")
            return
        ip = self.client_address[0]
        now = time.time()
        with _lock:
            n, first = _fails.get(ip, [0, now])
            if n >= LOCK_AFTER and now - first < LOCK_FOR:
                return self._login_page("Too many failed sign-ins. Try again in 15 minutes.")
            if now - first >= LOCK_FOR:
                _fails.pop(ip, None)
        length = min(int(self.headers.get("Content-Length", "0") or 0), 4096)
        form = urllib.parse.parse_qs(self.rfile.read(length).decode(errors="replace"))
        user, pw = form.get("username", [""])[0], form.get("password", [""])[0]
        try:
            want_u, want_p = credentials()
        except Exception:
            return self._login_page("Sign-in is not set up yet: the login secret is missing.")
        if hmac.compare_digest(user.encode(), want_u.encode()) & hmac.compare_digest(pw.encode(), want_p.encode()):
            tok = secrets.token_urlsafe(32)
            with _lock:
                _sessions[tok] = now + SESSION_TTL
                _fails.pop(ip, None)
            self._headers(303, "text/plain", {"Location": "/",
                          "Set-Cookie": f"wb_session={tok}; Path=/; Max-Age={SESSION_TTL}; Secure; HttpOnly; SameSite=Strict"})
            return
        with _lock:
            n, first = _fails.get(ip, [0, now])
            _fails[ip] = [n + 1, first]
        self._login_page("Wrong username or password.")

    def _json(self, code, obj):
        self._headers(code, "application/json")
        self.wfile.write(json.dumps(obj).encode())

    def _start_scan(self):
        """Queue an AWS Transform scan. Signed-in, same-origin, JSON only; the root runner does the rest."""
        if not self._session_ok():
            return self._json(401, {"error": "Sign in first."})
        origin = self.headers.get("Origin", "")
        host = self.headers.get("Host", "")
        if not origin or urllib.parse.urlsplit(origin).netloc != host:
            return self._json(403, {"error": "Cross-site request refused."})
        if not self.headers.get("Content-Type", "").startswith("application/json"):
            return self._json(415, {"error": "JSON body expected."})
        length = min(int(self.headers.get("Content-Length", "0") or 0), 2048)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            return self._json(400, {"error": "Bad JSON."})
        code, msg = assessment.request_scan(str(body.get("app", ""))[:40], str(body.get("transformation", ""))[:80])
        print(f"{self.client_address[0]} scan request {body.get('app')} {body.get('transformation')} -> {code}", flush=True)
        return self._json(code, {"message": msg} if code < 300 else {"error": msg})

    def _json_body(self, max_len=2048):
        """Signed-in, same-origin JSON POST. Returns the body dict, or None after sending the error."""
        if not self._session_ok():
            self._json(401, {"error": "Sign in first."}); return None
        origin, host = self.headers.get("Origin", ""), self.headers.get("Host", "")
        if not origin or urllib.parse.urlsplit(origin).netloc != host:
            self._json(403, {"error": "Cross-site request refused."}); return None
        if not self.headers.get("Content-Type", "").startswith("application/json"):
            self._json(415, {"error": "JSON body expected."}); return None
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length > max_len:
            self._json(413, {"error": "Request too large."}); return None
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            self._json(400, {"error": "Bad JSON."}); return None
        return body if isinstance(body, dict) else {}

    def _make_arch(self):
        """Draw current and future state for one Wave 1 app: discovery data -> Eraser DSL -> Eraser renders it (about 10-30 s)."""
        body = self._json_body()
        if body is None:
            return
        app = str(body.get("app", ""))[:40]
        data = discovery.build()
        if app not in data.get("wave1", []):
            return self._json(400, {"error": f"{app or 'That app'} is not in Wave 1."})
        token = _clean_token(collector.secret(ERASER_SECRET))
        if not token:
            return self._json(503, {"error": f"Eraser is not connected yet: store an Eraser API token in Secrets Manager as {ERASER_SECRET}."})
        if not _arch_lock.acquire(blocking=False):
            return self._json(409, {"error": "Another diagram is being drawn. Try again in a few seconds."})
        try:
            meta = archgen.generate(data, app, who="dashboard", eraser_token=token)
        except archgen.EraserError as e:
            print(f"{self.client_address[0]} architecture for {app}: {e}", flush=True)
            return self._json(502, {"error": f"{e}. The previous diagram, if any, is unchanged."})
        except Exception as e:
            traceback.print_exc()
            return self._json(500, {"error": f"Could not draw the architecture: {type(e).__name__}."})
        finally:
            _arch_lock.release()
            token = None
        print(f"{self.client_address[0]} architecture drawn in Eraser for {app}", flush=True)
        return self._json(200, {"message": f"Eraser drew the architecture for {app}.", "meta": meta})

    def _chat(self):
        """Answers-only chat: Claude answers from the dashboard's data. No tools, so it cannot change anything."""
        body = self._json_body(max_len=40_000)
        if body is None:
            return
        key = _clean_token(collector.secret(ANTHROPIC_SECRET))
        if not key:
            return self._json(503, {"error": "Chat is not set up: the Claude API key secret is missing."})
        with _lock:
            live = _status["value"]
        try:
            code, payload = chat.answer(body.get("messages"), body.get("page", ""), body.get("filter"), live, key)
        except Exception as e:
            traceback.print_exc()
            return self._json(502, {"error": f"Claude could not answer ({type(e).__name__}). Try again.", "usage": chat.usage()})
        finally:
            key = None
        print(f"{self.client_address[0]} chat question -> {code}", flush=True)   # never the question text
        return self._json(code, payload)

    def _make_rules(self):
        """Rules check for one finished scan: Claude compares the rules with the scan documents (about a minute)."""
        body = self._json_body()
        if body is None:
            return
        job = str(body.get("job", ""))[:80]
        key = _clean_token(collector.secret(ANTHROPIC_SECRET))
        if not key:
            return self._json(503, {"error": f"Claude is not set up: the {ANTHROPIC_SECRET} secret is missing."})
        if not _rules_lock.acquire(blocking=False):
            return self._json(409, {"error": "Another rules check is running. Try again in a minute."})
        try:
            report = rulescheck.generate(discovery.build(), job, key, who="dashboard")
        except ValueError as e:
            return self._json(400, {"error": str(e)})
        except Exception as e:
            traceback.print_exc()
            return self._json(502, {"error": f"Claude could not write the rules check ({type(e).__name__}). Nothing was changed."})
        finally:
            _rules_lock.release()
            key = None
        print(f"{self.client_address[0]} rules check for {job}: {report['verdict']}, {len(report['rows'])} rows", flush=True)
        return self._json(200, {"message": "Rules check ready.", "summary": rulescheck.status().get(job)})

    def _logout(self):
        c = self.headers.get("Cookie", "")
        tok = next((p.split("=", 1)[1] for p in c.split("; ") if p.startswith("wb_session=")), None)
        with _lock:
            _sessions.pop(tok, None)
        self._headers(303, "text/plain", {"Location": "/login", "Set-Cookie": "wb_session=; Path=/; Max-Age=0; Secure; HttpOnly; SameSite=Strict"})

    def _page(self):
        with _lock:
            live = _status["value"]
        return self._render("pipeline.html", "__LIVE__", live, "pipelines")

    def _render(self, template, var, data, current):
        """Serve a page template with its data injected under a per-request CSP nonce, plus the top navigation
        and the shared Wave / App filter (filterbar.js), which keeps the same filter across all pages."""
        nonce = secrets.token_urlsafe(16)
        page = open(os.path.join(HERE, template), encoding="utf-8").read()
        js = lambda v: json.dumps(v).replace("</", "<\\/") if v is not None else "null"
        try:
            catalog = discovery.filter_catalog()
        except Exception:
            traceback.print_exc()
            catalog = {"apps": [], "waves": []}
        head = (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
                f'<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">'
                f'<style>{TOPNAV_CSS}</style>'
                f'<script nonce="{nonce}">window.{var}={js(data)};window.__FILTER__={js(catalog)};</script></head><body>')
        cur = lambda k: ' aria-current="page"' if k == current else ""
        link = lambda href, k, label: f'<a href="{href}" data-filter-link="{href}"{cur(k)}>{label}</a>'
        nav = ('<div class="topband"><div class="topbar"><nav class="topnav" aria-label="Pages">'
               '<span class="brand"><i aria-hidden="true"></i>Migration Workbench</span>'
               + link("/", "pipelines", "App Pipelines") + link("/discovery", "discovery", "Discovery")
               + link("/assessment", "assessment", "Assessment Phase") + '<a class="so" href="/logout">Sign out</a></nav>'
               '<div class="filterbar" role="group" aria-label="Filter all pages">'
               '<label><span>Wave</span><i class="wdot" id="fWaveDot" aria-hidden="true"></i><select id="fWave"></select></label>'
               '<label><span>App</span><select id="fApp"></select></label>'
               '<button type="button" id="fClear" hidden>Clear filter</button>'
               '<span class="fnote">Applies to every page</span></div>'
               '<div class="wlegend" id="fLegend" aria-label="Wave colors"></div></div></div>')
        filter_js = open(os.path.join(HERE, "filterbar.js"), encoding="utf-8").read()
        chat_js = open(os.path.join(HERE, "chat.js"), encoding="utf-8").read()
        page = page.replace("<script>", f'<script nonce="{nonce}">', 1)
        theme_css = open(os.path.join(HERE, "theme.css"), encoding="utf-8").read()
        body = (head + nav + f'<script nonce="{nonce}">{filter_js}</script>' + page + f"<style>{theme_css}</style>"
                + f'<script nonce="{nonce}">window.__PAGE__={js(current)};{chat_js}</script></body></html>').encode()
        self._headers(200, "text/html; charset=utf-8", nonce=nonce)
        self.wfile.write(body)


TOPNAV_CSS = (
    "body{margin:0}[hidden]{display:none!important}"
    ".topbar{max-width:1280px;margin:0 auto;padding:10px max(16px,env(safe-area-inset-right,0px)) 0 max(16px,env(safe-area-inset-left,0px));"
    "display:grid;grid-template-columns:minmax(0,1fr);gap:10px;font:600 13px system-ui,sans-serif}"
    ".topnav{display:flex;flex-wrap:wrap;gap:6px 18px;align-items:center}"
    ".topnav a{color:#5b6577;text-decoration:none;padding:6px 0;border-bottom:2px solid transparent}"
    ".topnav a[aria-current=page]{color:#2f4fd8;border-color:#2f4fd8}.topnav .so{margin-left:auto}"
    ".filterbar{min-width:0;display:flex;flex-wrap:wrap;gap:8px 16px;align-items:center;padding:10px 12px;border:1px solid #d9dee7;"
    "border-radius:10px;background:#fff;box-shadow:0 1px 2px rgba(23,32,51,.06)}"
    ".filterbar label{display:flex;align-items:center;gap:8px;min-width:0;flex:0 1 auto}"
    ".filterbar label span{font-size:11.5px;letter-spacing:.08em;text-transform:uppercase;color:#5b6577}"
    ".filterbar select{font:500 13.5px system-ui,sans-serif;padding:6px 8px;border:1px solid #d9dee7;border-radius:8px;"
    "background:#f3f5f8;color:#172033;min-width:0;max-width:min(360px,70vw)}"
    ".filterbar select:focus-visible,.filterbar button:focus-visible{outline:2px solid #2f4fd8;outline-offset:1px}"
    ".filterbar button{font:600 12.5px system-ui,sans-serif;padding:6px 10px;border-radius:8px;border:1px solid #d9dee7;"
    "background:transparent;color:#2f4fd8;cursor:pointer}"
    ".filterbar .fnote{margin-left:auto;font-weight:400;font-size:12px;color:#5b6577}"
    "@media (max-width:560px){.filterbar .fnote{display:none}.filterbar label{flex:1 1 100%}.filterbar label span{width:44px;flex:none}.filterbar select{flex:1 1 0;width:0;max-width:none}}"
    "@media (prefers-color-scheme:dark){.topnav a{color:#98a2b6}.topnav a[aria-current=page]{color:#8ea4ff;border-color:#8ea4ff}"
    ".filterbar{background:#161d2c;border-color:#2a3348;box-shadow:none}.filterbar label span,.filterbar .fnote{color:#98a2b6}"
    ".filterbar select{background:#0f1420;border-color:#2a3348;color:#e4e8f1;color-scheme:dark}.filterbar button{border-color:#2a3348;color:#8ea4ff}}"
)


class Server(http.server.ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 64


def main():
    threading.Thread(target=refresher, daemon=True).start()
    srv = Server(("0.0.0.0", PORT), Handler)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(os.path.join(CERT_DIR, "cert.pem"), os.path.join(CERT_DIR, "key.pem"))
    # Handshake later, in the per-connection thread: with the default (handshake inside accept()), one client that
    # connects and never speaks TLS blocks the accept loop and the whole site stops answering.
    srv.socket = ctx.wrap_socket(srv.socket, server_side=True, do_handshake_on_connect=False)
    print(f"dashboard listening on {PORT}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
