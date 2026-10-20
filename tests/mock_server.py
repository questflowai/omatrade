"""A local stand-in for the Questflow API, built from docs/api-contract.md,
for black-box tests and local development. Standard library only.

    python3 tests/mock_server.py --port 8765
    OMATRADE_API_BASE=http://127.0.0.1:8765 OMATRADE_DEV_ALLOW_HTTP_LOCALHOST=1 \\
    OMATRADE_CLIENT_ID=omatrade-test-client bin/omatrade login --no-browser

Every token and code it issues is random and lives only in memory. Knobs on
`MockState` let tests force 401s, 429s, malformed payloads, revoked
refresh tokens and pushed stream events.
"""

import argparse
import base64
import copy
import hashlib
import json
import secrets
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

CLIENT_ID = "omatrade-test-client"
SCOPES = {"read:profile", "read:signals", "read:positions"}
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def iso(ts):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def b64url(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


class MockState:
    def __init__(self):
        self.lock = threading.RLock()
        self.cond = threading.Condition(self.lock)
        self.codes = {}
        self.access = {}
        self.refresh = {}
        self.revoked = []
        self.access_ttl = 3600
        self.log = []                 # (method, path, has_bearer, host_header)
        self.force_401 = False        # every bearer request answers 401
        self.refresh_disabled = False # refresh_token grant answers invalid_grant
        self.rate_limit = 0           # next N API requests answer 429
        self.malformed = set()        # paths that answer non-JSON
        self.grant_extra_scope = False
        self.authorize_error = None
        self.heartbeat = 0.5
        self.stream_retry_ms = None
        self.events = []              # (id, event, data) history for replay
        self.next_event = 1
        self.stream_connects = []     # Last-Event-ID seen on each connect
        self.stream_closing = False
        now = time.time()
        self.public_watchlist = {"sections": [
            {"name": "Stocks", "entries": [
                {"symbol": "NASDAQ:NVDA", "name": "NVIDIA", "quote": {"last": 186.2, "change": 2.2, "change_pct": 1.2, "currency": "USD"}},
                {"symbol": "NASDAQ:TSLA", "name": "Tesla", "quote": {"last": 268.7, "change": -3.1, "change_pct": -1.14, "currency": "USD"}}]},
            {"name": "Crypto", "entries": [
                {"symbol": "CRYPTO:BTCUSD", "name": "Bitcoin", "quote": {"last": 112450.0, "change": 900.0, "change_pct": 0.81, "currency": "USD"}}]}],
            "editable": False}
        self.watchlist = {"sections": [{"name": "Mine", "entries": [
            {"symbol": "NASDAQ:AAPL", "name": "Apple", "quote": {"last": 231.1, "change": 1.0, "change_pct": 0.43, "currency": "USD"}}]}],
            "editable": True}
        self.symbols = [
            {"symbol": "NASDAQ:AAPL", "name": "Apple", "type": "stock"},
            {"symbol": "NASDAQ:AMZN", "name": "Amazon", "type": "stock"},
            {"symbol": "CRYPTO:ETHUSD", "name": "Ethereum", "type": "crypto"}]
        self.public_signals = [
            {"id": "pub_1", "symbol": "NASDAQ:NVDA", "type": "news", "tier": "info",
             "source": {"kind": "news", "id": "news", "name": "Newswire"},
             "title": "Chipmakers rally into earnings", "created_at": iso(now - 600),
             "url": "https://news.example.com/a"}]
        self.signals = [
            {"id": "sig_old", "symbol": "NASDAQ:AAPL", "type": "rule_match", "tier": "important",
             "source": {"kind": "rule", "id": "rule_1", "name": "My breakout rule"},
             "title": "AAPL crossed above its 20-day average", "created_at": iso(now - 3600),
             "url": "https://app.example.com/signals/sig_old", "strength": 0.7, "read": False, "rule_id": "rule_1"}]
        self.skills = [
            {"id": "skill_whales", "name": "Whale flows", "description": "Large on-chain transfers", "subscribed": True},
            {"id": "skill_funding", "name": "Funding extremes", "description": "Perp funding outliers", "subscribed": False}]
        self.rules = [{"id": "rule_1", "enabled": True, "created_at": iso(now - 86400), "rule": {
            "version": 1, "name": "My breakout rule", "universe": {"watchlist": True},
            "when": {"all": [{"metric": "price_vs_sma", "op": "crosses_above", "value": 20}]},
            "tier": "important", "cooldown_sec": 3600}}]
        self.drafts = {}
        self.positions = json.loads((FIXTURES / "positions.json").read_text())
        self.acked = set()

    # ── tokens ──
    def issue(self, scope):
        access, refresh = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
        self.access[access] = {"exp": time.time() + self.access_ttl, "scope": scope}
        self.refresh[refresh] = {"scope": scope}
        return {"access_token": access, "refresh_token": refresh, "token_type": "Bearer",
                "expires_in": self.access_ttl, "scope": scope + (" write:trading" if self.grant_extra_scope else "")}

    def push(self, event, payload):
        with self.cond:
            eid = str(self.next_event)
            self.next_event += 1
            self.events.append((eid, event, json.dumps(payload) if not isinstance(payload, str) else payload))
            self.cond.notify_all()
            return eid


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "MockQuestflow/1"

    @property
    def st(self):
        return self.server.state

    def log_message(self, *a):
        pass

    def _send(self, status, payload=None, headers=None, raw=None, ctype="application/json"):
        body = raw if raw is not None else (b"" if payload is None else json.dumps(payload).encode())
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("RateLimit-Limit", "120")
        self.send_header("RateLimit-Remaining", "119")
        self.send_header("RateLimit-Reset", "60")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _err(self, status, code, message="", headers=None):
        self._send(status, {"error": {"code": code, "message": message}}, headers)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b""
        if self.headers.get("Content-Type", "").startswith("application/x-www-form-urlencoded"):
            return {k: v[0] for k, v in urllib.parse.parse_qs(raw.decode()).items()}
        try:
            return json.loads(raw or b"{}")
        except ValueError:
            return None

    def _bearer(self):
        h = self.headers.get("Authorization", "")
        return h[7:] if h.startswith("Bearer ") else None

    def _authed(self, scope):
        token = self._bearer()
        info = self.st.access.get(token) if token else None
        if self.st.force_401 or not info or info["exp"] < time.time():
            self._err(401, "invalid_token", "token missing, expired or revoked",
                      {"WWW-Authenticate": 'Bearer error="invalid_token"'})
            return False
        if scope not in info["scope"].split():
            self._err(403, "insufficient_scope", scope)
            return False
        return True

    def do_GET(self):
        self._route("GET")

    def do_POST(self):
        self._route("POST")

    def do_PUT(self):
        self._route("PUT")

    def do_DELETE(self):
        self._route("DELETE")

    def do_PATCH(self):
        self._route("PATCH")

    def _route(self, method):
        parts = urllib.parse.urlsplit(self.path)
        path = parts.path
        query = {k: v[0] for k, v in urllib.parse.parse_qs(parts.query).items()}
        st = self.st
        with st.lock:
            st.log.append((method, path, bool(self._bearer()), self.headers.get("Host", "")))
        if path.startswith("/oauth/"):
            return self._oauth(method, path, query)
        if st.rate_limit > 0:
            st.rate_limit -= 1
            return self._send(429, {"error": {"code": "rate_limited", "message": "slow down"}},
                              {"Retry-After": "2", "RateLimit-Remaining": "0"})
        if path in st.malformed:
            return self._send(200, raw=b"<html>not json", ctype="text/html")
        if path == "/v1/stream" and method == "GET":
            return self._stream()
        return self._api(method, path, query)

    # ── OAuth ──
    def _oauth(self, method, path, q):
        st = self.st
        if path == "/oauth/authorize" and method == "GET":
            redirect = q.get("redirect_uri", "")
            r = urllib.parse.urlsplit(redirect)
            if q.get("client_id") != CLIENT_ID or r.scheme != "http" or r.hostname != "127.0.0.1":
                return self._err(400, "invalid_request", "bad client or redirect")
            if q.get("code_challenge_method") != "S256" or len(q.get("code_challenge", "")) != 43:
                return self._err(400, "invalid_request", "PKCE S256 required")
            scopes = set(q.get("scope", "").split())
            if not scopes or not scopes <= SCOPES:
                return self._err(400, "invalid_scope")
            if st.authorize_error:
                params = {"error": st.authorize_error, "state": q.get("state", "")}
            else:
                code = secrets.token_urlsafe(16)
                st.codes[code] = {"challenge": q["code_challenge"], "redirect": redirect,
                                  "scope": " ".join(sorted(scopes)), "used": False}
                params = {"code": code, "state": q.get("state", "")}
            self.send_response(302)
            self.send_header("Location", redirect + "?" + urllib.parse.urlencode(params))
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if method != "POST":
            return self._err(404, "not_found")
        form = self._body() or {}
        if form.get("client_id") != CLIENT_ID:
            return self._send(401, {"error": "invalid_client"})
        if path == "/oauth/token":
            grant = form.get("grant_type")
            if grant == "authorization_code":
                info = st.codes.get(form.get("code", ""))
                if not info or info["used"] or info["redirect"] != form.get("redirect_uri"):
                    return self._send(400, {"error": "invalid_grant"})
                verifier = form.get("code_verifier", "")
                if b64url(hashlib.sha256(verifier.encode()).digest()) != info["challenge"]:
                    return self._send(400, {"error": "invalid_grant", "error_description": "PKCE mismatch"})
                info["used"] = True
                return self._send(200, st.issue(info["scope"]), {"Cache-Control": "no-store"})
            if grant == "refresh_token":
                info = st.refresh.pop(form.get("refresh_token", ""), None)
                if st.refresh_disabled or not info:
                    return self._send(400, {"error": "invalid_grant"})
                return self._send(200, st.issue(info["scope"]), {"Cache-Control": "no-store"})
            return self._send(400, {"error": "unsupported_grant_type"})
        if path == "/oauth/revoke":
            token = form.get("token", "")
            st.revoked.append(form.get("token_type_hint"))
            st.refresh.pop(token, None)
            st.access.pop(token, None)
            return self._send(200, {})
        return self._err(404, "not_found")

    # ── REST ──
    def _api(self, method, path, q):
        st = self.st
        now = time.time()
        if path == "/v1/public/watchlist":
            return self._send(200, st.public_watchlist)
        if path == "/v1/public/symbols":
            text = q.get("q", "").lower()
            return self._send(200, {"results": [s for s in st.symbols if text in (s["symbol"] + s["name"]).lower()]})
        if path == "/v1/public/signals":
            return self._send(200, {"signals": st.public_signals})
        scope = "read:positions" if path.startswith("/v1/positions") else \
            "read:profile" if path == "/v1/me" else "read:signals"
        if not self._authed(scope):
            return
        if path == "/v1/me":
            return self._send(200, {"id": "usr_test", "display_name": "Test User", "plan": "free"})
        if path == "/v1/watchlist" and method == "GET":
            return self._send(200, st.watchlist)
        if path.startswith("/v1/watchlist/items/"):
            sym = urllib.parse.unquote(path.rsplit("/", 1)[1])
            entries = st.watchlist["sections"][0]["entries"]
            if method == "PUT":
                if not any(e["symbol"] == sym for e in entries):
                    entries.insert(0, {"symbol": sym, "name": sym.split(":")[1], "quote": {"last": 10.0, "change_pct": 0.0}})
                return self._send(200, st.watchlist)
            if method == "DELETE":
                st.watchlist["sections"][0]["entries"] = [e for e in entries if e["symbol"] != sym]
                return self._send(204)
        if path == "/v1/signals" and method == "GET":
            items = [dict(s, read=s.get("read") or s["id"] in st.acked) for s in st.signals]
            return self._send(200, {"signals": items, "next_cursor": None})
        if path == "/v1/signals/ack" and method == "POST":
            body = self._body() or {}
            ids = [i for i in body.get("ids", []) if isinstance(i, str)]
            st.acked.update(ids)
            st.push("ack", {"ids": ids})
            return self._send(200, {"acked": ids})
        if path == "/v1/skills":
            return self._send(200, {"skills": st.skills})
        if path.startswith("/v1/subscriptions/"):
            sid = urllib.parse.unquote(path.rsplit("/", 1)[1])
            for s in st.skills:
                if s["id"] == sid:
                    s["subscribed"] = method == "PUT"
                    return self._send(200, {"skill": s})
            return self._err(404, "not_found", "no such skill")
        if path == "/v1/rules" and method == "GET":
            return self._send(200, {"rules": st.rules})
        if path == "/v1/rules/drafts" and method == "POST":
            body = self._body() or {}
            text = body.get("text", "")
            if "nonsense" in text:
                return self._err(422, "unparseable_rule", "could not understand that rule")
            rule = {"version": 1, "name": text[:40], "universe": {"symbols": ["CRYPTO:BTCUSD"]},
                    "when": {"all": [{"metric": "funding_rate_8h_pct", "op": ">", "value": 0.05}]},
                    "tier": "important", "cooldown_sec": 3600}
            if "evil" in text:
                rule["exec"] = "rm -rf ~"
            draft_id = "draft_" + secrets.token_hex(4)
            st.drafts[draft_id] = rule
            return self._send(201, {"draft": {"id": draft_id, "rule": rule, "summary": "server summary",
                                              "expires_at": iso(now + 600)}})
        if path == "/v1/rules" and method == "POST":
            body = self._body() or {}
            rule = st.drafts.pop(body.get("draft_id", ""), None)
            if rule is None:
                return self._err(410, "draft_expired")
            canon = json.dumps(rule, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
            if body.get("rule_digest") != "sha256:" + hashlib.sha256(canon.encode()).hexdigest():
                return self._err(409, "digest_mismatch")
            rec = {"id": "rule_" + secrets.token_hex(3), "enabled": True, "created_at": iso(now), "rule": rule}
            st.rules.append(rec)
            return self._send(201, {"rule": rec})
        if path.startswith("/v1/rules/"):
            rid = urllib.parse.unquote(path.rsplit("/", 1)[1])
            rec = next((r for r in st.rules if r["id"] == rid), None)
            if rec is None:
                return self._err(404, "not_found")
            if method == "PATCH":
                rec["enabled"] = bool((self._body() or {}).get("enabled"))
                return self._send(200, {"rule": rec})
            if method == "DELETE":
                st.rules.remove(rec)
                return self._send(204)
        if path == "/v1/positions":
            out = copy.deepcopy(st.positions)
            out.pop("_fixture", None)
            for a in out["accounts"]:
                a.setdefault("synced_at", iso(now - 5))
            return self._send(200, out)
        return self._err(404, "not_found")

    # ── SSE ──
    def _stream(self):
        st = self.st
        token = self._bearer()
        if token and not self._authed("read:signals"):
            return
        last = self.headers.get("Last-Event-ID")
        with st.lock:
            st.stream_connects.append(last)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        try:
            if st.stream_retry_ms is not None:
                self.wfile.write(f"retry: {st.stream_retry_ms}\n\n".encode())
            cursor = 0
            if last:
                ids = [e[0] for e in st.events]
                cursor = ids.index(last) + 1 if last in ids else 0
            else:
                cursor = len(st.events)
            self.wfile.write(b": connected\n\n")
            self.wfile.flush()
            while not st.stream_closing and not self.server.closing:
                with st.cond:
                    if cursor >= len(st.events):
                        st.cond.wait(st.heartbeat)
                    pending = st.events[cursor:]
                    cursor = len(st.events)
                if pending:
                    for eid, event, payload in pending:
                        lines = "".join(f"data: {line}\n" for line in payload.split("\n"))
                        self.wfile.write(f"id: {eid}\nevent: {event}\n{lines}\n".encode())
                else:
                    self.wfile.write(b": ping\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass


class MockServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, port=0, state=None):
        self.state = state or MockState()
        self.closing = False
        super().__init__(("127.0.0.1", port), Handler)
        self.thread = threading.Thread(target=self.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)

    @property
    def base(self):
        return f"http://127.0.0.1:{self.server_address[1]}"

    def start(self):
        self.thread.start()
        return self

    def stop(self):
        self.closing = True
        with self.state.cond:
            self.state.cond.notify_all()
        self.shutdown()
        self.server_close()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=8765)
    args = p.parse_args()
    server = MockServer(args.port).start()
    print(f"mock Questflow API on {server.base} (client_id {CLIENT_ID})")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        server.stop()


if __name__ == "__main__":
    main()
