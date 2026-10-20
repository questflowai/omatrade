import unittest

from tests import util  # noqa: F401
from omatrade import desktop


def sig(tier, **kw):
    return dict({"id": "sig_1", "tier": tier, "symbols": ["NVDA"], "title": "-n evil title",
                 "source": {"name": "Whale flows"}, "url": "https://app.test.invalid/s/1"}, **kw)


class Notification(unittest.TestCase):
    def test_tier_to_urgency(self):
        for tier, urgency in (("info", "low"), ("important", "normal"), ("critical", "critical")):
            cmd = desktop.notification_command(sig(tier), icon=None)
            self.assertIn(f"--urgency={urgency}", cmd)
            self.assertEqual("--expire-time=0" in cmd, tier == "critical")

    def test_text_cannot_become_options(self):
        cmd = desktop.notification_command(sig("info"), icon=None)
        self.assertEqual(cmd[-3:], ["--", "NVDA  Whale flows", "-n evil title"])

    def test_no_link_no_action(self):
        self.assertNotIn("--action=open=Open", desktop.notification_command(sig("info", url=""), icon=None))

    def test_send_passes_data_only_through_env(self):
        calls = []
        desktop.send_notification(sig("critical", url="javascript:alert(1)"),
                                  popen=lambda args, **kw: calls.append((args, kw)), which=lambda n: "/bin/x")
        args, kw = calls[0]
        self.assertEqual(args[:2], ["sh", "-c"])
        self.assertEqual(args[2], desktop.CLICK_SCRIPT)
        self.assertNotIn("javascript", " ".join(args))
        self.assertEqual(kw["env"]["OMATRADE_URL"], "")
        self.assertEqual(kw["env"]["OMATRADE_SIGNAL_ID"], "sig_1")
        self.assertTrue(kw["start_new_session"])

    def test_open_url_refuses_unsafe(self):
        calls = []
        self.assertFalse(desktop.open_url("file:///etc/passwd", popen=lambda *a, **k: calls.append(a)))
        self.assertTrue(desktop.open_url("https://app.test.invalid/", popen=lambda *a, **k: calls.append(a)))
        self.assertEqual(calls[0][0], ["xdg-open", "https://app.test.invalid/"])


if __name__ == "__main__":
    unittest.main()
