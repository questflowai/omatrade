"""Sign in with Questflow: OAuth 2.0 Authorization Code with PKCE (RFC 7636)
and a loopback redirect (RFC 8252), for a public client with no secret.

1. A fresh `code_verifier` and `state` are made for every attempt.
2. A one-shot HTTP listener binds 127.0.0.1 on an ephemeral port; that is
   the redirect URI.
3. The browser opens the authorize URL on the configured API base.
4. The callback must carry the exact `state` we sent (constant-time compare)
   or it is rejected.
5. The code is exchanged with the verifier. The granted scopes must be a
   subset of the ones we asked for.
6. Tokens go to the system keyring. If that fails, the refresh token is
   revoked and sign-in fails: nothing is kept on disk.
"""

import base64
import contextlib
import fcntl
import hashlib
import hmac
import html
import http.server
import json
import os
import secrets
import threading
import time
import urllib.parse

from . import state as statefile
from .http import ApiError, OfflineError
from .keyring import KeyringUnavailable

# Refresh this long before the access token runs out, so a request in flight
# does not cross the expiry.
EXPIRY_SKEW = 60
DEFAULT_EXPIRES_IN = 3600


class AuthError(Exception):
    pass


# ── PKCE and state ──────────────────────────────────────────────────────────

def b64url(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def make_pkce():
    """(verifier, S256 challenge). 64 characters from the unreserved set,
    inside RFC 7636's 43..128."""
    verifier = secrets.token_urlsafe(48)
    challenge = b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


def make_state():
    return secrets.token_urlsafe(32)


def authorize_url(client, config, redirect_uri, challenge, state):
    return client.url("/oauth/authorize", {
        "response_type": "code",
        "client_id": config.client_id,
        "redirect_uri": redirect_uri,
        "scope": " ".join(config.scopes),
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    })


def validate_callback(params, expected_state):
    """The authorization code, if this callback is the answer to our request."""
    got_state = params.get("state") or ""
    if not got_state or not hmac.compare_digest(got_state.encode(), expected_state.encode()):
        raise AuthError("sign-in answer did not match this request (state mismatch); try again")
    if params.get("error"):
        detail = params.get("error_description") or params["error"]
        raise AuthError(f"sign-in was not completed: {detail[:200]}")
    code = params.get("code") or ""
    if not code or len(code) > 2048:
        raise AuthError("sign-in answer had no authorization code")
    return code


# ── Loopback redirect ───────────────────────────────────────────────────────

PAGE = """<!doctype html><meta charset="utf-8"><title>Omatrade</title>
<body style="font:16px system-ui;margin:4em auto;max-width:28em">
<h1>{title}</h1><p>{body}</p></body>"""


class LoopbackReceiver:
    """A one-shot listener on 127.0.0.1 for the OAuth redirect."""

    def __init__(self, host="127.0.0.1", path="/callback"):
        self.host = host
        self.path = path
        self.result = None
        self._done = threading.Event()
        receiver = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                parts = urllib.parse.urlsplit(self.path)
                if parts.path != receiver.path or receiver._done.is_set():
                    self.send_error(404)
                    return
                params = {k: v[0] for k, v in urllib.parse.parse_qs(parts.query).items()}
                if not params.get("state"):
                    self.send_error(400)
                    return
                ok = "code" in params and "error" not in params
                page = PAGE.format(
                    title="Signed in" if ok else "Sign-in not completed",
                    body=("You can close this tab and go back to Omatrade." if ok else
                          html.escape(params.get("error_description") or params.get("error") or "")))
                body = page.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Referrer-Policy", "no-referrer")
                self.end_headers()
                self.wfile.write(body)
                receiver.result = params
                receiver._done.set()

            def log_message(self, *args):
                pass

        self._server = http.server.HTTPServer((host, 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever,
                                        kwargs={"poll_interval": 0.1}, daemon=True)

    @property
    def redirect_uri(self):
        return f"http://{self.host}:{self._server.server_address[1]}{self.path}"

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._server.shutdown()
        self._server.server_close()

    def wait(self, timeout):
        if not self._done.wait(timeout):
            raise AuthError("timed out waiting for the browser to finish signing in")
        return self.result


# ── Token responses ─────────────────────────────────────────────────────────

def parse_token_response(raw, requested_scopes, now, previous=None):
    if not isinstance(raw, dict):
        raise AuthError("token endpoint answered with something unexpected")
    access = raw.get("access_token")
    if not isinstance(access, str) or not access or len(access) > 8192:
        raise AuthError("token endpoint answered without an access token")
    if str(raw.get("token_type", "")).lower() != "bearer":
        raise AuthError("token endpoint answered with a token type other than Bearer")
    try:
        expires_in = int(raw.get("expires_in", DEFAULT_EXPIRES_IN))
    except (TypeError, ValueError):
        expires_in = DEFAULT_EXPIRES_IN
    expires_in = max(30, min(expires_in, 24 * 3600))
    refresh = raw.get("refresh_token")
    if refresh is not None and (not isinstance(refresh, str) or len(refresh) > 8192):
        raise AuthError("token endpoint answered with an unreadable refresh token")
    if refresh is None and previous:
        refresh = previous.get("refresh_token")
    granted = raw.get("scope")
    granted = set(granted.split()) if isinstance(granted, str) and granted.strip() else set(requested_scopes)
    extra = granted - set(requested_scopes)
    if extra:
        raise AuthError("server granted scopes this client never asks for: " + ", ".join(sorted(extra)))
    return {
        "access_token": access,
        "refresh_token": refresh,
        "expires_at": int(now + expires_in),
        "scope": " ".join(sorted(granted)),
        "token_type": "Bearer",
    }


# ── Token manager ───────────────────────────────────────────────────────────

class TokenManager:
    """Owns the session. The daemon and the CLI may both refresh, so the
    refresh is serialised with a file lock and re-reads the keyring once it
    holds it: refresh tokens rotate, and the loser of a race must use the
    winner's tokens instead of spending a token that is no longer valid."""

    def __init__(self, config, keyring, clock=time.time):
        self.config = config
        self.keyring = keyring
        self.clock = clock
        self._cache = None
        self._lock = threading.Lock()

    @property
    def attrs(self):
        return {"application": "omatrade", "api": self.config.api_base, "kind": "oauth-session"}

    def _marker(self):
        return self.config.state_dir / "auth.json"

    def status(self):
        """Not secret: whether we are signed in, and why not if not."""
        marker = statefile.load_json(self._marker(), {})
        try:
            tokens = self.load()
        except KeyringUnavailable:
            return {"state": "keyring_unavailable", "signedIn": False}
        if tokens:
            return {"state": "signed_in", "signedIn": True, "scopes": tokens.get("scope", "").split(),
                    "expiresAt": tokens.get("expires_at")}
        return {"state": marker.get("state", "signed_out"), "signedIn": False}

    def load(self, fresh=False):
        if self._cache is not None and not fresh:
            return self._cache
        raw = self.keyring.lookup(self.attrs)
        if not raw:
            self._cache = None
            return None
        try:
            tokens = json.loads(raw)
        except ValueError:
            tokens = None
        if not isinstance(tokens, dict) or not tokens.get("access_token"):
            self._cache = None
            return None
        self._cache = tokens
        return tokens

    def save(self, tokens):
        self.keyring.store(self.attrs, "Omatrade Questflow session", json.dumps(tokens))
        self._cache = tokens
        statefile.save_json(self._marker(), {"state": "signed_in", "at": int(self.clock())})

    def expire(self, reason="expired"):
        try:
            self.keyring.clear(self.attrs)
        except KeyringUnavailable:
            pass
        self._cache = None
        statefile.save_json(self._marker(), {"state": reason, "at": int(self.clock())})

    def valid(self, tokens):
        return bool(tokens) and tokens.get("expires_at", 0) - EXPIRY_SKEW > self.clock()

    def access_token(self, client):
        try:
            tokens = self.load()
        except KeyringUnavailable:
            return None
        if not tokens:
            return None
        if self.valid(tokens):
            return tokens["access_token"]
        return self.refresh(client)

    @contextlib.contextmanager
    def _file_lock(self):
        path = self.config.state_dir / "auth.lock"
        statefile.ensure_dir(path.parent)
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def refresh(self, client, force=False, rejected=None):
        with self._lock, self._file_lock():
            tokens = self.load(fresh=True)
            if not tokens:
                return None
            if self.valid(tokens) and (not force or tokens["access_token"] != rejected):
                return tokens["access_token"]
            if not tokens.get("refresh_token"):
                self.expire()
                return None
            try:
                raw = client.form("/oauth/token", {
                    "grant_type": "refresh_token",
                    "refresh_token": tokens["refresh_token"],
                    "client_id": self.config.client_id,
                })
            except ApiError as e:
                if e.status in (400, 401):
                    self.expire()
                    return None
                raise
            except OfflineError:
                raise
            try:
                fresh = parse_token_response(raw, self.config.scopes, self.clock(), previous=tokens)
            except AuthError:
                self.expire()
                return None
            self.save(fresh)
            return fresh["access_token"]

    def revoke(self, client, tokens):
        """RFC 7009, best effort: the refresh token first, as it is the one
        that outlives this session."""
        errors = []
        for kind in ("refresh_token", "access_token"):
            value = (tokens or {}).get(kind)
            if not value:
                continue
            try:
                client.form("/oauth/revoke", {"token": value, "token_type_hint": kind,
                                              "client_id": self.config.client_id})
            except (ApiError, OfflineError) as e:
                errors.append(f"{kind}: {e}")
        return errors

    def logout(self, client):
        try:
            tokens = self.load(fresh=True)
        except KeyringUnavailable:
            tokens = None
        errors = self.revoke(client, tokens) if tokens else []
        self.expire("signed_out")
        return errors


# ── The whole flow ──────────────────────────────────────────────────────────

def login(config, client, tokens, *, opener=None, announce=None, timeout=300, clock=time.time):
    """Run the browser sign-in. `opener(url)` opens the browser (None: just
    announce the URL); `announce(url)` tells the user where to go."""
    tokens.keyring.probe()  # fail closed before the browser opens
    verifier, challenge = make_pkce()
    state = make_state()
    with LoopbackReceiver() as receiver:
        url = authorize_url(client, config, receiver.redirect_uri, challenge, state)
        if announce:
            announce(url)
        if opener:
            opener(url)
        params = receiver.wait(timeout)
        redirect_uri = receiver.redirect_uri
    code = validate_callback(params, state)
    try:
        raw = client.form("/oauth/token", {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": config.client_id,
            "code_verifier": verifier,
        })
    except ApiError as e:
        raise AuthError(f"Questflow did not accept the sign-in ({e.code})") from None
    session = parse_token_response(raw, config.scopes, clock())
    try:
        tokens.save(session)
    except KeyringUnavailable:
        tokens.revoke(client, session)
        raise
    return session
