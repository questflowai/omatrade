"""Shared test helpers. Tests use only the standard library."""

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "tests"))

from omatrade import config as configmod  # noqa: E402
from omatrade.http import ApiClient  # noqa: E402

FAKES = ROOT / "tests" / "fakes"
TEST_CLIENT_ID = "omatrade-test-client"


class TempDirs(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="omatrade-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def make_config(self, base="https://api.test.invalid", allow_http=False):
        return configmod.Config(api_base=base, web_base="https://app.test.invalid",
                                client_id=TEST_CLIENT_ID, allow_http_loopback=allow_http,
                                state_dir=self.tmp / "state", config_dir=self.tmp / "config",
                                runtime_dir=self.tmp / "run")


class MemoryKeyring:
    """In-memory stand-in for omatrade.keyring.Keyring."""

    def __init__(self, fail=False):
        self.items = {}
        self.fail = fail
        self.calls = []

    def _check(self):
        from omatrade.keyring import KeyringUnavailable
        if self.fail:
            raise KeyringUnavailable("test")

    def tool(self):
        self._check()
        return "secret-tool"

    def available(self):
        return not self.fail

    def probe(self):
        self._check()

    def store(self, attrs, label, secret):
        self.calls.append("store")
        self._check()
        self.items[tuple(sorted(attrs.items()))] = secret

    def lookup(self, attrs):
        self.calls.append("lookup")
        self._check()
        return self.items.get(tuple(sorted(attrs.items())))

    def clear(self, attrs):
        self.calls.append("clear")
        self._check()
        self.items.pop(tuple(sorted(attrs.items())), None)


class RecordingOpener:
    """Wraps a urllib opener and records every URL it is asked to open."""

    def __init__(self, inner):
        self.inner = inner
        self.urls = []

    def open(self, request, timeout=None):
        self.urls.append(request.full_url)
        return self.inner.open(request, timeout=timeout)


def mock_env(base, tmp, extra=None):
    env = dict(os.environ)
    for key in list(env):
        if key.startswith(("OMATRADE_", "XDG_", "FAKE_")):
            env.pop(key)
    env.update({
        "OMATRADE_API_BASE": base,
        "OMATRADE_WEB_BASE": "https://app.test.invalid",
        "OMATRADE_CLIENT_ID": TEST_CLIENT_ID,
        "OMATRADE_DEV_ALLOW_HTTP_LOCALHOST": "1",
        "XDG_CONFIG_HOME": str(tmp / "config"),
        "XDG_STATE_HOME": str(tmp / "state"),
        "XDG_RUNTIME_DIR": str(tmp / "run"),
        "FAKE_KEYRING": str(tmp / "keyring.json"),
        "FAKE_NOTIFY_LOG": str(tmp / "notify.log"),
        "FAKE_OPEN_LOG": str(tmp / "open.log"),
        "PATH": str(FAKES) + os.pathsep + env.get("PATH", ""),
        "HOME": str(tmp / "home"),
        "OMATRADE_BACKOFF_BASE": "0.2",
        "OMATRADE_BACKOFF_CAP": "1.0",
        "OMATRADE_STREAM_TIMEOUT": "3",
    })
    (tmp / "run").mkdir(parents=True, exist_ok=True)
    (tmp / "home").mkdir(parents=True, exist_ok=True)
    env.update(extra or {})
    return env


def client_for(server_base, tmp_case, tokens=None, opener=None):
    cfg = tmp_case.make_config(server_base, allow_http=True)
    return cfg, ApiClient(cfg, tokens, opener=opener)


def have(tool):
    return shutil.which(tool) is not None
