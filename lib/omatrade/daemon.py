"""`omatrade daemon run`: the one long-lived process.

Why a daemon (shipped as a systemd user unit) rather than the bar spawning
the CLI every minute:

- Push needs a connection that stays open. A process that starts, fetches
  and exits cannot hold a server-sent-events stream, so it can only poll;
  polling every client every minute is also the most expensive way to serve
  tens of thousands of desktops.
- One process per user means one stream, one notification per signal and
  one refresh at a time, however many monitors have a bar. The bar's
  service only reads the snapshot this process writes.
- systemd restarts it on failure, and it keeps notifying while the shell
  reloads. It holds no listening network socket: the only listener is a
  0600 Unix socket for local "refresh" nudges, checked by peer uid.

The loop: a reader thread owns the SSE stream (reconnecting with jittered
exponential backoff, resuming with Last-Event-ID) and hands events to the
main thread through a queue. The main thread applies them to the snapshot,
decides notifications, and while the stream is down falls back to polling
the REST API. Each successful sync is cached in the snapshot, so a restart
or an offline laptop still shows the last data, marked stale.
"""

import contextlib
import fcntl
import json
import os
import queue
import socket
import struct
import threading
import time

from . import __version__, data, desktop, policy, sse, state as statefile, theme
from .http import ApiError, AuthExpired, NotSignedIn, OfflineError
from .keyring import KeyringUnavailable

DISCLAIMER = "Not investment advice. Signals are informational and may be delayed or wrong."
CONTROL_COMMANDS = {"refresh", "reload-auth", "settings", "stop"}
FULL_SYNC_EVERY = 15 * 60
PERSONAL = ("profile", "watchlist", "signals", "skills", "rules", "positions")
PUBLIC = ("watchlist", "signals")


def _env_float(name, default):
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


def snapshot_path(cfg):
    return cfg.state_dir / "state.json"


def settings_path(cfg):
    return cfg.config_dir / "settings.json"


def load_settings(cfg):
    raw = statefile.load_json(settings_path(cfg), {})
    notify = policy.NotifySettings.from_dict(raw.get("notify"))
    try:
        poll = max(15, min(1800, int(raw.get("pollIntervalSec", 60))))
    except (TypeError, ValueError):
        poll = 60
    return notify, poll


def empty_snapshot(cfg):
    return {
        "version": 2,
        "updatedAt": 0,
        "colors": None,
        "config": {"configured": cfg.configured, "webBase": cfg.web_base},
        "auth": {"state": "signed_out", "signedIn": False, "displayName": "", "scopes": []},
        "connection": {"mode": "connecting", "since": 0, "lastEventAt": 0, "lastSyncAt": 0,
                       "retryAt": 0, "error": ""},
        "stale": True,
        "watchlist": {"sections": [], "editable": False, "source": "default", "syncedAt": 0, "error": ""},
        "signals": {"items": [], "unread": 0, "personal": False, "syncedAt": 0, "error": "",
                    "disclaimer": DISCLAIMER},
        "skills": [],
        "rules": [],
        "positions": {"available": False, "accounts": [], "totals": None, "syncedAt": 0,
                      "stale": False, "error": ""},
        "notify": policy.NotifySettings().to_dict(),
        "malformed": 0,
        "daemon": {},
    }


class Daemon:
    def __init__(self, cfg, client, tokens, *, clock=time.time, notifier=None, poke=None,
                 rng=None, theme_dir=None):
        self.cfg = cfg
        self.client = client
        self.tokens = tokens
        self.clock = clock
        self.notifier = notifier or desktop.send_notification
        self.poke = poke or desktop.poke_bar
        self.theme_dir = theme_dir or theme.THEME_DIR
        self.q = queue.Queue()
        self.stop_event = threading.Event()
        self.wake_stream = threading.Event()
        self.backoff = sse.Backoff(base=_env_float("OMATRADE_BACKOFF_BASE", 1.0),
                                   cap=_env_float("OMATRADE_BACKOFF_CAP", 300.0), rng=rng)
        self.stream_timeout = _env_float("OMATRADE_STREAM_TIMEOUT", 45.0)
        self.snap = empty_snapshot(cfg)
        self.ledger = policy.Ledger()
        self.notify_settings, self.poll_interval = load_settings(cfg)
        self.mode = "connecting"
        self.last_event_id = None
        self._resp = None
        self._dirty = True
        self._next_poll = 0
        self._next_full = 0
        self._last_publish = 0
        self._mtimes = {}
        self._signed_in = False
        self.notified = []  # for tests and `daemon status`

    # ── Persistence ─────────────────────────────────────────────────────
    def load(self):
        cached = statefile.load_json(snapshot_path(self.cfg), {})
        if cached.get("version") == 2:
            base = empty_snapshot(self.cfg)
            for key in base:
                if key in cached and type(cached[key]) is type(base[key]):
                    base[key] = cached[key]
            self.snap = base
        self.snap["config"] = {"configured": self.cfg.configured, "webBase": self.cfg.web_base}
        self.snap["stale"] = True
        self.snap["connection"]["mode"] = "connecting"
        self.last_event_id = (statefile.load_json(self.cfg.state_dir / "stream.json", {}) or {}).get("lastEventId")
        self.ledger = policy.Ledger(statefile.load_json(self.cfg.state_dir / "ledger.json", {}))
        self.snap["notify"] = self.notify_settings.to_dict()
        self.snap["colors"] = theme.theme_colors(self.theme_dir)
        self.snap["daemon"] = {"pid": os.getpid(), "startedAt": int(self.clock()), "version": __version__}

    def publish(self, force=False):
        now = self.clock()
        if not self._dirty and not force:
            return
        self.snap["updatedAt"] = int(now * 1000)
        self.snap["stale"] = self._is_stale(now)
        statefile.save_json(snapshot_path(self.cfg), self.snap)
        statefile.save_json(self.cfg.state_dir / "ledger.json", self.ledger.to_dict())
        if self.last_event_id:
            statefile.save_json(self.cfg.state_dir / "stream.json", {"lastEventId": self.last_event_id})
        self._dirty = False
        # The bar also reloads on its own timer; the nudge just makes it now.
        if now - self._last_publish >= 2.0 or force:
            self._last_publish = now
            self.poke()

    def _is_stale(self, now):
        conn = self.snap["connection"]
        fresh = max(conn.get("lastSyncAt") or 0, conn.get("lastEventAt") or 0)
        return self.mode in ("offline", "connecting") or now - fresh > max(120, 3 * self.poll_interval)

    def _set_mode(self, mode, error=None):
        conn = self.snap["connection"]
        if mode != self.mode:
            self.mode = mode
            conn["mode"] = mode
            conn["since"] = int(self.clock())
            self._dirty = True
        if error is not None and conn.get("error") != error:
            conn["error"] = error
            self._dirty = True

    # ── Auth state ──────────────────────────────────────────────────────
    def refresh_auth(self):
        status = self.tokens.status()
        was = self._signed_in
        self._signed_in = status["signedIn"]
        auth = self.snap["auth"]
        auth.update({"state": status["state"], "signedIn": status["signedIn"],
                     "scopes": status.get("scopes", [])})
        if not self._signed_in:
            auth["displayName"] = ""
            self._clear_personal()
        if was != self._signed_in:
            self.last_event_id = None
        self._dirty = True
        return self._signed_in

    def _clear_personal(self):
        self.snap["skills"] = []
        self.snap["rules"] = []
        self.snap["positions"] = empty_snapshot(self.cfg)["positions"]
        if self.snap["signals"].get("personal"):
            self.snap["signals"].update({"items": [], "unread": 0, "personal": False})

    def _session_ended(self):
        self.refresh_auth()
        self.snap["auth"]["state"] = "expired"
        self._dirty = True

    # ── REST sync ───────────────────────────────────────────────────────
    def sync(self, resources=None):
        """Fetch resources over REST. True if the API answered at all."""
        signed_in = self.refresh_auth()
        wanted = resources or (PERSONAL if signed_in else PUBLIC)
        now = self.clock()
        answered = False
        for name in wanted:
            if name not in (PERSONAL if signed_in else PUBLIC):
                continue
            try:
                getattr(self, "_sync_" + name)(signed_in, now)
                answered = True
            except (NotSignedIn, AuthExpired):
                self._session_ended()
                return self.sync(PUBLIC)
            except OfflineError as e:
                self._set_mode("offline", f"Questflow unreachable ({e}); showing last data")
                return answered
            except ApiError as e:
                answered = True
                if e.status == 429:
                    wait = e.retry_after or self.poll_interval
                    self._next_poll = now + wait
                    self.snap["connection"]["retryAt"] = int(now + wait)
                    self._set_mode("rate_limited", f"Rate limited by Questflow; retrying in {wait}s")
                    return answered
                self._resource_error(name, f"{e.code}")
            except KeyringUnavailable:
                self.snap["auth"]["state"] = "keyring_unavailable"
                self._dirty = True
                return answered
        if answered:
            self.snap["connection"]["lastSyncAt"] = int(now)
            if self.mode in ("offline", "connecting", "rate_limited"):
                self._set_mode("polling", "")
            elif self.snap["connection"].get("error"):
                self._set_mode(self.mode, "")
            self._dirty = True
        return answered

    def _resource_error(self, name, message):
        key = {"profile": "auth", "skills": None, "rules": None}.get(name, name)
        if key and isinstance(self.snap.get(key), dict):
            self.snap[key]["error"] = message
        self._dirty = True

    def _count_dropped(self, dropped):
        if dropped:
            self.snap["malformed"] = self.snap.get("malformed", 0) + dropped

    def _sync_profile(self, signed_in, now):
        me = data.profile(self.client.call("GET", "/v1/me", auth="required"))
        self.snap["auth"]["displayName"] = me["displayName"]

    def _sync_watchlist(self, signed_in, now):
        raw = self.client.call("GET", "/v1/watchlist" if signed_in else "/v1/public/watchlist",
                               auth="required" if signed_in else "none")
        wl, dropped = data.watchlist(raw)
        self._count_dropped(dropped)
        wl.update({"syncedAt": int(now), "error": ""})
        self.snap["watchlist"] = wl

    def _sync_signals(self, signed_in, now):
        raw = self.client.call("GET", "/v1/signals" if signed_in else "/v1/public/signals",
                               query={"limit": 50}, auth="required" if signed_in else "none")
        items, dropped = data.signal_list(raw.get("signals") if isinstance(raw, dict) else None)
        self._count_dropped(dropped)
        identity = "personal" if signed_in else "public"
        baseline = self.ledger_identity() != identity
        for item in reversed(items):
            self._consider(item, silent=baseline)
        if baseline:
            self.set_ledger_identity(identity)
        merged = {s["id"]: s for s in self.snap["signals"]["items"] if self.snap["signals"].get("personal") == signed_in}
        for s in items:
            merged[s["id"]] = s
        self._set_signals(list(merged.values()), signed_in, now)

    def ledger_identity(self):
        return statefile.load_json(self.cfg.state_dir / "ledger-identity.json", {}).get("identity")

    def set_ledger_identity(self, identity):
        statefile.save_json(self.cfg.state_dir / "ledger-identity.json", {"identity": identity})

    def _set_signals(self, items, personal, now):
        items = sorted(items, key=lambda s: s.get("createdAt", 0), reverse=True)[:100]
        live = [s for s in items if not (s.get("expiresAt") and s["expiresAt"] <= now)]
        self.snap["signals"].update({
            "items": live, "personal": personal, "syncedAt": int(now), "error": "",
            "unread": sum(1 for s in live if not s.get("read")),
        })
        self._dirty = True

    def _sync_skills(self, signed_in, now):
        self.snap["skills"] = data.skills(self.client.call("GET", "/v1/skills", auth="required"))

    def _sync_rules(self, signed_in, now):
        self.snap["rules"] = data.rule_list(self.client.call("GET", "/v1/rules", auth="required"))

    def _sync_positions(self, signed_in, now):
        accts = data.accounts(self.client.call("GET", "/v1/positions", auth="required"), now)
        self.snap["positions"] = {"available": True, "accounts": accts, "totals": data.totals(accts),
                                  "syncedAt": int(now), "stale": any(a["stale"] for a in accts), "error": ""}

    # ── Notifications ───────────────────────────────────────────────────
    def _consider(self, item, silent=False):
        now = self.clock()
        if silent:
            self.ledger.mark(item["id"])
            return False
        ok, reason = policy.decide(item, self.notify_settings, self.ledger, policy.local_now(now), now)
        if ok:
            try:
                self.notifier(item)
            except OSError:
                pass
            self.notified.append(item["id"])
        self._dirty = True
        return ok

    # ── Stream events ───────────────────────────────────────────────────
    def handle_event(self, event):
        now = self.clock()
        self.snap["connection"]["lastEventAt"] = int(now)
        if event.id:
            self.last_event_id = event.id
        try:
            payload = json.loads(event.data) if event.data else {}
        except ValueError:
            self.snap["malformed"] = self.snap.get("malformed", 0) + 1
            self._dirty = True
            return
        if not isinstance(payload, dict):
            self.snap["malformed"] = self.snap.get("malformed", 0) + 1
            self._dirty = True
            return
        kind = event.event
        if kind in ("signal", "risk"):
            item = data.signal(payload)
            if item is None:
                self.snap["malformed"] = self.snap.get("malformed", 0) + 1
            else:
                if kind == "risk":
                    item["source"]["kind"] = "risk"
                    item["type"] = "risk"
                existing = [s for s in self.snap["signals"]["items"] if s["id"] != item["id"]]
                self._consider(item)
                self._set_signals(existing + [item], self._signed_in, now)
        elif kind == "ack":
            ids = {i for i in payload.get("ids", []) if isinstance(i, str)} if isinstance(payload.get("ids"), list) else set()
            for s in self.snap["signals"]["items"]:
                if s["id"] in ids:
                    s["read"] = True
            for i in ids:
                self.ledger.mark(i)
            self.snap["signals"]["unread"] = sum(1 for s in self.snap["signals"]["items"] if not s.get("read"))
        elif kind == "positions" and self._signed_in:
            accts = data.accounts(payload, now)
            self.snap["positions"] = {"available": True, "accounts": accts, "totals": data.totals(accts),
                                      "syncedAt": int(now), "stale": any(a["stale"] for a in accts), "error": ""}
        elif kind == "quote":
            self._apply_quote(payload)
        elif kind == "watchlist":
            wl, dropped = data.watchlist(payload)
            self._count_dropped(dropped)
            wl.update({"syncedAt": int(now), "error": ""})
            self.snap["watchlist"] = wl
        elif kind == "sync":
            names = [r for r in payload.get("resources", []) if r in PERSONAL] if isinstance(payload.get("resources"), list) else []
            if names:
                self.sync(names)
        elif kind == "session" and self._signed_in and payload.get("state") == "revoked":
            self.tokens.expire("revoked")
            self.refresh_auth()
            self.snap["auth"]["state"] = "revoked"
            self.reconnect_stream()
        self._dirty = True

    def _apply_quote(self, q):
        sym = data.symbol(q.get("symbol"))
        if not sym:
            return
        for sec in self.snap["watchlist"].get("sections", []):
            for e in sec.get("entries", []):
                if e.get("symbol") == sym:
                    for key, src in (("last", "last"), ("change", "change"), ("changePct", "change_pct")):
                        value = data.num(q.get(src))
                        if value is not None:
                            e[key] = value
                    e["found"] = e.get("last") is not None

    # ── Stream thread ───────────────────────────────────────────────────
    def reconnect_stream(self):
        resp = self._resp
        if resp is not None:
            with contextlib.suppress(Exception):
                resp.close()
        self.wake_stream.set()

    def stream_loop(self):
        while not self.stop_event.is_set():
            minimum = None
            parser = sse.SSEParser()
            parser.last_event_id = self.last_event_id
            token = None
            try:
                token = self.tokens.access_token(self.client) if self._signed_in else None
                resp = self.client.open_stream("/v1/stream", token=token,
                                               last_event_id=self.last_event_id,
                                               timeout=self.stream_timeout)
                self._resp = resp
                self.q.put(("stream_up", None))
                got_event = False
                with resp:
                    while not self.stop_event.is_set():
                        line = resp.readline(sse.MAX_EVENT_BYTES)
                        if not line:
                            break
                        event = parser.feed(line)
                        if event is not None:
                            if not got_event:
                                got_event = True
                                self.backoff.reset()
                            self.q.put(("event", event))
                self.q.put(("stream_down", "stream closed by server"))
            except ApiError as e:
                if e.status == 401 and token:
                    try:
                        fresh = self.tokens.refresh(self.client, force=True, rejected=token)
                    except OfflineError:
                        fresh = None
                    if not fresh:
                        self.q.put(("auth_expired", None))
                    else:
                        continue
                minimum = e.retry_after
                self.q.put(("stream_down", f"stream refused ({e.code})" if e.status != 429
                            else "stream rate limited"))
            except (OfflineError, OSError, ValueError) as e:
                self.q.put(("stream_down", f"stream lost ({type(e).__name__})"))
            except KeyringUnavailable:
                self.q.put(("stream_down", "keyring unavailable"))
            finally:
                self._resp = None
            if parser.retry is not None:
                minimum = max(minimum or 0, parser.retry / 1000)
            if self.stop_event.is_set():
                break
            if self.wake_stream.is_set():
                self.wake_stream.clear()
                continue
            delay = self.backoff.next(minimum=minimum)
            self.q.put(("retry_in", delay))
            self.wake_stream.wait(delay)
            self.wake_stream.clear()

    # ── Control socket ──────────────────────────────────────────────────
    def control_path(self):
        return self.cfg.runtime_dir / "control.sock"

    def control_loop(self, server):
        while not self.stop_event.is_set():
            try:
                conn, _ = server.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            with conn:
                try:
                    creds = conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
                    _, uid, _ = struct.unpack("3i", creds)
                    if uid != os.getuid():
                        continue
                    conn.settimeout(2)
                    command = conn.recv(64).decode("ascii", "ignore").strip()
                    if command in CONTROL_COMMANDS:
                        self.q.put(("control", command))
                        conn.sendall(b"ok\n")
                    else:
                        conn.sendall(b"unknown\n")
                except OSError:
                    pass

    def open_control(self):
        path = self.control_path()
        statefile.ensure_dir(path.parent)
        with contextlib.suppress(FileNotFoundError):
            path.unlink()
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        old = os.umask(0o177)
        try:
            server.bind(str(path))
        finally:
            os.umask(old)
        server.listen(4)
        server.settimeout(0.5)
        return server

    # ── Main loop ───────────────────────────────────────────────────────
    def _check_files(self):
        changed = []
        for name, path in (("settings", settings_path(self.cfg)),
                           ("theme", self.theme_dir / "colors.toml")):
            try:
                mtime = path.stat().st_mtime
            except OSError:
                mtime = None
            if self._mtimes.get(name, "unset") != mtime:
                if name in self._mtimes:
                    changed.append(name)
                self._mtimes[name] = mtime
        if "settings" in changed:
            self.notify_settings, self.poll_interval = load_settings(self.cfg)
            self.snap["notify"] = self.notify_settings.to_dict()
            self._dirty = True
        if "theme" in changed:
            self.snap["colors"] = theme.theme_colors(self.theme_dir)
            self._dirty = True

    def step(self, item):
        kind, payload = item
        now = self.clock()
        if kind == "event":
            self.handle_event(payload)
            if self.mode != "live":
                self._set_mode("live", "")
        elif kind == "stream_up":
            self._set_mode("live", "")
            # A stream that comes back may have missed events it cannot
            # replay (the server restarted): catch up over REST once.
            self.sync()
            self._next_full = now + FULL_SYNC_EVERY
        elif kind == "stream_down":
            if self.mode == "live":
                self._set_mode("polling", payload)
                self._next_poll = now
            elif self.mode not in ("offline", "rate_limited"):
                self._set_mode("polling", payload)
        elif kind == "retry_in":
            self.snap["connection"]["retryAt"] = int(now + payload)
            self._dirty = True
        elif kind == "auth_expired":
            self._session_ended()
            self.reconnect_stream()
        elif kind == "control":
            if payload == "stop":
                self.stop_event.set()
            elif payload == "reload-auth":
                was = self._signed_in
                self.sync()
                if was != self._signed_in:
                    self.reconnect_stream()
            elif payload == "settings":
                self.notify_settings, self.poll_interval = load_settings(self.cfg)
                self.snap["notify"] = self.notify_settings.to_dict()
                self._dirty = True
            else:
                self.sync()

    def tick(self):
        now = self.clock()
        self._check_files()
        if self.mode != "live" and now >= self._next_poll:
            self._next_poll = now + self.poll_interval
            self.sync()
        elif self.mode == "live" and now >= self._next_full:
            self._next_full = now + FULL_SYNC_EVERY
            self.sync()
        stale = self._is_stale(now)
        if stale != self.snap.get("stale"):
            self._dirty = True
        self.publish()

    def run(self):
        statefile.ensure_dir(self.cfg.state_dir)
        lock_fd = os.open(self.cfg.state_dir / "daemon.lock", os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(lock_fd)
            return 1
        os.ftruncate(lock_fd, 0)
        os.write(lock_fd, str(os.getpid()).encode())
        server = None
        try:
            self.load()
            self.refresh_auth()
            self.publish(force=True)
            server = self.open_control()
            threading.Thread(target=self.control_loop, args=(server,), daemon=True).start()
            self.sync()
            self._next_poll = self.clock() + self.poll_interval
            threading.Thread(target=self.stream_loop, daemon=True).start()
            while not self.stop_event.is_set():
                try:
                    item = self.q.get(timeout=0.5)
                except queue.Empty:
                    item = None
                if item is not None:
                    self.step(item)
                self.tick()
        finally:
            self.stop_event.set()
            self.reconnect_stream()
            with contextlib.suppress(Exception):
                self.publish(force=True)
            if server is not None:
                server.close()
                with contextlib.suppress(OSError):
                    self.control_path().unlink()
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)
        return 0


def poke(cfg, command="refresh", timeout=2):
    """Nudge a running daemon. False if none is listening."""
    if command not in CONTROL_COMMANDS:
        raise ValueError(command)
    path = cfg.runtime_dir / "control.sock"
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            s.connect(str(path))
            s.sendall(command.encode() + b"\n")
            return s.recv(16).startswith(b"ok")
    except OSError:
        return False


def running(cfg):
    path = cfg.state_dir / "daemon.lock"
    try:
        fd = os.open(path, os.O_RDWR)
    except OSError:
        return False
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    except BlockingIOError:
        return True
    finally:
        os.close(fd)
