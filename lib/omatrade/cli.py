"""The `omatrade` command line. The bar plugin calls the same commands with
`--json`; anything a person can do in the panel they can do here."""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path

from . import (__version__, auth, config, daemon, data, desktop, policy, rules,
               state as statefile, urls)
from .http import ApiClient, ApiError, AuthExpired, HostViolation, NotSignedIn, OfflineError
from .keyring import Keyring, KeyringUnavailable

ROOT = Path(__file__).resolve().parent.parent.parent
CLI = ROOT / "bin" / "omatrade"
UNIT_NAME = "omatrade.service"


class CliError(Exception):
    def __init__(self, message, code="error", status=1):
        super().__init__(message)
        self.code = code
        self.status = status


def context():
    cfg = config.load()
    tokens = auth.TokenManager(cfg, Keyring())
    client = ApiClient(cfg, tokens)
    return cfg, tokens, client


def seg(value):
    return urllib.parse.quote(value, safe="")


def emit(args, payload, render=None):
    if getattr(args, "json", False) or render is None:
        json.dump(payload, sys.stdout, separators=(",", ":"))
        print()
    else:
        render(payload)


def require_configured(cfg):
    if not cfg.configured:
        raise CliError("Omatrade is not configured with a Questflow API yet. Set api_base and "
                       "client_id in ~/.config/omatrade/config.toml (or OMATRADE_API_BASE and "
                       "OMATRADE_CLIENT_ID).", "not_configured", 2)


def nudge(cfg, command="refresh"):
    daemon.poke(cfg, command)


# ── Formatting ───────────────────────────────────────────────────────────────

def fmt_price(v):
    if v is None:
        return "–"
    a = abs(v)
    return f"{v:,.2f}" if a >= 1 else f"{v:.4f}" if a >= 0.01 else f"{v:.6f}"


def fmt_money(v, signed=False):
    if v is None:
        return "–"
    sign = "+" if signed and v > 0 else "-" if v < 0 else ""
    return f"{sign}${abs(v):,.2f}"


def fmt_pct(v):
    return "–" if v is None else f"{'+' if v > 0 else ''}{v:.2f}%"


def ago(ts, now):
    if not ts:
        return "never"
    s = max(0, int(now - ts))
    if s < 60:
        return "now"
    if s < 3600:
        return f"{s // 60}m"
    if s < 86400:
        return f"{s // 3600}h"
    return f"{s // 86400}d"


def print_watchlist(wl):
    if wl.get("source") == "default":
        print("Default list. Sign in to keep your own: omatrade login\n")
    for sec in wl["sections"]:
        if sec["name"]:
            print(sec["name"].upper())
        for e in sec["entries"]:
            print(f"  {e['symbol']:<22} {e['name'][:30]:<30} {fmt_price(e['last']):>12} {fmt_pct(e['changePct']):>8}")
        print()


def print_signals(payload):
    now = time.time()
    if not payload["personal"]:
        print("Public signals. Sign in for your rules and subscriptions: omatrade login\n")
    for s in payload["items"]:
        mark = {"critical": "!!", "important": "! "}.get(s["tier"], "  ")
        dot = " " if s["read"] else "•"
        syms = ",".join(s["symbols"][:3])
        print(f"{mark}{dot} {ago(s['createdAt'], now):>4}  {syms:<14} {s['title'][:80]}  — {s['source']['name']}  [{s['id']}]")
    print("\n" + daemon.DISCLAIMER)


def print_positions(payload):
    now = time.time()
    t = payload["totals"]
    if not t:
        print("No positions in your Questflow accounts.")
        return
    print(f"Equity {fmt_money(t['equity'])}   day {fmt_money(t['dayPnl'], True)} ({fmt_pct(t['dayPnlPct'])})"
          f"   open P&L {fmt_money(t['openPnl'], True)}   risk {t['risk']}")
    for a in payload["accounts"]:
        stale = "  STALE" if a["stale"] else ""
        ratio = f"  margin {a['marginRatio'] * 100:.1f}%" if a["marginRatio"] else ""
        print(f"\n{a['name'].upper()}  {a['label']}  {fmt_money(a['equity'])}{ratio}  synced {ago(a['syncedAt'], now)}{stale}")
        for p in a["positions"]:
            label = p["display"] if p["kind"] != "prediction" else f"{p['display']} · {p['name']}"
            liq = (f"liq {fmt_price(p['liquidationPrice'])} ({p['distanceToLiqPct']:.1f}% away)"
                   if p["distanceToLiqPct"] is not None else "")
            print(f"  {label[:34]:<34} {p['sizeLabel']:<26} {fmt_price(p['last']):>12} "
                  f"{fmt_money(p['pnl'], True):>13}  {liq}")


def print_rules(payload):
    if not payload["rules"]:
        print('No rules yet. Describe one: omatrade rules add "BTC funding above 0.05% on 8h"')
    for r in payload["rules"]:
        print(f"[{'on ' if r['enabled'] else 'off'}] {r['id']}  {r['description']}")


def print_skills(payload):
    for s in payload["skills"]:
        print(f"[{'x' if s['subscribed'] else ' '}] {s['id']:<24} {s['name']}  {s['description'][:60]}")


# ── Commands ────────────────────────────────────────────────────────────────

def cmd_login(args, cfg, tokens, client):
    require_configured(cfg)

    def announce(url):
        print("Opening your browser to sign in with Questflow.", file=sys.stderr)
        print(f"If it does not open, visit:\n  {url}", file=sys.stderr)
        sys.stderr.flush()

    def opener(url):
        # Built by the client from the configured base, so it is pinned to it.
        subprocess.Popen(["xdg-open", url], stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)

    session = auth.login(cfg, client, tokens, opener=None if args.no_browser else opener,
                         announce=announce, timeout=args.timeout)
    name = ""
    try:
        name = data.profile(client.call("GET", "/v1/me", auth="required"))["displayName"]
    except (ApiError, OfflineError):
        pass
    nudge(cfg, "reload-auth")
    emit(args, {"ok": True, "displayName": name, "scopes": session["scope"].split()},
         lambda p: print(f"Signed in{' as ' + name if name else ''}. Scopes: {', '.join(p['scopes'])}"))


def cmd_logout(args, cfg, tokens, client):
    errors = tokens.logout(client)
    nudge(cfg, "reload-auth")
    emit(args, {"ok": True, "revokeErrors": errors},
         lambda p: print("Signed out." + (" (Could not reach Questflow to revoke the session; "
                                           "it was removed from this computer.)" if errors else "")))


def cmd_whoami(args, cfg, tokens, client):
    status = tokens.status()
    out = {"state": status["state"], "signedIn": status["signedIn"], "scopes": status.get("scopes", []),
           "apiBase": cfg.api_base, "displayName": "", "plan": ""}
    if status["signedIn"]:
        out.update(data.profile(client.call("GET", "/v1/me", auth="required")))
    emit(args, out, lambda p: print(
        f"Signed in as {p['displayName'] or '(unnamed)'} — scopes {' '.join(p['scopes'])}" if p["signedIn"]
        else {"expired": "Your session ended. Sign in again: omatrade login",
              "revoked": "Your session was revoked. Sign in again: omatrade login",
              "keyring_unavailable": KeyringUnavailable.MESSAGE}.get(p["state"], "Not signed in. Sign in: omatrade login")))


def cmd_watchlist(args, cfg, tokens, client):
    sub = args.wl_cmd
    signed_in = tokens.status()["signedIn"]
    if sub == "search":
        results = data.search_results(client.call("GET", "/v1/public/symbols",
                                                  query={"q": args.text[:64]}, auth="none"))
        emit(args, {"results": results, "error": ""},
             lambda p: [print(f"{r['symbol']:<24} {r['type']:<10} {r['name']}") for r in p["results"]])
        return
    if sub in ("add", "remove"):
        sym = data.symbol(args.symbol)
        if not sym:
            raise CliError("use EXCHANGE:SYMBOL (e.g. NASDAQ:AAPL)", "bad_symbol")
        if not signed_in:
            raise NotSignedIn()
        if sub == "add":
            client.call("PUT", f"/v1/watchlist/items/{seg(sym)}", body={"section": args.section}, auth="required")
        else:
            client.call("DELETE", f"/v1/watchlist/items/{seg(sym)}", auth="required")
        nudge(cfg)
    raw = client.call("GET", "/v1/watchlist" if signed_in else "/v1/public/watchlist",
                      auth="required" if signed_in else "none")
    wl, _ = data.watchlist(raw)
    emit(args, wl, print_watchlist)


def cmd_signals(args, cfg, tokens, client):
    signed_in = tokens.status()["signedIn"]
    raw = client.call("GET", "/v1/signals" if signed_in else "/v1/public/signals",
                      query={"limit": args.limit}, auth="required" if signed_in else "none")
    items, dropped = data.signal_list(raw.get("signals") if isinstance(raw, dict) else None)
    if args.unread:
        items = [s for s in items if not s["read"]]
    emit(args, {"items": items, "personal": signed_in, "dropped": dropped,
                "disclaimer": daemon.DISCLAIMER}, print_signals)


def cmd_ack(args, cfg, tokens, client):
    ids = [i for i in args.ids if data.sid(i)]
    if not ids:
        raise CliError("no valid signal ids", "bad_id")
    if not tokens.status()["signedIn"]:
        emit(args, {"ok": True, "acked": []}, lambda p: None)
        return
    client.call("POST", "/v1/signals/ack", body={"ids": ids}, auth="required")
    nudge(cfg)
    emit(args, {"ok": True, "acked": ids}, lambda p: print(f"Marked {len(ids)} read."))


def cmd_skills(args, cfg, tokens, client):
    if args.sk_cmd in ("subscribe", "unsubscribe"):
        skill = data.sid(args.id)
        if not skill:
            raise CliError("not a skill id", "bad_id")
        client.call("PUT" if args.sk_cmd == "subscribe" else "DELETE",
                    f"/v1/subscriptions/{seg(skill)}", auth="required")
        nudge(cfg)
    emit(args, {"skills": data.skills(client.call("GET", "/v1/skills", auth="required"))}, print_skills)


def confirm(prompt, assume_yes):
    if assume_yes:
        return True
    if not sys.stdin.isatty():
        raise CliError("confirmation required: run in a terminal, or pass --yes after reviewing",
                       "confirmation_required")
    return input(prompt + " [y/N] ").strip().lower() in ("y", "yes")


def draft_rule(client, text):
    text = " ".join(text).strip()
    if not text or len(text) > 500:
        raise CliError("describe the rule in 1 to 500 characters", "bad_rule_text")
    raw = client.call("POST", "/v1/rules/drafts", body={"text": text}, auth="required")
    draft = raw.get("draft") if isinstance(raw, dict) else None
    if not isinstance(draft, dict) or not data.sid(draft.get("id")):
        raise CliError("Questflow returned an unreadable draft", "malformed_response")
    body = rules.validate(draft.get("rule"))
    return {"draftId": draft["id"], "rule": body, "digest": rules.digest(body),
            "description": rules.describe(body),
            "serverSummary": data.text(draft.get("summary"), 300),
            "expiresAt": data.epoch(draft.get("expires_at"))}


def save_rule(client, draft_id, digest):
    if not data.sid(draft_id) or not isinstance(digest, str) or not digest.startswith("sha256:"):
        raise CliError("bad draft id or digest", "bad_draft")
    raw = client.call("POST", "/v1/rules", body={"draft_id": draft_id, "rule_digest": digest},
                      auth="required")
    record = data.rule_record(raw.get("rule") if isinstance(raw, dict) else None)
    if not record:
        raise CliError("Questflow returned an unreadable rule", "malformed_response")
    if record["rule"] is None or rules.digest(record["rule"]) != digest:
        raise CliError("the saved rule differs from the one you confirmed; check `omatrade rules`",
                       "digest_mismatch")
    return record


def cmd_rules(args, cfg, tokens, client):
    sub = args.ru_cmd
    if sub == "draft":
        emit(args, draft_rule(client, args.text))
        return
    if sub == "confirm":
        record = save_rule(client, args.draft_id, args.digest)
        nudge(cfg)
        emit(args, {"ok": True, "rule": record}, lambda p: print("Saved: " + record["description"]))
        return
    if sub == "add":
        d = draft_rule(client, args.text)
        if not args.json:
            print("Questflow understood:\n  " + d["description"])
            print(f"  (exact rule: {rules.canonical(d['rule'])})")
        if not confirm("Save this rule?", args.yes):
            raise CliError("not saved", "cancelled", 1)
        record = save_rule(client, d["draftId"], d["digest"])
        nudge(cfg)
        emit(args, {"ok": True, "rule": record}, lambda p: print("Saved as " + record["id"]))
        return
    if sub in ("enable", "disable", "delete"):
        rid = data.sid(args.id)
        if not rid:
            raise CliError("not a rule id", "bad_id")
        if sub == "delete":
            if not confirm(f"Delete rule {rid}?", args.yes):
                raise CliError("not deleted", "cancelled", 1)
            client.call("DELETE", f"/v1/rules/{seg(rid)}", auth="required")
        else:
            client.call("PATCH", f"/v1/rules/{seg(rid)}", body={"enabled": sub == "enable"}, auth="required")
        nudge(cfg)
    emit(args, {"rules": data.rule_list(client.call("GET", "/v1/rules", auth="required"))}, print_rules)


def cmd_positions(args, cfg, tokens, client):
    now = time.time()
    accts = data.accounts(client.call("GET", "/v1/positions", auth="required"), now)
    emit(args, {"accounts": accts, "totals": data.totals(accts), "syncedAt": int(now)}, print_positions)


def parse_quiet(value):
    if value in ("off", "none", ""):
        return "", ""
    start, sep, end = value.partition("-")
    if not sep or policy.parse_hhmm(start) is None or policy.parse_hhmm(end) is None:
        raise CliError("quiet hours look like 22:00-07:00, or off", "bad_quiet_hours")
    return start, end


def cmd_notify_settings(args, cfg, tokens, client):
    path = daemon.settings_path(cfg)
    raw = statefile.load_json(path, {})
    notify = policy.NotifySettings.from_dict(raw.get("notify")).to_dict()
    if args.enabled is not None:
        notify["enabled"] = args.enabled == "on"
    if args.quiet_hours is not None:
        notify["quietStart"], notify["quietEnd"] = parse_quiet(args.quiet_hours)
    if args.daily_cap is not None:
        notify["dailyCap"] = args.daily_cap
    if args.min_tier is not None:
        notify["minTier"] = args.min_tier
    raw["notify"] = policy.NotifySettings.from_dict(notify).to_dict()
    if args.poll_interval is not None:
        raw["pollIntervalSec"] = max(15, min(1800, args.poll_interval))
    statefile.save_json(path, raw)
    nudge(cfg, "settings")
    emit(args, raw, lambda p: print(json.dumps(p, indent=2)))


def cmd_summary(args, cfg, tokens, client):
    snap = statefile.load_json(daemon.snapshot_path(cfg), {})
    if not snap:
        snap = daemon.empty_snapshot(cfg)
        snap["daemon"] = {"running": False}
    else:
        snap.setdefault("daemon", {})["running"] = daemon.running(cfg)
    if not args.json:
        print_status(cfg, tokens, snap)
        return
    emit(args, snap)


def print_status(cfg, tokens, snap):
    now = time.time()
    conn = snap.get("connection", {})
    print(f"Omatrade {__version__}  ·  API {cfg.api_base}{'' if cfg.configured else ' (not configured)'}")
    print(f"Account: {tokens.status()['state'].replace('_', ' ')}  ·  daemon "
          f"{'running' if snap.get('daemon', {}).get('running') else 'stopped (omatrade daemon ensure)'}  ·  "
          f"{conn.get('mode', 'connecting')}{'  ·  STALE' if snap.get('stale') else ''}  ·  "
          f"updated {ago((snap.get('updatedAt') or 0) / 1000, now)}")
    if conn.get("error"):
        print(conn["error"])
    print()
    if snap.get("watchlist", {}).get("sections"):
        print_watchlist(snap["watchlist"])
    if snap.get("signals", {}).get("items"):
        print_signals(snap["signals"])
    if snap.get("positions", {}).get("available"):
        print()
        print_positions(snap["positions"])


def unit_text():
    template = (ROOT / "systemd" / UNIT_NAME).read_text()
    return template.replace("@OMATRADE_BIN@", str(CLI))


def cmd_daemon(args, cfg, tokens, client):
    sub = args.dm_cmd or "status"
    if sub == "run":
        require_configured(cfg)
        code = daemon.Daemon(cfg, client, tokens).run()
        if code:
            print("omatrade daemon is already running", file=sys.stderr)
        return code
    if sub == "poke":
        delivered = daemon.poke(cfg, args.what)
        emit(args, {"ok": True, "delivered": delivered},
             lambda p: print("ok" if delivered else "no daemon is running"))
        return 0
    if sub == "status":
        out = {"running": daemon.running(cfg), "unitInstalled": unit_path().exists(), "configured": cfg.configured}
        emit(args, out, lambda p: print(json.dumps(p)))
        return 0
    if sub == "install":
        path = unit_path()
        statefile.ensure_dir(path.parent)
        path.write_text(unit_text())
        if shutil.which("systemctl"):
            subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
            subprocess.run(["systemctl", "--user", "enable", "--now", UNIT_NAME], check=False)
        print(f"installed {path}")
        return 0
    if sub == "uninstall":
        if shutil.which("systemctl"):
            subprocess.run(["systemctl", "--user", "disable", "--now", UNIT_NAME], check=False)
        unit_path().unlink(missing_ok=True)
        daemon.poke(cfg, "stop")
        print("uninstalled")
        return 0
    if sub == "ensure":
        if not cfg.configured or daemon.running(cfg):
            return 0
        if shutil.which("systemctl") and unit_path().exists():
            r = subprocess.run(["systemctl", "--user", "start", UNIT_NAME], check=False,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if r.returncode == 0:
                return 0
        subprocess.Popen([sys.executable, "-I", str(CLI), "daemon", "run"],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
        return 0
    return 0


def unit_path():
    base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "systemd" / "user" / UNIT_NAME


def cmd_open(args, cfg, tokens, client):
    target = urls.safe_url(args.target) if args.target else ""
    desktop.open_url(target or cfg.web_base)


def cmd_config(args, cfg, tokens, client):
    emit(args, cfg.public(), lambda p: [print(f"{k}: {v}") for k, v in p.items()])


# ── Parser ──────────────────────────────────────────────────────────────────

def parser():
    p = argparse.ArgumentParser(prog="omatrade", description=(
        "Questflow on the Omarchy desktop: watchlist, signals and positions. "
        "Sign in once with `omatrade login`; nothing else to connect."))
    p.add_argument("--version", action="version", version=f"omatrade {__version__}")
    sub = p.add_subparsers(dest="cmd")

    def add(name, help_text, json_flag=True):
        sp = sub.add_parser(name, help=help_text)
        if json_flag:
            sp.add_argument("--json", action="store_true")
        return sp

    sp = add("login", "sign in with Questflow (opens your browser)")
    sp.add_argument("--no-browser", action="store_true", help="print the sign-in URL instead of opening it")
    sp.add_argument("--timeout", type=int, default=300)
    add("logout", "sign out and revoke the session")
    add("whoami", "who is signed in")
    add("summary", "what the bar shows (local snapshot, no network)")
    add("status", "same as summary, for people")

    sp = add("watchlist", "your watchlist")
    ws = sp.add_subparsers(dest="wl_cmd")
    x = ws.add_parser("search"); x.add_argument("text"); x.add_argument("--json", action="store_true")
    x = ws.add_parser("add"); x.add_argument("symbol"); x.add_argument("--section"); x.add_argument("--json", action="store_true")
    x = ws.add_parser("remove"); x.add_argument("symbol"); x.add_argument("--json", action="store_true")

    sp = add("signals", "your signals (public signals when signed out)")
    sp.add_argument("--unread", action="store_true")
    sp.add_argument("--limit", type=int, default=50)
    sp = add("ack", "mark signals read on every device")
    sp.add_argument("ids", nargs="+")

    sp = add("skills", "skill signal feeds and your subscriptions")
    ss = sp.add_subparsers(dest="sk_cmd")
    for name in ("subscribe", "unsubscribe"):
        x = ss.add_parser(name); x.add_argument("id"); x.add_argument("--json", action="store_true")

    sp = add("rules", "your alert rules")
    rs = sp.add_subparsers(dest="ru_cmd")
    x = rs.add_parser("add", help="describe a rule in words; you confirm before it is saved")
    x.add_argument("text", nargs="+"); x.add_argument("--yes", action="store_true"); x.add_argument("--json", action="store_true")
    x = rs.add_parser("draft", help="(for the panel) draft a rule without saving")
    x.add_argument("text", nargs="+"); x.add_argument("--json", action="store_true")
    x = rs.add_parser("confirm", help="(for the panel) save a confirmed draft")
    x.add_argument("draft_id"); x.add_argument("--digest", required=True); x.add_argument("--json", action="store_true")
    for name in ("enable", "disable", "delete"):
        x = rs.add_parser(name); x.add_argument("id"); x.add_argument("--json", action="store_true")
        if name == "delete":
            x.add_argument("--yes", action="store_true")

    add("positions", "positions in your Questflow accounts (read-only)")

    sp = add("notify-settings", "notification tiers, quiet hours and the daily cap")
    sp.add_argument("--enabled", choices=("on", "off"))
    sp.add_argument("--quiet-hours", metavar="HH:MM-HH:MM|off")
    sp.add_argument("--daily-cap", type=int)
    sp.add_argument("--min-tier", choices=policy.TIERS)
    sp.add_argument("--poll-interval", type=int, help="seconds between polls when the stream is down")

    sp = add("daemon", "the background process that holds the stream")
    ds = sp.add_subparsers(dest="dm_cmd")
    for name in ("run", "ensure", "install", "uninstall", "status"):
        x = ds.add_parser(name); x.add_argument("--json", action="store_true")
    x = ds.add_parser("poke"); x.add_argument("what", choices=sorted(daemon.CONTROL_COMMANDS)); x.add_argument("--json", action="store_true")

    sp = add("open", "open an https link, or Questflow", json_flag=False)
    sp.add_argument("target", nargs="?", default="")
    add("config", "show the effective configuration (no secrets)")
    return p


COMMANDS = {
    "login": cmd_login, "logout": cmd_logout, "whoami": cmd_whoami,
    "summary": cmd_summary, "status": cmd_summary, "watchlist": cmd_watchlist,
    "signals": cmd_signals, "ack": cmd_ack, "skills": cmd_skills, "rules": cmd_rules,
    "positions": cmd_positions, "notify-settings": cmd_notify_settings,
    "daemon": cmd_daemon, "open": cmd_open, "config": cmd_config,
}


def fail(args, message, code, status):
    if getattr(args, "json", False):
        json.dump({"ok": False, "error": message, "code": code}, sys.stdout)
        print()
    else:
        print(f"omatrade: {message}", file=sys.stderr)
    return status


def main(argv=None):
    args = parser().parse_args(argv)
    cmd = args.cmd or "status"
    if cmd == "status":
        args.json = False
    try:
        cfg, tokens, client = context()
        return COMMANDS[cmd](args, cfg, tokens, client) or 0
    except config.ConfigError as e:
        return fail(args, f"configuration: {e}", "config", 2)
    except CliError as e:
        return fail(args, str(e), e.code, e.status)
    except NotSignedIn:
        return fail(args, "not signed in. Sign in with Questflow: omatrade login", "not_signed_in", 3)
    except AuthExpired:
        return fail(args, "your Questflow session has ended. Sign in again: omatrade login", "session_expired", 3)
    except auth.AuthError as e:
        return fail(args, str(e), "auth", 3)
    except KeyringUnavailable as e:
        return fail(args, str(e), "keyring_unavailable", 4)
    except OfflineError as e:
        return fail(args, f"Questflow is unreachable ({e})", "offline", 5)
    except ApiError as e:
        if e.status == 429:
            wait = f" in {e.retry_after}s" if e.retry_after is not None else " later"
            return fail(args, f"rate limited by Questflow; try again{wait}", "rate_limited", 6)
        if e.code == "malformed_response":
            return fail(args, "Questflow answered with something unreadable", "malformed_response", 7)
        return fail(args, f"Questflow error: {e.code}{(' — ' + e.message) if e.message else ''}", e.code, 1)
    except rules.RuleError as e:
        return fail(args, f"the drafted rule is not valid: {e}", "invalid_rule", 1)
    except HostViolation as e:
        return fail(args, str(e), "host_violation", 1)
    except KeyboardInterrupt:
        return 130
