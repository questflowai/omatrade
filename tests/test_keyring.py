import json
import os
import subprocess
import unittest

from tests import util
from omatrade.keyring import Keyring, KeyringUnavailable


class Result:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


class FakeRunner:
    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    def __call__(self, args, input=None, **kw):
        self.calls.append((args, input, kw))
        return self.results.pop(0)


ATTRS = {"application": "omatrade", "api": "https://api.test.invalid", "kind": "oauth-session"}


class KeyringWrapper(unittest.TestCase):
    def test_secret_goes_on_stdin_never_argv(self):
        run = FakeRunner([Result()])
        Keyring(tool="/usr/bin/secret-tool", runner=run).store(ATTRS, "label", "s3cr3t-value")
        args, stdin, kw = run.calls[0]
        self.assertEqual(stdin, "s3cr3t-value")
        self.assertNotIn("s3cr3t-value", " ".join(args))
        self.assertEqual(args[:3], ["/usr/bin/secret-tool", "store", "--label=label"])
        self.assertEqual(args[3:], ["api", ATTRS["api"], "application", "omatrade", "kind", "oauth-session"])
        self.assertTrue(kw["capture_output"])
        self.assertIn("timeout", kw)

    def test_lookup_found_missing_and_broken(self):
        run = FakeRunner([Result(0, "value\n"), Result(1, "", ""), Result(1, "", "Cannot autolaunch D-Bus")])
        k = Keyring(tool="st", runner=run)
        self.assertEqual(k.lookup(ATTRS), "value")
        self.assertIsNone(k.lookup(ATTRS))
        with self.assertRaises(KeyringUnavailable):
            k.lookup(ATTRS)

    def test_store_failure_fails_closed(self):
        k = Keyring(tool="st", runner=FakeRunner([Result(1, "", "locked collection")]))
        with self.assertRaisesRegex(KeyringUnavailable, "never written to disk"):
            k.store(ATTRS, "l", "x")

    def test_missing_tool(self):
        k = Keyring(which=lambda name: None)
        self.assertFalse(k.available())
        with self.assertRaises(KeyringUnavailable):
            k.lookup(ATTRS)

    def test_runner_errors_become_unavailable(self):
        def boom(*a, **kw):
            raise subprocess.TimeoutExpired("secret-tool", 15)
        with self.assertRaises(KeyringUnavailable):
            Keyring(tool="st", runner=boom).lookup(ATTRS)

    def test_rejects_bad_attribute_names_and_empty_secret(self):
        k = Keyring(tool="st", runner=FakeRunner([]))
        with self.assertRaises(ValueError):
            k.lookup({"bad key": "x"})
        with self.assertRaises(ValueError):
            k.store(ATTRS, "l", "")


class FakeSecretToolRoundTrip(util.TempDirs):
    """The fake secret-tool used by the black-box tests behaves like the real one."""

    def test_round_trip(self):
        env = dict(os.environ, FAKE_KEYRING=str(self.tmp / "kr.json"))
        tool = str(util.FAKES / "secret-tool")

        def runner(args, **kw):
            return subprocess.run(args, env=env, **kw)
        k = Keyring(tool=tool, runner=runner)
        self.assertIsNone(k.lookup(ATTRS))
        k.store(ATTRS, "Omatrade", "abc")
        self.assertEqual(k.lookup(ATTRS), "abc")
        k.clear(ATTRS)
        self.assertIsNone(k.lookup(ATTRS))
        argv_log = (self.tmp / "kr.json.argv").read_text()
        for line in argv_log.splitlines():
            self.assertNotIn("abc", json.loads(line))


if __name__ == "__main__":
    unittest.main()
