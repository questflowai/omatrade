import json
import unittest

from tests import util
from omatrade import data, risk


class Risk(unittest.TestCase):
    def test_distance_long_and_short(self):
        self.assertEqual(risk.distance_to_liquidation_pct("long", 100, 80), 20.0)
        self.assertEqual(risk.distance_to_liquidation_pct("short", 100, 104.5), 4.5)
        self.assertEqual(risk.distance_to_liquidation_pct("long", 112450, 79500), 29.3)

    def test_through_liquidation_is_zero(self):
        self.assertEqual(risk.distance_to_liquidation_pct("long", 70, 80), 0.0)
        self.assertEqual(risk.distance_to_liquidation_pct("short", 120, 100), 0.0)

    def test_no_liquidation(self):
        for args in [("long", 100, None), ("long", None, 80), ("long", 0, 80), ("flat", 100, 80),
                     ("long", float("nan"), 80), ("long", True, 80), ("long", "100", 80)]:
            self.assertIsNone(risk.distance_to_liquidation_pct(*args), args)

    def test_margin_ratio(self):
        self.assertEqual(risk.margin_ratio(2048.2, 9310), 0.22)
        self.assertEqual(risk.margin_ratio(100, 0), 1.0)
        self.assertIsNone(risk.margin_ratio(None, 10))
        self.assertEqual(risk.margin_ratio(1e9, 1), 10.0)

    def test_levels_and_staleness(self):
        self.assertEqual(risk.risk_level(4.5), "danger")
        self.assertEqual(risk.risk_level(12), "watch")
        self.assertEqual(risk.risk_level(40, 0.85), "danger")
        self.assertEqual(risk.risk_level(40, 0.55), "watch")
        self.assertEqual(risk.risk_level(None, None), "ok")
        self.assertEqual(risk.worst(["ok", "danger", "watch"]), "danger")
        self.assertTrue(risk.is_stale(None, 100))
        self.assertTrue(risk.is_stale(0, 200, 120))
        self.assertFalse(risk.is_stale(100, 200, 120))


class Positions(unittest.TestCase):
    def setUp(self):
        self.fixture = json.loads((util.ROOT / "tests/fixtures/positions.json").read_text())
        self.assertIn("DEMO FIXTURE", self.fixture["_fixture"])

    def test_enrichment(self):
        now = 1_800_000_000
        for a in self.fixture["accounts"]:
            a["synced_at"] = now - 10
        accts = data.accounts(self.fixture, now)
        perps = next(a for a in accts if a["venue"] == "hyperliquid")
        eth = next(p for p in perps["positions"] if p["display"] == "ETH")
        self.assertEqual(eth["distanceToLiqPct"], 4.53)
        self.assertEqual(eth["risk"], "danger")
        self.assertEqual(perps["risk"], "danger")
        self.assertEqual(perps["marginRatio"], 0.22)
        self.assertFalse(perps["stale"])
        self.assertEqual(eth["sizeLabel"], "3x Short 12 @ 3,850.00")
        t = data.totals(accts)
        self.assertEqual(t["risk"], "danger")
        self.assertEqual(t["positions"], 5)

    def test_stale_account(self):
        now = 1_800_000_000
        for a in self.fixture["accounts"]:
            a["synced_at"] = now - 3600
        accts = data.accounts(self.fixture, now)
        self.assertTrue(all(a["stale"] for a in accts))
        self.assertTrue(data.totals(accts)["stale"])

    def test_garbage_positions(self):
        raw = {"accounts": [None, {"id": "bad id!"}, {"id": "a1", "positions": [{"id": "p", "mark_price": "1e9", "side": "sideways"}, 5]}]}
        accts = data.accounts(raw, 0)
        self.assertEqual(len(accts), 1)
        self.assertEqual(accts[0]["positions"][0]["side"], "long")
        self.assertIsNone(accts[0]["positions"][0]["last"])


class Signals(unittest.TestCase):
    def test_normalise(self):
        s = data.signal({"id": "sig_1", "symbol": "nasdaq:nvda", "type": "rule_match", "tier": "critical",
                         "strength": 7, "source": {"kind": "rule", "id": "rule_1", "name": "R"},
                         "title": "T\x07itle", "created_at": "2026-10-20T08:00:00Z",
                         "url": "javascript:alert(1)", "read": "yes", "extra": "dropped"})
        self.assertEqual(s["symbol"], "NASDAQ:NVDA")
        self.assertEqual(s["symbols"], ["NVDA"])
        self.assertEqual(s["strength"], 1.0)
        self.assertEqual(s["title"], "Title")
        self.assertEqual(s["url"], "")
        self.assertFalse(s["read"])
        self.assertEqual(s["createdAt"], 1792483200)
        self.assertNotIn("extra", s)

    def test_list_drops_and_counts_bad_items(self):
        items, dropped = data.signal_list([{"id": "a", "title": "x", "created_at": 1},
                                           {"id": "a", "title": "dup"}, {"title": "no id"},
                                           {"id": "b"}, "junk", {"id": "c", "title": "y", "created_at": 5}])
        self.assertEqual([s["id"] for s in items], ["c", "a"])
        self.assertEqual(dropped, 4)
        self.assertEqual(data.signal_list("not a list"), ([], 0))

    def test_unknown_tier_and_type_fall_back(self):
        s = data.signal({"id": "a", "title": "x", "tier": "apocalyptic", "type": "rm -rf"})
        self.assertEqual((s["tier"], s["type"]), ("info", "skill"))

    def test_epoch_parsing(self):
        self.assertEqual(data.epoch(1_800_000_000_000), 1_800_000_000)
        self.assertEqual(data.epoch("2026-10-20T08:00:00+08:00"), 1792454400)
        self.assertIsNone(data.epoch("yesterday"))
        self.assertIsNone(data.epoch(True))


class Watchlist(unittest.TestCase):
    def test_shape(self):
        wl, dropped = data.watchlist({"editable": True, "sections": [
            {"name": "A", "entries": [{"symbol": "NASDAQ:AAPL", "quote": {"last": 1.5, "change_pct": 2}},
                                      {"symbol": "$(rm)"}]}, "junk"]})
        self.assertEqual(dropped, 2)
        self.assertEqual(wl["source"], "account")
        e = wl["sections"][0]["entries"][0]
        self.assertEqual((e["display"], e["exchange"], e["changePct"], e["found"]), ("AAPL", "NASDAQ", 2.0, True))


if __name__ == "__main__":
    unittest.main()
