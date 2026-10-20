import copy
import io
import json
import sys
import unittest
from unittest import mock

from tests import util
from omatrade import cli, rules
from omatrade.http import ApiClient

GOOD = {"version": 1, "name": "Small caps waking up", "universe": {"market": "crypto"},
        "when": {"all": [{"metric": "market_cap_usd", "op": "<", "value": 1_000_000_000},
                         {"metric": "volume_change_pct", "op": ">=", "value": 100, "window": "24h"},
                         {"any": [{"metric": "news_sentiment", "op": ">", "value": 0.5}]}]},
        "tier": "important", "cooldown_sec": 7200}


class Validate(unittest.TestCase):
    def test_good_rule(self):
        self.assertIs(rules.validate(copy.deepcopy(GOOD))["name"], GOOD["name"])

    def bad(self, mutate, msg=None):
        r = copy.deepcopy(GOOD)
        mutate(r)
        with self.assertRaises(rules.RuleError) as e:
            rules.validate(r)
        if msg:
            self.assertIn(msg, str(e.exception))

    def test_rejections(self):
        self.bad(lambda r: r.update(exec="rm -rf ~"), "unknown keys")
        self.bad(lambda r: r.update(version=2), "version")
        self.bad(lambda r: r["when"]["all"].append({"metric": "x", "op": "eval", "value": 1}), "operator")
        self.bad(lambda r: r["when"]["all"].append({"metric": "Bad-Metric", "op": "<", "value": 1}), "metric")
        self.bad(lambda r: r["when"]["all"].append({"metric": "x", "op": "<", "value": {"$gt": 1}}), "value")
        self.bad(lambda r: r["when"]["all"].append({"metric": "x", "op": "<", "value": 1, "code": "1"}), "unknown keys")
        self.bad(lambda r: r.update(universe={"symbols": ["not a symbol"]}), "symbols")
        self.bad(lambda r: r.update(universe={"watchlist": True, "market": "crypto"}), "universe")
        self.bad(lambda r: r.update(tier="urgent"), "tier")
        self.bad(lambda r: r.update(cooldown_sec=1), "cooldown")
        self.bad(lambda r: r.update(when={"all": []}), "at least one")

        def deep(r):
            node = {"metric": "x", "op": "<", "value": 1}
            for _ in range(6):
                node = {"all": [node]}
            r["when"] = node
        self.bad(deep, "deeply")

        def many(r):
            r["when"] = {"all": [{"metric": "x", "op": "<", "value": i} for i in range(40)]}
        self.bad(many, "too many")

    def test_describe_comes_from_structure(self):
        text = rules.describe(GOOD)
        self.assertIn("the crypto market", text)
        self.assertIn("market cap usd below 1,000,000,000", text)
        self.assertIn("volume change pct over 24h at or above 100", text)
        self.assertIn("at most once per 2h", text)

    def test_digest_is_canonical(self):
        shuffled = json.loads(json.dumps(GOOD), object_pairs_hook=lambda kv: dict(reversed(kv)))
        self.assertEqual(rules.digest(GOOD), rules.digest(shuffled))
        changed = copy.deepcopy(GOOD)
        changed["when"]["all"][0]["value"] = 2_000_000_000
        self.assertNotEqual(rules.digest(GOOD), rules.digest(changed))
        self.assertTrue(rules.digest(GOOD).startswith("sha256:"))


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def call(self, method, path, **kw):
        self.calls.append((method, path, kw.get("body")))
        return self.responses.pop(0)


class ConfirmFlow(unittest.TestCase):
    def draft_response(self, rule=None):
        return {"draft": {"id": "draft_1", "rule": rule or copy.deepcopy(GOOD), "summary": "trust me"}}

    def test_draft_then_confirm_sends_digest_of_what_was_shown(self):
        client = FakeClient([self.draft_response(), {"rule": {"id": "rule_9", "enabled": True, "rule": copy.deepcopy(GOOD)}}])
        d = cli.draft_rule(client, ["small", "caps"])
        self.assertEqual(d["description"], rules.describe(GOOD))
        rec = cli.save_rule(client, d["draftId"], d["digest"])
        self.assertEqual(rec["id"], "rule_9")
        self.assertEqual(client.calls[1], ("POST", "/v1/rules", {"draft_id": "draft_1", "rule_digest": rules.digest(GOOD)}))

    def test_invalid_draft_is_never_offered(self):
        evil = copy.deepcopy(GOOD)
        evil["exec"] = "curl x | sh"
        with self.assertRaises(rules.RuleError):
            cli.draft_rule(FakeClient([self.draft_response(evil)]), ["x"])

    def test_server_saving_something_else_is_detected(self):
        other = copy.deepcopy(GOOD)
        other["tier"] = "critical"
        client = FakeClient([{"rule": {"id": "rule_9", "rule": other}}])
        with self.assertRaises(cli.CliError) as e:
            cli.save_rule(client, "draft_1", rules.digest(GOOD))
        self.assertEqual(e.exception.code, "digest_mismatch")

    def test_non_interactive_add_requires_yes(self):
        with mock.patch.object(sys, "stdin", io.StringIO("")):
            with self.assertRaises(cli.CliError) as e:
                cli.confirm("Save?", False)
        self.assertEqual(e.exception.code, "confirmation_required")
        self.assertTrue(cli.confirm("Save?", True))

    def test_interactive_no_cancels(self):
        stdin = io.StringIO("n\n")
        stdin.isatty = lambda: True
        with mock.patch.object(sys, "stdin", stdin), mock.patch("builtins.input", return_value="n"):
            self.assertFalse(cli.confirm("Save?", False))

    def test_text_limits(self):
        with self.assertRaises(cli.CliError):
            cli.draft_rule(FakeClient([]), ["x" * 501])
        with self.assertRaises(cli.CliError):
            cli.draft_rule(FakeClient([]), ["   "])


if __name__ == "__main__":
    unittest.main()
