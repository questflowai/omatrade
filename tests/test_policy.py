import datetime as dt
import unittest

from tests import util  # noqa: F401
from omatrade import policy

TZ = dt.timezone(dt.timedelta(hours=8))


def at(h, m=0, day=20):
    return dt.datetime(2026, 10, day, h, m, tzinfo=TZ)


def sig(i, tier="important", **kw):
    return dict({"id": f"s{i}", "tier": tier, "read": False, "expiresAt": None}, **kw)


class QuietHours(unittest.TestCase):
    def test_same_day_window(self):
        s = policy.NotifySettings(quiet_start="12:00", quiet_end="13:30")
        self.assertTrue(policy.in_quiet_hours(s, at(12, 0)))
        self.assertTrue(policy.in_quiet_hours(s, at(13, 29)))
        self.assertFalse(policy.in_quiet_hours(s, at(13, 30)))
        self.assertFalse(policy.in_quiet_hours(s, at(11, 59)))

    def test_window_wrapping_midnight(self):
        s = policy.NotifySettings(quiet_start="22:00", quiet_end="07:00")
        for h in (22, 23, 0, 3, 6):
            self.assertTrue(policy.in_quiet_hours(s, at(h)), h)
        for h in (7, 12, 21):
            self.assertFalse(policy.in_quiet_hours(s, at(h)), h)

    def test_off_or_invalid(self):
        self.assertFalse(policy.in_quiet_hours(policy.NotifySettings(), at(3)))
        s = policy.NotifySettings.from_dict({"quietStart": "25:00", "quietEnd": "07:00"})
        self.assertEqual(s.quiet_start, "")
        self.assertFalse(policy.in_quiet_hours(s, at(3)))
        self.assertFalse(policy.in_quiet_hours(policy.NotifySettings(quiet_start="08:00", quiet_end="08:00"), at(8)))


class Decide(unittest.TestCase):
    def setUp(self):
        self.s = policy.NotifySettings(quiet_start="22:00", quiet_end="07:00", daily_cap=2, min_tier="important")
        self.ledger = policy.Ledger()

    def d(self, signal, when=None, now=0):
        return policy.decide(signal, self.s, self.ledger, when or at(12), now)

    def test_notifies_once_per_id(self):
        self.assertEqual(self.d(sig(1)), (True, "notify"))
        self.assertEqual(self.d(sig(1)), (False, "duplicate"))

    def test_read_expired_disabled_and_tier(self):
        self.assertEqual(self.d(sig(1, read=True)), (False, "read"))
        self.assertEqual(self.d(sig(2, expiresAt=100), now=200), (False, "expired"))
        self.assertEqual(self.d(sig(3, tier="info")), (False, "below_min_tier"))
        self.s.enabled = False
        self.assertEqual(self.d(sig(4, tier="critical")), (False, "disabled"))

    def test_quiet_hours_suppress_but_critical_breaks_through(self):
        self.assertEqual(self.d(sig(1), at(23)), (False, "quiet_hours"))
        self.assertEqual(self.d(sig(2, tier="critical"), at(3)), (True, "notify"))
        # suppressed signals are seen: no burst when quiet hours end
        self.assertEqual(self.d(sig(1), at(8)), (False, "duplicate"))

    def test_daily_cap_resets_next_day_and_ignores_critical(self):
        self.assertTrue(self.d(sig(1))[0])
        self.assertTrue(self.d(sig(2))[0])
        self.assertEqual(self.d(sig(3)), (False, "daily_cap"))
        self.assertEqual(self.d(sig(4, tier="critical")), (True, "notify"))
        self.assertEqual(self.ledger.count, 2)
        self.assertEqual(self.d(sig(5), at(12, day=21)), (True, "notify"))
        self.assertEqual(self.ledger.count, 1)

    def test_urgency_mapping(self):
        self.assertEqual(policy.URGENCY, {"info": "low", "important": "normal", "critical": "critical"})


class LedgerTest(unittest.TestCase):
    def test_bounded_and_round_trips(self):
        led = policy.Ledger()
        for i in range(policy.SEEN_LIMIT + 50):
            led.mark(f"id{i}")
        self.assertEqual(len(led.seen), policy.SEEN_LIMIT)
        self.assertFalse(led.has("id0"))
        self.assertTrue(led.has(f"id{policy.SEEN_LIMIT + 49}"))
        again = policy.Ledger(led.to_dict())
        self.assertTrue(again.has(f"id{policy.SEEN_LIMIT + 49}"))

    def test_garbage_is_tolerated(self):
        led = policy.Ledger({"seen": [1, None, "ok"], "day": 5, "count": "x"})
        self.assertEqual(led.seen, ["ok"])
        self.assertEqual(led.count, 0)


class SettingsParsing(unittest.TestCase):
    def test_defaults_and_clamps(self):
        s = policy.NotifySettings.from_dict({"dailyCap": 10_000, "minTier": "loud", "enabled": "no"})
        self.assertEqual(s.daily_cap, 500)
        self.assertEqual(s.min_tier, "important")
        self.assertTrue(s.enabled)  # only an explicit false switches off
        self.assertFalse(policy.NotifySettings.from_dict({"enabled": False}).enabled)


if __name__ == "__main__":
    unittest.main()
