"""Turning API payloads into the shapes the bar draws.

Everything from the network is treated as untrusted: unknown fields are
dropped, wrong types fall back to safe defaults, text is length-capped,
links must be https, and an item without an id is discarded rather than
guessed at. The number of dropped items is reported, so a malformed feed is
visible instead of silent.
"""

import datetime as dt
import math
import re

from . import risk, rules, urls

SYMBOL_RE = re.compile(r"^[A-Z0-9_]+:[A-Z0-9_.!&/-]+$")
ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
SIGNAL_TYPES = {"news", "price", "volume", "rule_match", "skill", "funding", "odds_shift",
                "whale_flow", "risk", "earnings", "unlock"}
KINDS = {"stock", "perp", "spot", "prediction", "option", "future"}
SIDES = {"long", "short"}


def text(value, limit=300):
    if not isinstance(value, str):
        return ""
    value = "".join(ch for ch in value if ch >= " " or ch == "\n")
    return value[:limit]


def num(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def sid(value):
    return value if isinstance(value, str) and ID_RE.match(value) else None


def symbol(value):
    if isinstance(value, str) and SYMBOL_RE.match(value.upper()):
        return value.upper()
    return None


def epoch(value):
    """RFC 3339 (`2026-10-20T08:00:00Z`) or epoch seconds, to epoch seconds."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and math.isfinite(value):
        return int(value / 1000) if value > 1e12 else int(value)
    if isinstance(value, str) and value:
        try:
            parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        return int(parsed.timestamp())
    return None


# ── Signals ──────────────────────────────────────────────────────────────────

def signal(raw):
    if not isinstance(raw, dict):
        return None
    ident = sid(raw.get("id"))
    title = text(raw.get("title"), 200)
    if not ident or not title:
        return None
    source = raw.get("source") if isinstance(raw.get("source"), dict) else {}
    tier = raw.get("tier") if raw.get("tier") in ("info", "important", "critical") else "info"
    kind = raw.get("type") if raw.get("type") in SIGNAL_TYPES else "skill"
    strength = num(raw.get("strength"))
    syms = []
    for s in ([raw.get("symbol")] + (raw.get("symbols") if isinstance(raw.get("symbols"), list) else [])):
        s = symbol(s)
        if s and s not in syms:
            syms.append(s)
    return {
        "id": ident,
        "symbol": syms[0] if syms else "",
        "symbols": [s.split(":", 1)[1] for s in syms[:4]],
        "type": kind,
        "strength": None if strength is None else max(0.0, min(1.0, strength)),
        "source": {
            "kind": source.get("kind") if source.get("kind") in ("skill", "rule", "news", "risk") else "skill",
            "id": sid(source.get("id")) or "",
            "name": text(source.get("name"), 80),
        },
        "title": title,
        "body": text(raw.get("body"), 600),
        "createdAt": epoch(raw.get("created_at")) or 0,
        "expiresAt": epoch(raw.get("expires_at")),
        "url": urls.safe_url(raw.get("url")),
        "tier": tier,
        "ruleId": sid(raw.get("rule_id")) or "",
        "read": raw.get("read") is True,
    }


def signal_list(items, limit=100):
    out, dropped, seen = [], 0, set()
    for raw in items if isinstance(items, list) else []:
        s = signal(raw)
        if s is None or s["id"] in seen:
            dropped += 1
            continue
        seen.add(s["id"])
        out.append(s)
    out.sort(key=lambda s: s["createdAt"], reverse=True)
    return out[:limit], dropped


# ── Watchlist ────────────────────────────────────────────────────────────────

def watchlist(raw):
    raw = raw if isinstance(raw, dict) else {}
    sections, dropped = [], 0
    for sec in raw.get("sections") if isinstance(raw.get("sections"), list) else []:
        if not isinstance(sec, dict):
            dropped += 1
            continue
        entries = []
        for e in sec.get("entries") if isinstance(sec.get("entries"), list) else []:
            sym = symbol(e.get("symbol")) if isinstance(e, dict) else None
            if not sym:
                dropped += 1
                continue
            q = e.get("quote") if isinstance(e.get("quote"), dict) else {}
            exchange, _, ticker = sym.partition(":")
            entries.append({
                "symbol": sym, "display": ticker, "exchange": exchange,
                "name": text(e.get("name"), 80),
                "last": num(q.get("last")), "change": num(q.get("change")),
                "changePct": num(q.get("change_pct")),
                "currency": text(q.get("currency"), 8),
                "delayedSec": int(num(q.get("delay_sec")) or 0),
                "found": q.get("last") is not None,
                "url": urls.safe_url(e.get("url")),
            })
        if entries:
            sections.append({"name": text(sec.get("name"), 40), "entries": entries})
    return {"sections": sections, "editable": raw.get("editable") is True,
            "source": "account" if raw.get("editable") is True else "default"}, dropped


def search_results(raw, limit=10):
    out = []
    for r in (raw.get("results") if isinstance(raw, dict) and isinstance(raw.get("results"), list) else []):
        sym = symbol(r.get("symbol")) if isinstance(r, dict) else None
        if not sym:
            continue
        exchange, _, ticker = sym.partition(":")
        out.append({"symbol": sym, "display": ticker, "exchange": exchange,
                    "name": text(r.get("name"), 80), "type": text(r.get("type"), 16)})
        if len(out) >= limit:
            break
    return out


# ── Skills and rules ────────────────────────────────────────────────────────

def skills(raw):
    out = []
    for s in (raw.get("skills") if isinstance(raw, dict) and isinstance(raw.get("skills"), list) else []):
        ident = sid(s.get("id")) if isinstance(s, dict) else None
        if not ident:
            continue
        out.append({"id": ident, "name": text(s.get("name"), 80) or ident,
                    "description": text(s.get("description"), 200),
                    "subscribed": s.get("subscribed") is True})
    return out


def rule_record(r):
    if not isinstance(r, dict) or not sid(r.get("id")):
        return None
    try:
        body = rules.validate(r.get("rule"))
        description = rules.describe(body)
    except rules.RuleError:
        body, description = None, "(this rule could not be read by this version of Omatrade)"
    return {"id": r["id"], "enabled": r.get("enabled") is not False,
            "name": text(body["name"] if body else r.get("name"), 120),
            "description": description, "rule": body,
            "createdAt": epoch(r.get("created_at")) or 0,
            "lastMatchedAt": epoch(r.get("last_matched_at"))}


def rule_list(raw):
    items = raw.get("rules") if isinstance(raw, dict) and isinstance(raw.get("rules"), list) else []
    return [r for r in (rule_record(x) for x in items) if r]


# ── Positions ───────────────────────────────────────────────────────────────

def position(raw, now):
    if not isinstance(raw, dict) or not sid(raw.get("id")):
        return None
    kind = raw.get("kind") if raw.get("kind") in KINDS else "spot"
    side = raw.get("side") if raw.get("side") in SIDES else "long"
    mark, entry, liq = num(raw.get("mark_price")), num(raw.get("entry_price")), num(raw.get("liquidation_price"))
    size, lev = num(raw.get("size")), num(raw.get("leverage"))
    pnl, value = num(raw.get("unrealized_pnl")), num(raw.get("value"))
    distance = risk.distance_to_liquidation_pct(side, mark, liq)
    cost = abs((entry or 0) * (size or 0)) / (lev or 1)
    spark = [v for v in (raw.get("spark") if isinstance(raw.get("spark"), list) else []) if num(v) is not None][:96]
    display = text(raw.get("display"), 24) or (symbol(raw.get("symbol")) or "").split(":")[-1]
    lev_label = f"{lev:g}x " if lev and lev > 1 else ""
    size_label = (f"{lev_label}{side.capitalize()} {abs(size):g} @ {entry:,.2f}"
                  if size is not None and entry is not None else "")
    return {
        "id": raw["id"], "symbol": text(raw.get("symbol"), 40), "display": display,
        "name": text(raw.get("name"), 120), "kind": kind, "side": side,
        "size": size, "sizeLabel": size_label, "avg": entry, "last": mark,
        "leverage": lev, "value": value, "pnl": pnl,
        "pnlPct": round(pnl / cost * 100, 2) if pnl is not None and cost else None,
        "dayChangePct": num(raw.get("day_change_pct")),
        "liquidationPrice": liq, "distanceToLiqPct": distance,
        "risk": risk.risk_level(distance),
        "spark": spark,
    }


def account(raw, now):
    if not isinstance(raw, dict) or not sid(raw.get("id")):
        return None
    positions = [p for p in (position(x, now) for x in (raw.get("positions") if isinstance(raw.get("positions"), list) else [])) if p]
    equity = num(raw.get("equity"))
    ratio = risk.margin_ratio(raw.get("maintenance_margin"), equity)
    synced = epoch(raw.get("synced_at"))
    stale_after = int(num(raw.get("stale_after_sec")) or risk.DEFAULT_STALE_AFTER_SEC)
    level = risk.worst([risk.risk_level(None, ratio)] + [p["risk"] for p in positions])
    return {
        "id": raw["id"], "venue": text(raw.get("venue"), 24), "name": text(raw.get("name"), 60),
        "label": text(raw.get("label"), 40), "equity": equity,
        "dayPnl": num(raw.get("day_pnl")), "dayPnlPct": num(raw.get("day_pnl_pct")),
        "marginRatio": ratio, "risk": level, "syncedAt": synced,
        "stale": risk.is_stale(synced, now, stale_after), "positions": positions,
    }


def accounts(raw, now):
    items = raw.get("accounts") if isinstance(raw, dict) and isinstance(raw.get("accounts"), list) else []
    return [a for a in (account(x, now) for x in items) if a]


def totals(accts):
    if not accts:
        return None
    eq = sum(a["equity"] or 0 for a in accts)
    day = sum(a["dayPnl"] or 0 for a in accts)
    return {
        "equity": round(eq, 2), "dayPnl": round(day, 2),
        "dayPnlPct": round(day / max(1, eq - day) * 100, 2),
        "openPnl": round(sum(p["pnl"] or 0 for a in accts for p in a["positions"]), 2),
        "positions": sum(len(a["positions"]) for a in accts),
        "risk": risk.worst([a["risk"] for a in accts]),
        "stale": any(a["stale"] for a in accts),
        "oldestSyncAt": min((a["syncedAt"] for a in accts if a["syncedAt"]), default=None),
    }


def profile(raw):
    raw = raw if isinstance(raw, dict) else {}
    return {"displayName": text(raw.get("display_name"), 80), "plan": text(raw.get("plan"), 24)}
