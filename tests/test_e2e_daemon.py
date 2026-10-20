"""Black-box: run `omatrade daemon run` as a subprocess against the mock
server and watch only its outputs: the snapshot file, notifications sent
through the fake notify-send, and what the server saw."""

import json
import subprocess
import time

from tests import util
from tests.test_e2e_cli import CLI, E2E
from mock_server import MockServer, iso


class Daemon(E2E):
    def start_daemon(self):
        d = subprocess.Popen([CLI, "daemon", "run"], env=self.env, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True)
        self.addCleanup(self.stop_daemon, d)
        self.assertTrue(self.wait(lambda s: s.get("connection", {}).get("mode") == "live"), self.snap())
        return d

    def stop_daemon(self, d):
        if d.poll() is None:
            subprocess.run([CLI, "daemon", "poke", "stop"], env=self.env, capture_output=True)
            try:
                d.wait(10)
            except subprocess.TimeoutExpired:
                d.kill()
                d.wait()
        d.stdout.close()
        d.stderr.close()

    def snap(self):
        try:
            return json.loads((self.tmp / "state" / "omatrade" / "state.json").read_text())
        except (OSError, ValueError):
            return {}

    def wait(self, pred, timeout=10):
        end = time.time() + timeout
        while time.time() < end:
            s = self.snap()
            if s and pred(s):
                return True
            time.sleep(0.1)
        return False

    def notifications(self):
        try:
            return [json.loads(line) for line in (self.tmp / "notify.log").read_text().splitlines()]
        except OSError:
            return []

    def push_signal(self, sid, tier="important", **kw):
        return self.server.state.push("signal", dict({
            "id": sid, "symbol": "NASDAQ:NVDA", "title": f"title {sid}", "tier": tier,
            "source": {"kind": "skill", "id": "skill_whales", "name": "Whale flows"},
            "created_at": iso(time.time()), "url": "https://app.test.invalid/s/" + sid}, **kw))

    def test_single_instance(self):
        self.start_daemon()
        r = subprocess.run([CLI, "daemon", "run"], env=self.env, capture_output=True, text=True, timeout=10)
        self.assertEqual(r.returncode, 1)
        self.assertIn("already running", r.stderr)
        r = subprocess.run([CLI, "daemon", "status", "--json"], env=self.env, capture_output=True, text=True)
        self.assertTrue(json.loads(r.stdout)["running"])

    def test_public_then_sign_in_then_live_signals(self):
        self.start_daemon()
        s = self.snap()
        self.assertFalse(s["auth"]["signedIn"])
        self.assertEqual(s["watchlist"]["source"], "default")
        self.assertEqual(self.notifications(), [])

        self.assertEqual(self.login()[0], 0)  # CLI nudges the daemon to reload auth
        self.assertTrue(self.wait(lambda s: s["auth"]["signedIn"] and s["positions"]["available"]
                                  and s["connection"]["mode"] == "live"))
        time.sleep(0.5)
        for sid, tier in (("sig_i", "info"), ("sig_n", "important"), ("sig_c", "critical")):
            self.push_signal(sid, tier)
        self.assertTrue(self.wait(lambda s: {"sig_i", "sig_n", "sig_c"} <= {x["id"] for x in s["signals"]["items"]}))
        time.sleep(0.5)
        notes = self.notifications()
        urg = {n[n.index("--") + 2]: [a for a in n if a.startswith("--urgency=")][0] for n in notes}
        # info is below the default minimum tier: listed, not notified
        self.assertEqual(urg, {"title sig_n": "--urgency=normal", "title sig_c": "--urgency=critical"})
        crit = [n for n in notes if "--urgency=critical" in n][0]
        self.assertIn("--expire-time=0", crit)

        # the snapshot never contains the session
        session = json.loads(next(iter(self.keyring_db().values())))
        raw = (self.tmp / "state" / "omatrade" / "state.json").read_text()
        self.assertNotIn(session["access_token"], raw)
        self.assertNotIn(session["refresh_token"], raw)

        # acknowledged on another device -> read here, nothing re-notified
        r, _ = self.json_cli("ack", "sig_n")
        self.assertTrue(self.wait(lambda s: [x for x in s["signals"]["items"] if x["id"] == "sig_n"][0]["read"]))

        # sign out: personal data leaves the snapshot
        self.run_cli("logout")
        self.assertTrue(self.wait(lambda s: not s["auth"]["signedIn"] and not s["positions"]["available"]))

    def test_restart_reconnect_offline_and_dedup(self):
        self.assertEqual(self.login()[0], 0)
        self.start_daemon()
        time.sleep(0.5)
        self.push_signal("sig_1")
        self.assertTrue(self.wait(lambda s: any(x["id"] == "sig_1" for x in s["signals"]["items"])))

        port, state = self.server.server_address[1], self.server.state
        self.server.stop()
        self.assertTrue(self.wait(lambda s: s["connection"]["mode"] == "offline" and s["stale"], 15))
        s = self.snap()
        self.assertTrue(s["positions"]["available"])  # last data still shown
        self.assertIn("unreachable", s["connection"]["error"])

        self.server = MockServer(port=port, state=state).start()
        self.assertTrue(self.wait(lambda s: s["connection"]["mode"] == "live" and not s["stale"], 15))
        self.assertEqual(state.stream_connects[-1], "1")  # resumed with Last-Event-ID
        self.push_signal("sig_2")
        self.assertTrue(self.wait(lambda s: any(x["id"] == "sig_2" for x in s["signals"]["items"])))
        time.sleep(0.3)
        titles = [n[n.index("--") + 2] for n in self.notifications()]
        self.assertEqual(titles, ["title sig_1", "title sig_2"])  # each once, despite resync and replay

    def test_quiet_hours_and_cap_from_settings(self):
        self.assertEqual(self.login()[0], 0)
        r = subprocess.run([CLI, "notify-settings", "--quiet-hours", "00:00-23:59", "--daily-cap", "1",
                            "--min-tier", "info", "--json"], env=self.env, capture_output=True, text=True)
        self.assertEqual(json.loads(r.stdout)["notify"]["quietStart"], "00:00")
        self.start_daemon()
        time.sleep(0.5)
        self.push_signal("sig_quiet", "important")
        self.push_signal("risk_1", "critical", type="risk", title="ETH short 4.5% from liquidation")
        self.assertTrue(self.wait(lambda s: {"sig_quiet", "risk_1"} <= {x["id"] for x in s["signals"]["items"]}))
        time.sleep(0.3)
        titles = [n[n.index("--") + 2] for n in self.notifications()]
        self.assertEqual(titles, ["ETH short 4.5% from liquidation"])
        s = self.snap()
        self.assertEqual(s["notify"]["dailyCap"], 1)

    def test_rate_limited_and_malformed_stream_keep_running(self):
        self.assertEqual(self.login()[0], 0)
        self.start_daemon()
        time.sleep(0.5)
        self.server.state.push("signal", "{this is not json")
        self.server.state.push("signal", {"no": "id"})
        self.push_signal("sig_ok")
        self.assertTrue(self.wait(lambda s: any(x["id"] == "sig_ok" for x in s["signals"]["items"])))
        self.assertGreaterEqual(self.snap()["malformed"], 2)
        self.server.state.rate_limit = 3
        subprocess.run([CLI, "daemon", "poke", "refresh"], env=self.env, capture_output=True)
        self.assertTrue(self.wait(lambda s: s["connection"]["mode"] in ("rate_limited", "live")))


if __name__ == "__main__":
    import unittest
    unittest.main()
