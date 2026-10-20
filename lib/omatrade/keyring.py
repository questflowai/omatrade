"""Tokens live in the system keyring (Secret Service, via libsecret's
`secret-tool`) and nowhere else.

The secret is always passed on stdin, never on the command line, where any
local process could read it from /proc. With no keyring available every
operation fails closed: Omatrade will not fall back to a file on disk.
"""

import shutil
import subprocess

TOOL = "secret-tool"
TIMEOUT = 15


class KeyringUnavailable(Exception):
    MESSAGE = ("No system keyring is available, so Omatrade cannot keep you signed in. "
               "Install libsecret (secret-tool) and a Secret Service provider such as "
               "gnome-keyring or KeePassXC, unlock it, and try again. "
               "Tokens are never written to disk in plain text.")

    def __init__(self, detail=""):
        super().__init__(self.MESSAGE + (f" ({detail})" if detail else ""))
        self.detail = detail


def _flatten(attrs):
    out = []
    for key, value in sorted(attrs.items()):
        if not key or any(c.isspace() for c in key):
            raise ValueError(f"bad keyring attribute {key!r}")
        out += [key, str(value)]
    return out


class Keyring:
    def __init__(self, tool=None, runner=None, which=None):
        self._tool = tool
        self._runner = runner or subprocess.run
        self._which = which or shutil.which

    def tool(self):
        path = self._tool or self._which(TOOL)
        if not path:
            raise KeyringUnavailable("secret-tool not found")
        return path

    def available(self):
        try:
            self.tool()
            return True
        except KeyringUnavailable:
            return False

    def probe(self):
        """Talk to the Secret Service once, so a missing daemon or a locked
        collection is found before the browser opens, not after."""
        self.lookup({"application": "omatrade", "kind": "probe"})

    def _run(self, args, secret=None):
        try:
            return self._runner([self.tool()] + args, input=secret, capture_output=True,
                                text=True, timeout=TIMEOUT, check=False)
        except (OSError, subprocess.SubprocessError) as e:
            raise KeyringUnavailable(type(e).__name__) from None

    def store(self, attrs, label, secret):
        if not isinstance(secret, str) or not secret:
            raise ValueError("empty secret")
        result = self._run(["store", f"--label={label}"] + _flatten(attrs), secret=secret)
        if result.returncode != 0:
            raise KeyringUnavailable((result.stderr or "store failed").strip()[:200])

    def lookup(self, attrs):
        result = self._run(["lookup"] + _flatten(attrs))
        if result.returncode == 0:
            return result.stdout.rstrip("\n") or None
        # secret-tool exits 1 with no output when nothing matches; anything on
        # stderr means the keyring itself is the problem (locked, no daemon).
        if (result.stderr or "").strip():
            raise KeyringUnavailable(result.stderr.strip()[:200])
        return None

    def clear(self, attrs):
        result = self._run(["clear"] + _flatten(attrs))
        if result.returncode not in (0, 1) or (result.returncode == 1 and (result.stderr or "").strip()):
            raise KeyringUnavailable((result.stderr or "clear failed").strip()[:200])
