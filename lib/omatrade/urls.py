"""URL rules: what may be opened in a browser and what may be an API base."""

import ipaddress
import urllib.parse

LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}


class UnsafeURL(ValueError):
    pass


def safe_url(url):
    """Only https links leave this program. A link in a signal comes from a
    server or a third party, and xdg-open would hand any other scheme
    (file:, javascript:, a custom handler) to whatever claims it. Returns the
    URL unchanged when it is safe, otherwise an empty string."""
    if not isinstance(url, str) or len(url) > 2048:
        return ""
    if any(ch in url for ch in "\r\n\t\x00 ") or url.startswith("-"):
        return ""
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return ""
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
        return ""
    return url


def is_loopback(host):
    if not host:
        return False
    if host.lower() in LOOPBACK_HOSTS:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def validate_base(url, *, allow_http_loopback=False, what="API base URL"):
    """An API or web base must be https. Plain http is accepted only for a
    loopback host and only when the developer override is switched on, so a
    typo can never send tokens in the clear across a network."""
    if not isinstance(url, str) or not url:
        raise UnsafeURL(f"{what} is empty")
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError as e:
        raise UnsafeURL(f"{what} is not a URL: {e}") from None
    if parts.username or parts.password:
        raise UnsafeURL(f"{what} must not contain credentials")
    if parts.query or parts.fragment:
        raise UnsafeURL(f"{what} must not have a query or fragment")
    if not parts.hostname:
        raise UnsafeURL(f"{what} has no host")
    if parts.scheme == "https":
        pass
    elif parts.scheme == "http" and is_loopback(parts.hostname):
        if not allow_http_loopback:
            raise UnsafeURL(
                f"{what} uses http on {parts.hostname}; set "
                "OMATRADE_DEV_ALLOW_HTTP_LOCALHOST=1 to allow this for local development")
    else:
        raise UnsafeURL(f"{what} must use https (got {parts.scheme or 'no scheme'})")
    return url.rstrip("/")


def origin_of(url):
    parts = urllib.parse.urlsplit(url)
    return parts.scheme, (parts.hostname or "").lower(), parts.port
