"""The rule DSL as the client sees it: validate what the server drafted,
describe it in words from the structure itself, and fingerprint it so the
server saves exactly what the user confirmed.

A rule is data, never code: conditions over named metrics, combined with
all/any, evaluated on the server. The client never executes a rule.
"""

import hashlib
import json
import re

OPS = {"<": "below", "<=": "at or below", ">": "above", ">=": "at or above",
       "==": "equal to", "!=": "not", "crosses_above": "crossing above",
       "crosses_below": "crossing below", "in": "one of"}
TIERS = ("info", "important", "critical")
METRIC_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
SYMBOL_RE = re.compile(r"^[A-Z0-9_]+:[A-Z0-9_.!&/-]+$")
MAX_DEPTH = 4
MAX_CONDITIONS = 32


class RuleError(ValueError):
    pass


def _check_value(value):
    if isinstance(value, bool) or isinstance(value, (int, float)):
        return
    if isinstance(value, str) and len(value) <= 200:
        return
    if isinstance(value, list) and len(value) <= 50 and all(
            isinstance(v, (int, float, str)) and not isinstance(v, bool) for v in value):
        return
    raise RuleError("condition value must be a number, a short string or a short list")


def _check_node(node, depth, count):
    if depth > MAX_DEPTH:
        raise RuleError("rule is nested too deeply")
    if not isinstance(node, dict):
        raise RuleError("condition must be an object")
    groups = [k for k in ("all", "any") if k in node]
    if groups:
        if len(groups) != 1 or len(node) != 1:
            raise RuleError("a group has exactly one of all/any")
        children = node[groups[0]]
        if not isinstance(children, list) or not children:
            raise RuleError("a group needs at least one condition")
        for child in children:
            count = _check_node(child, depth + 1, count)
        return count
    if set(node) - {"metric", "op", "value", "window"}:
        raise RuleError("unknown keys in condition: " + ", ".join(sorted(set(node) - {"metric", "op", "value", "window"})))
    if not isinstance(node.get("metric"), str) or not METRIC_RE.match(node["metric"]):
        raise RuleError("condition metric must be a lower_snake_case name")
    if node.get("op") not in OPS:
        raise RuleError("condition operator is not supported")
    if "value" not in node:
        raise RuleError("condition needs a value")
    _check_value(node["value"])
    if "window" in node and (not isinstance(node["window"], str) or not re.match(r"^\d{1,4}[mhd]$", node["window"])):
        raise RuleError("condition window must look like 15m, 4h or 1d")
    count += 1
    if count > MAX_CONDITIONS:
        raise RuleError("rule has too many conditions")
    return count


def validate(rule):
    """Raise RuleError unless `rule` is a well-formed version-1 rule."""
    if not isinstance(rule, dict):
        raise RuleError("rule must be an object")
    if rule.get("version") != 1:
        raise RuleError("unsupported rule version")
    name = rule.get("name")
    if not isinstance(name, str) or not name.strip() or len(name) > 120:
        raise RuleError("rule needs a name")
    universe = rule.get("universe")
    if not isinstance(universe, dict) or len(universe) != 1:
        raise RuleError("rule universe must be one of watchlist, symbols, market")
    (kind, value), = universe.items()
    if kind == "watchlist":
        if value is not True:
            raise RuleError("universe.watchlist must be true")
    elif kind == "symbols":
        if not isinstance(value, list) or not value or len(value) > 100 or not all(
                isinstance(s, str) and SYMBOL_RE.match(s) for s in value):
            raise RuleError("universe.symbols must be EXCHANGE:SYMBOL strings")
    elif kind == "market":
        if not isinstance(value, str) or not METRIC_RE.match(value):
            raise RuleError("universe.market must be a market name")
    else:
        raise RuleError("rule universe must be one of watchlist, symbols, market")
    _check_node(rule.get("when"), 1, 0)
    if rule.get("tier", "info") not in TIERS:
        raise RuleError("rule tier must be info, important or critical")
    cooldown = rule.get("cooldown_sec", 3600)
    if isinstance(cooldown, bool) or not isinstance(cooldown, int) or not 60 <= cooldown <= 7 * 86400:
        raise RuleError("cooldown_sec must be between 60 and 604800")
    allowed = {"version", "name", "universe", "when", "tier", "cooldown_sec"}
    if set(rule) - allowed:
        raise RuleError("unknown keys in rule: " + ", ".join(sorted(set(rule) - allowed)))
    return rule


def canonical(rule):
    return json.dumps(rule, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest(rule):
    return "sha256:" + hashlib.sha256(canonical(rule).encode("utf-8")).hexdigest()


def _fmt_value(value):
    if isinstance(value, list):
        return ", ".join(_fmt_value(v) for v in value)
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if isinstance(value, int) and abs(value) >= 10_000:
        return f"{value:,}"
    return str(value)


def _describe_node(node):
    if "all" in node or "any" in node:
        key = "all" if "all" in node else "any"
        parts = [_describe_node(c) for c in node[key]]
        joined = (" and " if key == "all" else " or ").join(parts)
        return f"({joined})" if len(parts) > 1 else joined
    metric = node["metric"].replace("_", " ")
    window = f" over {node['window']}" if node.get("window") else ""
    return f"{metric}{window} {OPS[node['op']]} {_fmt_value(node['value'])}"


def describe(rule):
    """Plain words, built from the structure rather than taken from the
    server's summary: what is described is what will be saved."""
    (kind, value), = rule["universe"].items()
    scope = ("symbols on your watchlist" if kind == "watchlist" else
             ", ".join(value) if kind == "symbols" else f"the {value} market")
    when = _describe_node(rule["when"])
    tier = rule.get("tier", "info")
    hours = rule.get("cooldown_sec", 3600) / 3600
    cooldown = f"{hours:g}h" if hours >= 1 else f"{rule.get('cooldown_sec', 3600) // 60}m"
    return f"{rule['name']}: alert ({tier}) for {scope} when {when}; at most once per {cooldown} per symbol."
