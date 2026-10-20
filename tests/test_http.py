"""The HTTP client only ever talks to the configured base."""

import contextlib
import io
import json
import socket
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

from tests import util
from omatrade import auth, cli, daemon
from omatrade.http import ApiClient, ApiError, HostViolation, OfflineError, build_opener
from mock_server import MockServer


class Recorder(BaseHTTPRequestHandler):
    hits = []

    def do_GET(self):
        Recorder.hits.append(self.path)
        target = getattr(self.server, "redirect_to", None)
        if target:
            self.send_response(302)
            self.send_header("Location", target)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        body = getattr(self.server, "body", b"{}")
        self.send_response(getattr(self.server, "status", 200))
        self.send_header("Content-Type", "application/json")
        for k, v in getattr(self.server, "headers_out", {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def serve(**attrs):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Recorder)
    for k, v in attrs.items():
        setattr(srv, k, v)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def stop(srv):
    srv.shutdown()
    srv.server_close()


class UrlBuilding(util.TempDirs):
    def setUp(self):
        super().setUp()
        self.client = ApiClient(self.make_config("https://api.test.invalid/base"))

    def test_paths_join_onto_base(self):
        self.assertEqual(self.client.url("/v1/me"), "https://api.test.invalid/base/v1/me")
        self.assertEqual(self.client.url("/v1/x", {"q": "a b", "n": None}), "https://api.test.invalid/base/v1/x?q=a+b")

    def test_anything_that_could_leave_the_base_is_refused(self):
        for bad in ["https://evil.test/v1", "//evil.test/v1", "v1/me", "/v1/../../x", "/v1/\\evil",
                    "/@evil.test", "", None]:
            if bad == "/@evil.test":
                # stays a path on the base: the origin check holds
                self.assertEqual(urllib.parse.urlsplit(self.client.url(bad)).hostname, "api.test.invalid")
                continue
            with self.assertRaises(HostViolation, msg=bad):
                self.client.url(bad)

    def test_symbol_segments_are_quoted(self):
        url = self.client.url("/v1/watchlist/items/" + cli.seg("NASDAQ:BRK.B/../x"))
        self.assertEqual(urllib.parse.urlsplit(url).path, "/base/v1/watchlist/items/NASDAQ%3ABRK.B%2F..%2Fx")


class Responses(util.TempDirs):
    def client(self, srv):
        return ApiClient(self.make_config(f"http://127.0.0.1:{srv.server_address[1]}", allow_http=True))

    def test_redirects_are_refused_and_never_followed(self):
        Recorder.hits = []
        evil = serve()
        self.addCleanup(stop, evil)
        origin = serve(redirect_to=f"http://127.0.0.1:{evil.server_address[1]}/steal")
        self.addCleanup(stop, origin)
        with self.assertRaises(ApiError) as e:
            self.client(origin).call("GET", "/v1/me")
        self.assertEqual(e.exception.code, "redirect_refused")
        self.assertEqual(Recorder.hits, ["/v1/me"])  # never /steal

    def test_rate_limit_headers(self):
        srv = serve(status=429, body=b'{"error":{"code":"rate_limited","message":"slow"}}',
                    headers_out={"Retry-After": "7", "RateLimit-Limit": "60", "RateLimit-Remaining": "0", "RateLimit-Reset": "30"})
        self.addCleanup(stop, srv)
        with self.assertRaises(ApiError) as e:
            self.client(srv).call("GET", "/v1/public/signals", auth="none")
        err = e.exception
        self.assertEqual((err.status, err.code, err.retry_after), (429, "rate_limited", 7))
        self.assertEqual(err.rate_limit, {"limit": 60, "remaining": 0, "reset": 30})

    def test_malformed_and_oversized(self):
        srv = serve(body=b"<html>")
        self.addCleanup(stop, srv)
        with self.assertRaises(ApiError) as e:
            self.client(srv).call("GET", "/v1/x", auth="none")
        self.assertEqual(e.exception.code, "malformed_response")
        big = serve(body=b'"' + b"a" * (2 * 1024 * 1024 + 10) + b'"')
        self.addCleanup(stop, big)
        with self.assertRaises(ApiError) as e:
            self.client(big).call("GET", "/v1/x", auth="none")
        self.assertEqual(e.exception.code, "malformed_response")

    def test_offline(self):
        c = ApiClient(self.make_config("http://127.0.0.1:9", allow_http=True))
        with self.assertRaises(OfflineError):
            c.call("GET", "/v1/public/signals", auth="none")

    def test_required_auth_without_session(self):
        from omatrade.http import NotSignedIn
        c = ApiClient(self.make_config(), tokens=None)
        with self.assertRaises(NotSignedIn):
            c.call("GET", "/v1/me", auth="required")


class NoForeignHosts(util.TempDirs):
    """Drive a whole session in-process (sign-in, every CLI command, the
    daemon's sync and stream) and record every outbound TCP connection.
    Only the configured base may appear, plus the loopback redirect that the
    simulated browser (not Omatrade) visits."""

    def test_every_connection_goes_to_the_base(self):
        server = MockServer().start()
        self.addCleanup(server.stop)
        base_port = server.server_address[1]
        cfg = self.make_config(server.base, allow_http=True)
        keyring = util.MemoryKeyring()
        tokens = auth.TokenManager(cfg, keyring)
        recorder = util.RecordingOpener(build_opener())
        client = ApiClient(cfg, tokens, opener=recorder)

        connects = []
        browser_threads = set()
        real_connect = socket.socket.connect

        def spy(sock, address):
            if sock.family in (socket.AF_INET, socket.AF_INET6) and threading.get_ident() not in browser_threads:
                connects.append(address[:2])
            return real_connect(sock, address)

        def browser(url):
            def visit():
                browser_threads.add(threading.get_ident())
                urllib.request.urlopen(url).read()
            threading.Thread(target=visit, daemon=True).start()

        with mock.patch.object(socket.socket, "connect", spy):
            auth.login(cfg, client, tokens, opener=browser, timeout=5)
            out = io.StringIO()
            with mock.patch.object(cli, "context", lambda: (cfg, tokens, client)), \
                    contextlib.redirect_stdout(out), mock.patch.object(cli, "nudge", lambda *a: None):
                for argv in (["whoami", "--json"], ["signals", "--json"], ["positions", "--json"],
                             ["watchlist", "--json"], ["watchlist", "search", "app", "--json"],
                             ["watchlist", "add", "NASDAQ:AMZN", "--json"], ["watchlist", "remove", "NASDAQ:AMZN", "--json"],
                             ["skills", "subscribe", "skill_funding", "--json"], ["rules", "--json"],
                             ["rules", "add", "btc", "funding", "--yes", "--json"], ["ack", "sig_old", "--json"]):
                    self.assertEqual(cli.main(argv), 0, argv)
            d = daemon.Daemon(cfg, client, tokens, notifier=lambda s: None, poke=lambda: None)
            d.load()
            d.sync()
            t = threading.Thread(target=d.stream_loop, daemon=True)
            t.start()
            time.sleep(0.5)
            server.state.push("signal", {"id": "sig_live", "title": "x", "tier": "info"})
            deadline = time.time() + 3
            while time.time() < deadline:
                if any(k == "event" for k, _ in list(d.q.queue)):
                    break
                time.sleep(0.05)
            d.stop_event.set()
            d.reconnect_stream()
            t.join(3)
            tokens.logout(client)

        self.assertTrue(recorder.urls)
        hosts = {urllib.parse.urlsplit(u).netloc for u in recorder.urls}
        self.assertEqual(hosts, {f"127.0.0.1:{base_port}"})
        self.assertTrue(connects)
        self.assertEqual({c for c in connects}, {("127.0.0.1", base_port)})
        paths = {urllib.parse.urlsplit(u).path for u in recorder.urls}
        self.assertIn("/v1/stream", paths)
        self.assertIn("/oauth/revoke", paths)


class StaticChecks(util.TempDirs):
    """No third-party market data endpoints or spoofed headers remain."""

    def shipped_files(self):
        root = util.ROOT
        files = list((root / "lib").rglob("*.py")) + [root / "bin/omatrade"] + list(root.glob("*.qml")) + \
            list(root.glob("*.js")) + [root / "manifest.json"]
        return [f for f in files if f.is_file()]

    def test_no_tradingview_or_origin_spoofing(self):
        for f in self.shipped_files():
            text = f.read_text().lower()
            self.assertNotIn("tradingview", text, f)
            self.assertNotIn('"origin"', text, f)

    def test_no_hardcoded_hosts_in_code(self):
        import re
        allowed = {"api.example.invalid", "app.example.invalid", "127.0.0.1", "localhost"}
        for f in self.shipped_files():
            for host in re.findall(r"https?://([A-Za-z0-9.\-]+)", f.read_text()):
                self.assertIn(host, allowed | {"api.questflow.example", "app.questflow.example"}, (f, host))


if __name__ == "__main__":
    import unittest
    unittest.main()
