"""The one HTTP client. Every request Omatrade makes goes through here, to
the configured API base and nowhere else.

- Paths only: callers pass `/v1/...`, never a full URL, and the joined URL is
  checked against the base's scheme, host and port before it is sent.
- Redirects are refused, so a bearer token can never follow a 30x to another
  host.
- Responses are size-capped and parsed defensively; anything unreadable is a
  `malformed_response` error rather than a crash.
"""

import json
import socket
import urllib.error
import urllib.parse
import urllib.request

from . import __version__, urls

USER_AGENT = f"omatrade/{__version__}"
MAX_BODY = 2 * 1024 * 1024


class ApiError(Exception):
    """An answer from the server that is not a success."""

    def __init__(self, status, code, message="", retry_after=None, rate_limit=None):
        super().__init__(f"{code}: {message}" if message else code)
        self.status = status
        self.code = code
        self.message = message
        self.retry_after = retry_after
        self.rate_limit = rate_limit or {}


class OfflineError(Exception):
    """No answer at all: DNS, refused, reset, timeout."""


class NotSignedIn(Exception):
    pass


class AuthExpired(Exception):
    """The session can no longer be refreshed; the user must sign in again."""


class HostViolation(Exception):
    pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, "redirect refused", headers, fp)


def build_opener():
    return urllib.request.build_opener(_NoRedirect())


def _int_or_none(value):
    try:
        return max(0, int(float(value)))
    except (TypeError, ValueError):
        return None


def rate_limit_info(headers):
    if headers is None:
        return {}
    out = {}
    for key, name in (("limit", "RateLimit-Limit"), ("remaining", "RateLimit-Remaining"),
                      ("reset", "RateLimit-Reset")):
        value = _int_or_none(headers.get(name))
        if value is not None:
            out[key] = value
    return out


def _read_capped(response):
    data = response.read(MAX_BODY + 1)
    if len(data) > MAX_BODY:
        raise ApiError(getattr(response, "status", 0), "malformed_response", "response too large")
    return data


def _error_from(status, headers, body):
    code, message = f"http_{status}", ""
    try:
        parsed = json.loads(body or b"{}")
        err = parsed.get("error") if isinstance(parsed, dict) else None
        if isinstance(err, dict):
            code = str(err.get("code") or code)[:64]
            message = str(err.get("message") or "")[:300]
        elif isinstance(err, str):
            # RFC 6749 token-endpoint errors: {"error": "invalid_grant", ...}
            code = err[:64]
            message = str(parsed.get("error_description") or "")[:300]
    except (ValueError, AttributeError):
        pass
    retry = _int_or_none(headers.get("Retry-After")) if headers is not None else None
    limits = rate_limit_info(headers)
    if retry is None and status == 429:
        retry = limits.get("reset")
    return ApiError(status, code, message, retry_after=retry, rate_limit=limits)


class ApiClient:
    def __init__(self, config, tokens=None, opener=None, timeout=10):
        self.config = config
        self.tokens = tokens
        self.opener = opener or build_opener()
        self.timeout = timeout
        self.base = config.api_base.rstrip("/")
        self._origin = urls.origin_of(self.base)
        self.last_rate_limit = {}

    # ── URL building ────────────────────────────────────────────────────
    def url(self, path, query=None):
        if not isinstance(path, str) or not path.startswith("/") or path.startswith("//"):
            raise HostViolation(f"not an API path: {path!r}")
        if "://" in path or "\\" in path or any(seg == ".." for seg in path.split("/")):
            raise HostViolation(f"not an API path: {path!r}")
        full = self.base + path
        if query:
            clean = {k: v for k, v in query.items() if v is not None}
            if clean:
                full += "?" + urllib.parse.urlencode(clean, doseq=True)
        if urls.origin_of(full) != self._origin:
            raise HostViolation(f"refusing to contact {urls.origin_of(full)[1]}")
        return full

    # ── Requests ────────────────────────────────────────────────────────
    def _request(self, method, path, *, query=None, body=None, form=None, token=None,
                 accept="application/json", timeout=None, extra_headers=None):
        url = self.url(path, query)
        headers = {"User-Agent": USER_AGENT, "Accept": accept}
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        elif form is not None:
            data = urllib.parse.urlencode(form).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if extra_headers:
            headers.update(extra_headers)
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            return self.opener.open(request, timeout=timeout or self.timeout)
        except urllib.error.HTTPError as e:
            try:
                body_bytes = e.read(64 * 1024)
            except Exception:
                body_bytes = b""
            if 300 <= e.code < 400:
                raise ApiError(e.code, "redirect_refused", "the API answered with a redirect") from None
            raise _error_from(e.code, e.headers, body_bytes) from None
        except (urllib.error.URLError, socket.timeout, ConnectionError, OSError) as e:
            raise OfflineError(str(getattr(e, "reason", e))) from None

    def _json(self, response):
        with response:
            self.last_rate_limit = rate_limit_info(response.headers)
            raw = _read_capped(response)
            status = getattr(response, "status", 200)
        if status == 204 or not raw.strip():
            return {}
        try:
            return json.loads(raw)
        except ValueError:
            raise ApiError(status, "malformed_response", "the API answered with something that is not JSON") from None

    def call(self, method, path, *, query=None, body=None, auth="optional", timeout=None):
        """JSON request. `auth` is "none" (never send a token), "optional"
        (send one if signed in) or "required" (NotSignedIn without one).
        A 401 triggers one refresh-and-retry; a second 401 expires the
        session."""
        token = None
        if auth != "none" and self.tokens is not None:
            token = self.tokens.access_token(self)
        if auth == "required" and not token:
            raise NotSignedIn()
        try:
            return self._json(self._request(method, path, query=query, body=body, token=token,
                                            timeout=timeout))
        except ApiError as e:
            if e.status != 401 or not token:
                raise
        token = self.tokens.refresh(self, force=True, rejected=token)
        if not token:
            raise AuthExpired()
        try:
            return self._json(self._request(method, path, query=query, body=body, token=token,
                                            timeout=timeout))
        except ApiError as e:
            if e.status == 401:
                self.tokens.expire()
                raise AuthExpired() from None
            raise

    def form(self, path, form):
        """OAuth endpoints: form-encoded, never with a bearer token."""
        return self._json(self._request("POST", path, form=form))

    def open_stream(self, path, *, token=None, last_event_id=None, timeout=45):
        headers = {"Cache-Control": "no-cache"}
        if last_event_id:
            headers["Last-Event-ID"] = last_event_id
        response = self._request("GET", path, token=token, accept="text/event-stream",
                                 timeout=timeout, extra_headers=headers)
        ctype = response.headers.get("Content-Type", "")
        if not ctype.startswith("text/event-stream"):
            response.close()
            raise ApiError(getattr(response, "status", 200), "malformed_response",
                           "stream is not text/event-stream")
        return response
