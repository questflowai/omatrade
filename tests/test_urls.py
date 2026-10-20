import unittest

from tests import util  # noqa: F401
from omatrade import config, urls


class SafeUrl(unittest.TestCase):
    def test_https_passes(self):
        self.assertEqual(urls.safe_url("https://example.com/a?b=1"), "https://example.com/a?b=1")

    def test_everything_else_is_refused(self):
        for bad in ["http://example.com", "file:///etc/passwd", "javascript:alert(1)",
                    "-https://x", "https://user:pw@example.com/", "https:///nohost",
                    "https://example.com/\nX", "custom-handler://do", None, 42, "",
                    "https://example.com/" + "a" * 3000, "HTTPS ://x"]:
            self.assertEqual(urls.safe_url(bad), "", bad)


class ValidateBase(unittest.TestCase):
    def test_https(self):
        self.assertEqual(urls.validate_base("https://api.example.test/"), "https://api.example.test")

    def test_http_needs_loopback_and_override(self):
        with self.assertRaises(urls.UnsafeURL):
            urls.validate_base("http://api.example.test")
        with self.assertRaises(urls.UnsafeURL):
            urls.validate_base("http://127.0.0.1:8000")
        self.assertEqual(urls.validate_base("http://127.0.0.1:8000", allow_http_loopback=True),
                         "http://127.0.0.1:8000")
        self.assertEqual(urls.validate_base("http://localhost:1", allow_http_loopback=True), "http://localhost:1")
        with self.assertRaises(urls.UnsafeURL):
            urls.validate_base("http://10.0.0.1", allow_http_loopback=True)
        with self.assertRaises(urls.UnsafeURL):
            urls.validate_base("http://localhost.evil.test", allow_http_loopback=True)

    def test_rejects_credentials_query_fragment(self):
        for bad in ["https://u:p@h.test", "https://h.test/?x=1", "https://h.test/#f", "ftp://h.test", ""]:
            with self.assertRaises(urls.UnsafeURL):
                urls.validate_base(bad)


class ConfigLoad(util.TempDirs):
    def test_placeholders_are_unconfigured(self):
        cfg = config.load(env={"XDG_CONFIG_HOME": str(self.tmp)})
        self.assertEqual(cfg.api_base, "https://api.example.invalid")
        self.assertTrue(cfg.client_id.endswith("placeholder"))
        self.assertFalse(cfg.configured)

    def test_env_overrides_file(self):
        d = self.tmp / "omatrade"
        d.mkdir()
        (d / "config.toml").write_text('api_base = "https://file.test"\nclient_id = "c1"\n')
        cfg = config.load(env={"XDG_CONFIG_HOME": str(self.tmp)})
        self.assertEqual(cfg.api_base, "https://file.test")
        cfg = config.load(env={"XDG_CONFIG_HOME": str(self.tmp), "OMATRADE_API_BASE": "https://env.test"})
        self.assertEqual(cfg.api_base, "https://env.test")
        self.assertTrue(cfg.configured)

    def test_insecure_base_is_a_config_error(self):
        with self.assertRaises(config.ConfigError):
            config.load(env={"XDG_CONFIG_HOME": str(self.tmp), "OMATRADE_API_BASE": "http://api.test"})

    def test_public_view_has_no_secret_fields(self):
        cfg = config.load(env={"XDG_CONFIG_HOME": str(self.tmp)})
        self.assertEqual(set(cfg.public()), {"apiBase", "webBase", "clientId", "scopes", "configured", "devHttpLoopback"})
        self.assertEqual(cfg.scopes, ("read:profile", "read:signals", "read:positions"))


if __name__ == "__main__":
    unittest.main()
