"""White-box tests of the daemon's event handling, sync and notification
decisions, against the in-process mock server."""

import json
import threading
import time
import urllib.request

from tests import util
from omatrade import auth, daemon, policy, sse, state as statefile
from omatrade.http import ApiClient
from mock_server import MockServer, iso


class DaemonCase(util.TempDirs):
    def setUp(self):
        super().setUp()
        self.server = MockServer().start()
        self.addCleanup(self.server.stop)
        self.cfg = self.make_config(self.server.base, allow_http=True)
        self.keyring = util.MemoryKeyring()
        self.tokens = auth.TokenManager(self.cfg, self.keyring)
        self.client = ApiClient(self.cfg, self.tokens)
        self.sent = []
        self.d = daemon.Daemon(self.cfg, self.client, self.tokens, notifier=self.sent.append, poke=lambda: None)
        self.d.load()

    def sign_in(self):
        def opener(url):
            threading.Thread(target=lambda: urllib.request.urlopen(url).read(), daemon=True).start()
        auth.login(self.cfg, self.client, self.tokens, opener=opener, timeout=5)

    def ev(self, event, payload, eid="1"):
        data = payload if isinstance(payload, str) else json.dumps(payload)
        return sse.Event(event=event, data=data, id=eid)

    def signal(self, sid, tier="important", **kw):
        return dict({"id": sid, "symbol": "NASDAQ:NVDA", "title": f"title {sid}", "tier": tier,
                     "source": {"kind": "skill", "id": "skill_whales", "name": "Whale flows"},
                     "created_at": iso(time.time())}, **kw)


class PublicMode(DaemonCase):
    def test_signed_out_syncs_public_only(self):
        self.assertTrue(self.d.sync())
        snap = self.d.snap
        self.assertFalse(snap["auth"]["signedIn"])
        self.assertEqual(snap["watchlist"]["source"], "default")
        self.assertEqual([s["id"] for s in snap["signals"]["items"]], ["pub_1"])
        self.assertFalse(snap["positions"]["available"])
        paths = [p for _, p, _, _ in self.server.state.log]
        self.assertNotIn("/v1/positions", paths)
        self.assertFalse(any(bearer for _, _, bearer, _ in self.server.state.log))
        # backlog on first sync is history, not news
        self.assertEqual(self.sent, [])


class SignedIn(DaemonCase):
    def setUp(self):
        super().setUp()
        self.sign_in()
        self.d.sync()

    def test_full_sync(self):
        snap = self.d.snap
        self.assertEqual(snap["auth"]["displayName"], "Test User")
        self.assertEqual(snap["watchlist"]["source"], "account")
        self.assertEqual(len(snap["rules"]), 1)
        self.assertEqual(len(snap["skills"]), 2)
        self.assertEqual(snap["positions"]["totals"]["risk"], "danger")
        self.assertEqual(snap["signals"]["unread"], 1)
        self.assertEqual(self.sent, [])  # baseline

    def test_live_signal_notifies_once_even_when_replayed(self):
        self.d.handle_event(self.ev("signal", self.signal("sig_a"), "5"))
        self.d.handle_event(self.ev("signal", self.signal("sig_a"), "5"))
        self.d.sync(["signals"])
        self.assertEqual([s["id"] for s in self.sent], ["sig_a"])
        self.assertEqual(self.d.last_event_id, "5")
        self.assertEqual(self.d.snap["signals"]["items"][0]["id"], "sig_a")

    def test_ack_from_another_device(self):
        self.d.handle_event(self.ev("ack", {"ids": ["sig_old"]}))
        self.assertTrue(self.d.snap["signals"]["items"][0]["read"])
        self.assertEqual(self.d.snap["signals"]["unread"], 0)
        # acked before it arrived here: never notified
        self.d.handle_event(self.ev("ack", {"ids": ["sig_future"]}))
        self.d.handle_event(self.ev("signal", self.signal("sig_future")))
        self.assertEqual(self.sent, [])

    def test_risk_event_is_critical_and_bypasses_quiet_hours(self):
        self.d.notify_settings = policy.NotifySettings(quiet_start="00:00", quiet_end="23:59")
        self.d.handle_event(self.ev("signal", self.signal("sig_q")))
        self.d.handle_event(self.ev("risk", self.signal("risk_1", tier="critical", type="risk",
                                                       title="ETH short 4.5% from liquidation")))
        self.assertEqual([s["id"] for s in self.sent], ["risk_1"])
        self.assertEqual(self.sent[0]["source"]["kind"], "risk")

    def test_positions_and_quote_events(self):
        self.d.handle_event(self.ev("positions", {"accounts": [{"id": "a1", "name": "X", "equity": 100,
                                                                "maintenance_margin": 90, "synced_at": iso(time.time()),
                                                                "positions": []}]}))
        self.assertEqual(self.d.snap["positions"]["accounts"][0]["risk"], "danger")
        self.d.handle_event(self.ev("quote", {"symbol": "NASDAQ:AAPL", "last": 250.5, "change_pct": 3.1}))
        entry = self.d.snap["watchlist"]["sections"][0]["entries"][0]
        self.assertEqual((entry["last"], entry["changePct"]), (250.5, 3.1))

    def test_malformed_events_are_counted_not_fatal(self):
        before = self.d.snap["malformed"]
        self.d.handle_event(self.ev("signal", "{not json"))
        self.d.handle_event(self.ev("signal", "[1,2]"))
        self.d.handle_event(self.ev("signal", {"title": "no id"}))
        self.d.handle_event(self.ev("mystery", {"x": 1}))
        self.assertEqual(self.d.snap["malformed"], before + 3)

    def test_sync_event_refetches_named_resources(self):
        self.server.state.rules.clear()
        self.d.handle_event(self.ev("sync", {"resources": ["rules", "nope"]}))
        self.assertEqual(self.d.snap["rules"], [])

    def test_session_revoked_event_clears_personal_data(self):
        self.d.handle_event(self.ev("session", {"state": "revoked"}))
        self.assertEqual(self.d.snap["auth"]["state"], "revoked")
        self.assertFalse(self.d.snap["positions"]["available"])
        self.assertEqual(self.keyring.items, {})

    def test_expired_session_falls_back_to_public(self):
        self.server.state.force_401 = True
        self.d.sync()
        self.assertEqual(self.d.snap["auth"]["state"], "expired")
        self.assertEqual(self.d.snap["watchlist"]["source"], "default")
        self.assertEqual(self.d.snap["positions"]["accounts"], [])

    def test_rate_limited(self):
        self.server.state.rate_limit = 1
        self.d.sync()
        self.assertEqual(self.d.mode, "rate_limited")
        self.assertGreaterEqual(self.d._next_poll, time.time() + 1)
        self.assertIn("Rate limited", self.d.snap["connection"]["error"])

    def test_offline_keeps_last_data_and_goes_stale(self):
        self.d.publish(force=True)
        self.server.stop()
        self.d.sync()
        self.assertEqual(self.d.mode, "offline")
        self.d.publish(force=True)
        snap = statefile.load_json(daemon.snapshot_path(self.cfg), {})
        self.assertTrue(snap["stale"])
        self.assertTrue(snap["positions"]["available"])
        self.assertEqual(snap["connection"]["mode"], "offline")
        self.server = MockServer().start()  # for cleanup

    def test_snapshot_and_state_files_hold_no_tokens_and_are_private(self):
        self.d.publish(force=True)
        session = json.loads(next(iter(self.keyring.items.values())))
        for f in self.cfg.state_dir.rglob("*"):
            if f.is_file():
                body = f.read_text(errors="ignore")
                self.assertNotIn(session["access_token"], body, f)
                self.assertNotIn(session["refresh_token"], body, f)
                if f.suffix == ".json":
                    self.assertEqual(f.stat().st_mode & 0o077, 0, f)
        self.assertEqual(self.cfg.state_dir.stat().st_mode & 0o077, 0)


class StreamLoop(DaemonCase):
    def test_reconnects_with_last_event_id_after_restart(self):
        self.sign_in()
        self.d.sync()
        self.d.backoff = sse.Backoff(base=0.1, cap=0.3)
        t = threading.Thread(target=self.d.stream_loop, daemon=True)
        t.start()

        def wait_for(pred, timeout=5):
            end = time.time() + timeout
            while time.time() < end:
                while not self.d.q.empty():
                    self.d.step(self.d.q.get())
                if pred():
                    return True
                time.sleep(0.05)
            return False

        self.assertTrue(wait_for(lambda: self.d.mode == "live"))
        self.server.state.push("signal", self.signal("sig_1"))
        self.assertTrue(wait_for(lambda: self.d.last_event_id == "1"))
        port = self.server.server_address[1]
        state = self.server.state
        self.server.stop()
        self.assertTrue(wait_for(lambda: self.d.mode in ("polling", "offline")))
        self.server = MockServer(port=port, state=state).start()
        self.assertTrue(wait_for(lambda: self.d.mode == "live" and len(state.stream_connects) >= 2, 8))
        self.assertEqual(state.stream_connects[-1], "1")
        self.d.stop_event.set()
        self.d.reconnect_stream()
        t.join(3)
        self.assertEqual([s["id"] for s in self.sent], ["sig_1"])

    def test_server_retry_hint_sets_minimum_wait(self):
        self.server.state.stream_retry_ms = 700
        self.server.state.heartbeat = 0.1
        self.d.stream_timeout = 0.5
        self.d.backoff = sse.Backoff(base=0.01, cap=5)
        t = threading.Thread(target=self.d.stream_loop, daemon=True)
        t.start()
        time.sleep(0.4)
        self.server.state.stream_closing = True  # server ends the stream
        waits = []
        end = time.time() + 4
        while time.time() < end and not waits:
            kind, payload = self.d.q.get(timeout=4)
            if kind == "retry_in":
                waits.append(payload)
        self.d.stop_event.set()
        self.d.reconnect_stream()
        self.assertTrue(waits)
        self.assertGreaterEqual(waits[0], 0.7)


class Settings(DaemonCase):
    def test_settings_file_is_picked_up(self):
        self.d._check_files()
        statefile.save_json(daemon.settings_path(self.cfg), {"notify": {"enabled": False, "dailyCap": 3}, "pollIntervalSec": 5})
        time.sleep(0.01)
        self.d._mtimes["settings"] = -1
        self.d._check_files()
        self.assertFalse(self.d.notify_settings.enabled)
        self.assertEqual(self.d.poll_interval, 15)  # clamped
        self.assertEqual(self.d.snap["notify"]["dailyCap"], 3)


if __name__ == "__main__":
    import unittest
    unittest.main()
