"""Black-box: run bin/omatrade as a subprocess against the mock Questflow
server, with test doubles for secret-tool, notify-send and xdg-open on PATH."""

import json
import os
import re
import subprocess
import urllib.request

from tests import util
from mock_server import MockServer

CLI = str(util.ROOT / "bin" / "omatrade")


class E2E(util.TempDirs):
    def setUp(self):
        super().setUp()
        self.server = MockServer().start()
        self.addCleanup(self.server.stop)
        self.env = util.mock_env(self.server.base, self.tmp)

    def run_cli(self, *args, env=None, input=None, timeout=30):
        return subprocess.run([CLI, *args], env=env or self.env, capture_output=True, text=True,
                              input=input, timeout=timeout)

    def json_cli(self, *args, **kw):
        r = self.run_cli(*args, "--json", **kw)
        return r, json.loads(r.stdout) if r.stdout.strip() else None

    def login(self, visit=None):
        p = subprocess.Popen([CLI, "login", "--no-browser", "--json", "--timeout", "10"], env=self.env,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        url = None
        for line in p.stderr:
            m = re.search(r"(http://127\.0\.0\.1:\d+/oauth/authorize\?\S+)", line)
            if m:
                url = m.group(1)
                break
        self.assertIsNotNone(url, "login never printed an authorize URL")
        (visit or (lambda u: urllib.request.urlopen(u).read()))(url)
        out, err = p.communicate(timeout=15)
        return p.returncode, json.loads(out) if out.strip() else None, url

    def keyring_db(self):
        try:
            return json.loads((self.tmp / "keyring.json").read_text())
        except OSError:
            return {}


class SignInAndUse(E2E):
    def test_whole_journey(self):
        # signed out: public data works without an account
        r, out = self.json_cli("signals")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(out["personal"])
        self.assertEqual(out["disclaimer"][:22], "Not investment advice.")
        r, out = self.json_cli("watchlist")
        self.assertEqual(out["source"], "default")
        r, out = self.json_cli("positions")
        self.assertEqual((r.returncode, out["code"]), (3, "not_signed_in"))

        code, out, url = self.login()
        self.assertEqual(code, 0)
        self.assertEqual(out["displayName"], "Test User")
        self.assertIn("code_challenge_method=S256", url)
        self.assertNotIn("client_secret", url)

        # tokens only in the keyring, never on argv, never on disk
        session = json.loads(next(iter(self.keyring_db().values())))
        argv = (self.tmp / "keyring.json.argv").read_text()
        self.assertNotIn(session["access_token"], argv)
        self.assertNotIn(session["refresh_token"], argv)
        for f in (self.tmp / "state").rglob("*"):
            if f.is_file():
                self.assertNotIn(session["access_token"], f.read_text(errors="ignore"))

        r, out = self.json_cli("whoami")
        self.assertEqual((out["signedIn"], out["displayName"]), (True, "Test User"))
        self.assertEqual(sorted(out["scopes"]), ["read:positions", "read:profile", "read:signals"])

        r, out = self.json_cli("positions")
        self.assertEqual(r.returncode, 0, r.stderr)
        eth = [p for a in out["accounts"] for p in a["positions"] if p["display"] == "ETH"][0]
        self.assertEqual((eth["distanceToLiqPct"], eth["risk"]), (4.53, "danger"))

        r, out = self.json_cli("watchlist", "add", "nasdaq:amzn")
        self.assertEqual(out["sections"][0]["entries"][0]["symbol"], "NASDAQ:AMZN")
        r, out = self.json_cli("watchlist", "remove", "NASDAQ:AMZN")
        self.assertNotIn("NASDAQ:AMZN", [e["symbol"] for e in out["sections"][0]["entries"]])
        r, out = self.json_cli("watchlist", "add", "$(touch /tmp/pwned)")
        self.assertEqual(out["code"], "bad_symbol")

        r, out = self.json_cli("skills", "subscribe", "skill_funding")
        self.assertTrue([s for s in out["skills"] if s["id"] == "skill_funding"][0]["subscribed"])

        r, out = self.json_cli("signals", "--unread")
        self.assertEqual([s["id"] for s in out["items"]], ["sig_old"])
        r, out = self.json_cli("ack", "sig_old")
        self.assertEqual(out["acked"], ["sig_old"])
        r, out = self.json_cli("signals", "--unread")
        self.assertEqual(out["items"], [])

        r = self.run_cli("logout")
        self.assertEqual(r.returncode, 0)
        self.assertEqual(self.keyring_db(), {})
        self.assertIn("refresh_token", self.server.state.revoked)
        r, out = self.json_cli("whoami")
        self.assertEqual(out["state"], "signed_out")


class Rules(E2E):
    def setUp(self):
        super().setUp()
        self.assertEqual(self.login()[0], 0)

    def test_add_requires_confirmation_when_not_a_tty(self):
        r = self.run_cli("rules", "add", "BTC", "funding", "above", "0.05%", input="")
        self.assertEqual(r.returncode, 1)
        self.assertIn("confirmation required", r.stderr)
        self.assertIn("Questflow understood", r.stdout)
        self.assertEqual(len(self.server.state.rules), 1)  # nothing saved

    def test_add_with_yes_and_lifecycle(self):
        r, out = self.json_cli("rules", "add", "BTC funding above 0.05%", "--yes")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        rid = out["rule"]["id"]
        r, out = self.json_cli("rules", "disable", rid)
        self.assertFalse([x for x in out["rules"] if x["id"] == rid][0]["enabled"])
        r, out = self.json_cli("rules", "delete", rid)
        self.assertEqual(out["code"], "confirmation_required")
        r, out = self.json_cli("rules", "delete", rid, "--yes")
        self.assertNotIn(rid, [x["id"] for x in out["rules"]])

    def test_panel_two_step_draft_and_confirm(self):
        r, draft = self.json_cli("rules", "draft", "BTC funding above 0.05%")
        self.assertIn("funding rate 8h pct above 0.05", draft["description"])
        r, out = self.json_cli("rules", "confirm", draft["draftId"], "--digest", "sha256:" + "0" * 64)
        self.assertEqual(out["code"], "digest_mismatch")  # server refuses a rule the user did not see
        r, draft = self.json_cli("rules", "draft", "BTC funding above 0.05%")
        r, out = self.json_cli("rules", "confirm", draft["draftId"], "--digest", draft["digest"])
        self.assertTrue(out["ok"])

    def test_server_draft_with_code_is_refused(self):
        r, out = self.json_cli("rules", "draft", "evil rule")
        self.assertEqual(out["code"], "invalid_rule")
        r, out = self.json_cli("rules", "draft", "nonsense")
        self.assertEqual(out["code"], "unparseable_rule")


class Failures(E2E):
    def test_not_configured(self):
        env = dict(self.env)
        for k in ("OMATRADE_API_BASE", "OMATRADE_CLIENT_ID"):
            env.pop(k)
        r = self.run_cli("login", env=env)
        self.assertEqual(r.returncode, 2)
        self.assertIn("not configured", r.stderr)

    def test_plain_http_without_override_is_refused(self):
        env = dict(self.env)
        env.pop("OMATRADE_DEV_ALLOW_HTTP_LOCALHOST")
        r = self.run_cli("signals", env=env)
        self.assertEqual(r.returncode, 2)
        self.assertIn("OMATRADE_DEV_ALLOW_HTTP_LOCALHOST", r.stderr)

    def test_offline(self):
        env = dict(self.env, OMATRADE_API_BASE="http://127.0.0.1:9")
        r, out = self.json_cli("signals", env=env)
        self.assertEqual((r.returncode, out["code"]), (5, "offline"))

    def test_rate_limited(self):
        self.server.state.rate_limit = 1
        r, out = self.json_cli("signals")
        self.assertEqual((r.returncode, out["code"]), (6, "rate_limited"))
        self.assertIn("2s", out["error"])

    def test_malformed(self):
        self.server.state.malformed.add("/v1/public/signals")
        r, out = self.json_cli("signals")
        self.assertEqual((r.returncode, out["code"]), (7, "malformed_response"))

    def test_401_refresh_then_expired(self):
        self.assertEqual(self.login()[0], 0)
        self.server.state.access.clear()  # access tokens revoked server-side: refresh recovers
        r, out = self.json_cli("whoami")
        self.assertEqual(out["displayName"], "Test User")
        self.server.state.force_401 = True
        r, out = self.json_cli("positions")
        self.assertEqual((r.returncode, out["code"]), (3, "session_expired"))
        self.assertEqual(self.keyring_db(), {})
        r, out = self.json_cli("whoami")
        self.assertEqual(out["state"], "expired")

    def test_no_keyring_fails_closed(self):
        env = dict(self.env, FAKE_KEYRING_FAIL="1")
        r, out = self.json_cli("login", env=env)
        self.assertEqual((r.returncode, out["code"]), (4, "keyring_unavailable"))
        self.assertIn("never written to disk", out["error"])
        self.assertFalse([p for _, p, _, _ in self.server.state.log if p.startswith("/oauth")])
        env["PATH"] = "/usr/bin:/bin"  # no secret-tool at all
        r, out = self.json_cli("login", env=env)
        self.assertEqual(out["code"], "keyring_unavailable")

    def test_state_mismatch_from_a_forged_callback(self):
        def forged(url):
            # a foreign page hitting the loopback with its own code and state
            q = dict(p.split("=", 1) for p in url.split("?", 1)[1].split("&"))
            redirect = urllib.parse.unquote(q["redirect_uri"])
            urllib.request.urlopen(redirect + "?code=forged&state=forged").read()
        import urllib.parse
        code, out, _ = self.login(visit=forged)
        self.assertEqual((code, out["code"]), (3, "auth"))
        self.assertIn("state mismatch", out["error"])
        self.assertEqual(self.keyring_db(), {})

    def test_open_refuses_non_https(self):
        r = self.run_cli("open", "file:///etc/passwd")
        self.assertEqual(r.returncode, 0)
        self.assertEqual((self.tmp / "open.log").read_text().strip(), "https://app.test.invalid")

    def test_summary_without_daemon(self):
        r, out = self.json_cli("summary")
        self.assertEqual(out["version"], 2)
        self.assertFalse(out["daemon"]["running"])
        self.assertTrue(out["stale"])
