import base64
import hashlib
import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from tests import util
from omatrade import auth
from omatrade.http import ApiClient, ApiError, AuthExpired, OfflineError
from omatrade.keyring import KeyringUnavailable
from mock_server import MockServer


class Pkce(util.TempDirs):
    def test_verifier_shape_and_s256_challenge(self):
        seen = set()
        for _ in range(50):
            verifier, challenge = auth.make_pkce()
            self.assertTrue(43 <= len(verifier) <= 128)
            self.assertRegex(verifier, r"^[A-Za-z0-9._~-]+$")
            expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
            self.assertEqual(challenge, expected)
            self.assertNotIn("=", challenge)
            seen.add(verifier)
        self.assertEqual(len(seen), 50)

    def test_state_is_random(self):
        self.assertNotEqual(auth.make_state(), auth.make_state())
        self.assertGreaterEqual(len(auth.make_state()), 40)

    def test_authorize_url_is_on_the_base_and_complete(self):
        cfg = self.make_config()
        client = ApiClient(cfg)
        url = auth.authorize_url(client, cfg, "http://127.0.0.1:5555/callback", "CH", "ST")
        parts = urllib.parse.urlsplit(url)
        self.assertEqual(parts.netloc, "api.test.invalid")
        q = dict(urllib.parse.parse_qsl(parts.query))
        self.assertEqual(q["response_type"], "code")
        self.assertEqual(q["code_challenge_method"], "S256")
        self.assertEqual(q["scope"], "read:profile read:signals read:positions")
        self.assertEqual(q["state"], "ST")
        self.assertNotIn("client_secret", q)


class StateCheck(util.TempDirs):
    def test_matching_state_returns_code(self):
        self.assertEqual(auth.validate_callback({"state": "abc", "code": "c"}, "abc"), "c")

    def test_mismatch_missing_and_error(self):
        with self.assertRaisesRegex(auth.AuthError, "state mismatch"):
            auth.validate_callback({"state": "abd", "code": "c"}, "abc")
        with self.assertRaisesRegex(auth.AuthError, "state mismatch"):
            auth.validate_callback({"code": "c"}, "abc")
        with self.assertRaisesRegex(auth.AuthError, "not completed"):
            auth.validate_callback({"state": "abc", "error": "access_denied"}, "abc")
        with self.assertRaisesRegex(auth.AuthError, "no authorization code"):
            auth.validate_callback({"state": "abc"}, "abc")

    def test_loopback_receiver(self):
        with auth.LoopbackReceiver() as rx:
            self.assertTrue(rx.redirect_uri.startswith("http://127.0.0.1:"))
            base = rx.redirect_uri.rsplit("/", 1)[0]
            with self.assertRaises(urllib.error.HTTPError) as e:
                urllib.request.urlopen(base + "/favicon.ico")
            self.assertEqual(e.exception.code, 404)
            with self.assertRaises(urllib.error.HTTPError):
                urllib.request.urlopen(rx.redirect_uri + "?code=x")  # no state
            page = urllib.request.urlopen(rx.redirect_uri + "?code=x&state=y").read().decode()
            self.assertIn("Signed in", page)
            self.assertEqual(rx.wait(1), {"code": "x", "state": "y"})
            with self.assertRaises(urllib.error.HTTPError):  # one shot
                urllib.request.urlopen(rx.redirect_uri + "?code=z&state=y")

    def test_loopback_timeout(self):
        with auth.LoopbackReceiver() as rx:
            with self.assertRaisesRegex(auth.AuthError, "timed out"):
                rx.wait(0.2)


class TokenResponse(util.TempDirs):
    SCOPES = ("read:profile", "read:signals", "read:positions")

    def test_good(self):
        t = auth.parse_token_response({"access_token": "a", "refresh_token": "r", "token_type": "bearer",
                                       "expires_in": 600, "scope": "read:profile read:signals"}, self.SCOPES, 1000)
        self.assertEqual(t["expires_at"], 1600)
        self.assertEqual(t["scope"], "read:profile read:signals")

    def test_rejects_extra_scope_and_wrong_type(self):
        with self.assertRaisesRegex(auth.AuthError, "never asks for"):
            auth.parse_token_response({"access_token": "a", "token_type": "Bearer", "scope": "read:profile write:trading"},
                                      self.SCOPES, 0)
        with self.assertRaises(auth.AuthError):
            auth.parse_token_response({"access_token": "a", "token_type": "mac"}, self.SCOPES, 0)
        with self.assertRaises(auth.AuthError):
            auth.parse_token_response({"token_type": "Bearer"}, self.SCOPES, 0)

    def test_clamps_expiry_and_keeps_previous_refresh(self):
        t = auth.parse_token_response({"access_token": "a", "token_type": "Bearer", "expires_in": 10 ** 9},
                                      self.SCOPES, 0, previous={"refresh_token": "old"})
        self.assertEqual(t["expires_at"], 24 * 3600)
        self.assertEqual(t["refresh_token"], "old")


class Clock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


class TokenManagerAgainstMock(util.TempDirs):
    def setUp(self):
        super().setUp()
        self.server = MockServer().start()
        self.addCleanup(self.server.stop)
        self.cfg = self.make_config(self.server.base, allow_http=True)
        self.keyring = util.MemoryKeyring()
        self.clock = Clock(time.time())
        self.tokens = auth.TokenManager(self.cfg, self.keyring, clock=self.clock)
        self.client = ApiClient(self.cfg, self.tokens)

    def login(self):
        def opener(url):
            threading.Thread(target=lambda: urllib.request.urlopen(url).read(), daemon=True).start()
        return auth.login(self.cfg, self.client, self.tokens, opener=opener, timeout=5, clock=self.clock)

    def test_full_login_stores_only_in_keyring(self):
        session = self.login()
        self.assertTrue(session["access_token"])
        stored = json.loads(next(iter(self.keyring.items.values())))
        self.assertEqual(stored["access_token"], session["access_token"])
        # nothing secret on disk
        for f in self.cfg.state_dir.rglob("*"):
            if f.is_file():
                body = f.read_text(errors="ignore")
                self.assertNotIn(session["access_token"], body)
                self.assertNotIn(session["refresh_token"], body)

    def test_login_rejects_tampered_state(self):
        def opener(url):
            bad = re.sub(r"state=[^&]+", "state=forged", url)
            threading.Thread(target=lambda: urllib.request.urlopen(bad).read(), daemon=True).start()
        with self.assertRaisesRegex(auth.AuthError, "state mismatch"):
            auth.login(self.cfg, self.client, self.tokens, opener=opener, timeout=5)
        self.assertEqual(self.keyring.items, {})

    def test_login_denied(self):
        self.server.state.authorize_error = "access_denied"
        with self.assertRaisesRegex(auth.AuthError, "access_denied"):
            self.login()

    def test_login_rejects_overbroad_grant_and_stores_nothing(self):
        self.server.state.grant_extra_scope = True
        with self.assertRaisesRegex(auth.AuthError, "write:trading"):
            self.login()
        self.assertEqual(self.keyring.items, {})

    def test_login_fails_closed_without_keyring_before_browser(self):
        self.keyring.fail = True
        opened = []
        with self.assertRaises(KeyringUnavailable):
            auth.login(self.cfg, self.client, self.tokens, opener=opened.append, timeout=1)
        self.assertEqual(opened, [])

    def test_keyring_store_failure_revokes(self):
        tokens = auth.TokenManager(self.cfg, self.keyring)
        orig_store = self.keyring.store

        def failing_store(*a):
            raise KeyringUnavailable("locked")
        self.keyring.store = failing_store
        client = ApiClient(self.cfg, tokens)

        def opener(url):
            threading.Thread(target=lambda: urllib.request.urlopen(url).read(), daemon=True).start()
        with self.assertRaises(KeyringUnavailable):
            auth.login(self.cfg, client, tokens, opener=opener, timeout=5)
        self.keyring.store = orig_store
        self.assertIn("refresh_token", self.server.state.revoked)
        self.assertEqual(self.server.state.refresh, {})

    def test_valid_token_is_not_refreshed(self):
        session = self.login()
        self.assertEqual(self.tokens.access_token(self.client), session["access_token"])
        self.assertEqual(len(self.server.state.refresh), 1)

    def test_expiry_triggers_refresh_and_rotation(self):
        session = self.login()
        self.clock.t += 3600 - 30  # inside the skew window
        fresh = self.tokens.access_token(self.client)
        self.assertNotEqual(fresh, session["access_token"])
        self.assertNotIn(session["refresh_token"], self.server.state.refresh)  # rotated
        stored = json.loads(next(iter(self.keyring.items.values())))
        self.assertEqual(stored["access_token"], fresh)

    def test_401_refreshes_once_and_retries(self):
        session = self.login()
        self.server.state.access.pop(session["access_token"])  # server-side revocation of access token
        me = self.client.call("GET", "/v1/me", auth="required")
        self.assertEqual(me["display_name"], "Test User")

    def test_second_401_expires_session(self):
        self.login()
        self.server.state.force_401 = True
        with self.assertRaises(AuthExpired):
            self.client.call("GET", "/v1/me", auth="required")
        self.assertEqual(self.keyring.items, {})
        self.assertEqual(self.tokens.status()["state"], "expired")

    def test_invalid_grant_expires_session(self):
        self.login()
        self.server.state.refresh_disabled = True
        self.clock.t += 7200
        self.assertIsNone(self.tokens.access_token(self.client))
        self.assertEqual(self.tokens.status()["state"], "expired")

    def test_offline_refresh_keeps_session(self):
        self.login()
        self.clock.t += 7200
        dead = ApiClient(self.make_config("http://127.0.0.1:9", allow_http=True), self.tokens)
        with self.assertRaises(OfflineError):
            self.tokens.refresh(dead)
        self.assertTrue(self.keyring.items)

    def test_refresh_race_uses_winners_tokens(self):
        session = self.login()
        other = auth.TokenManager(self.cfg, self.keyring, clock=self.clock)
        other.load()
        # this process refreshes first (rotation) ...
        self.clock.t += 3600
        winner = self.tokens.refresh(self.client)
        # ... the other, holding the stale access token, must not spend the dead refresh token
        got = other.refresh(ApiClient(self.cfg, other), force=True, rejected=session["access_token"])
        self.assertEqual(got, winner)
        self.assertTrue(self.keyring.items)

    def test_logout_revokes_and_clears(self):
        self.login()
        errors = self.tokens.logout(self.client)
        self.assertEqual(errors, [])
        self.assertEqual(self.server.state.revoked[:2], ["refresh_token", "access_token"])
        self.assertEqual(self.keyring.items, {})
        self.assertEqual(self.tokens.status()["state"], "signed_out")

    def test_logout_offline_still_clears_locally(self):
        self.login()
        dead = ApiClient(self.make_config("http://127.0.0.1:9", allow_http=True), self.tokens)
        errors = self.tokens.logout(dead)
        self.assertTrue(errors)
        self.assertEqual(self.keyring.items, {})


if __name__ == "__main__":
    import unittest
    unittest.main()
