"""When a signal becomes a desktop notification.

Tiers map to notification urgency:

    info       -> low       (shown, never interrupts)
    important  -> normal
    critical   -> critical, persistent until dismissed

Order of checks for each signal:

1. Already notified on this device (server id in the local ledger), already
   read on any device (server `read` flag or an `ack` event), or expired:
   never notify.
2. Notifications switched off: nothing, not even critical. Off means off.
3. Below the minimum tier: silent (still listed in the panel).
4. Critical skips quiet hours and the daily cap, and does not count toward
   it. A liquidation warning at 3am is the one interruption worth having.
5. Quiet hours, then the daily cap, for everything else.

Suppressed signals are recorded as seen: they stay unread in the panel but do
not arrive as a burst when quiet hours end.
"""

import datetime as dt
import re
from dataclasses import dataclass

TIERS = ("info", "important", "critical")
URGENCY = {"info": "low", "important": "normal", "critical": "critical"}
SEEN_LIMIT = 2000
HHMM = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


def tier_rank(tier):
    return TIERS.index(tier) if tier in TIERS else 0


def parse_hhmm(value):
    if not isinstance(value, str) or not HHMM.match(value.strip()):
        return None
    h, m = value.strip().split(":")
    return int(h) * 60 + int(m)


@dataclass
class NotifySettings:
    enabled: bool = True
    quiet_start: str = ""
    quiet_end: str = ""
    daily_cap: int = 20
    min_tier: str = "important"

    @classmethod
    def from_dict(cls, raw):
        raw = raw if isinstance(raw, dict) else {}
        cap = raw.get("dailyCap", cls.daily_cap)
        try:
            cap = max(0, min(500, int(cap)))
        except (TypeError, ValueError):
            cap = cls.daily_cap
        tier = raw.get("minTier", cls.min_tier)
        return cls(
            enabled=raw.get("enabled", True) is not False,
            quiet_start=raw.get("quietStart", "") if parse_hhmm(raw.get("quietStart")) is not None else "",
            quiet_end=raw.get("quietEnd", "") if parse_hhmm(raw.get("quietEnd")) is not None else "",
            daily_cap=cap,
            min_tier=tier if tier in TIERS else cls.min_tier,
        )

    def to_dict(self):
        return {"enabled": self.enabled, "quietStart": self.quiet_start, "quietEnd": self.quiet_end,
                "dailyCap": self.daily_cap, "minTier": self.min_tier}


def in_quiet_hours(settings, local_time):
    start, end = parse_hhmm(settings.quiet_start), parse_hhmm(settings.quiet_end)
    if start is None or end is None or start == end:
        return False
    minute = local_time.hour * 60 + local_time.minute
    if start < end:
        return start <= minute < end
    return minute >= start or minute < end  # wraps past midnight


class Ledger:
    """What this device has already notified, and today's count. Persisted
    as plain JSON (ids only, no content)."""

    def __init__(self, raw=None):
        raw = raw if isinstance(raw, dict) else {}
        seen = raw.get("seen") if isinstance(raw.get("seen"), list) else []
        self.seen = [s for s in seen if isinstance(s, str)][-SEEN_LIMIT:]
        self._seen_set = set(self.seen)
        self.day = raw.get("day") if isinstance(raw.get("day"), str) else ""
        self.count = raw.get("count") if isinstance(raw.get("count"), int) else 0

    def has(self, sid):
        return sid in self._seen_set

    def mark(self, sid):
        if sid in self._seen_set:
            return
        self.seen.append(sid)
        self._seen_set.add(sid)
        if len(self.seen) > SEEN_LIMIT:
            drop = self.seen[:-SEEN_LIMIT]
            self.seen = self.seen[-SEEN_LIMIT:]
            self._seen_set.difference_update(drop)

    def count_today(self, day):
        if self.day != day:
            self.day, self.count = day, 0
        return self.count

    def bump(self, day):
        self.count_today(day)
        self.count += 1

    def to_dict(self):
        return {"seen": self.seen, "day": self.day, "count": self.count}


def decide(signal, settings, ledger, now_local, now_epoch):
    """(notify?, reason). Records the signal as seen unless it is a duplicate."""
    sid = signal.get("id")
    if not sid or ledger.has(sid):
        return False, "duplicate"
    ledger.mark(sid)
    if signal.get("read"):
        return False, "read"
    expires = signal.get("expiresAt")
    if isinstance(expires, (int, float)) and expires <= now_epoch:
        return False, "expired"
    if not settings.enabled:
        return False, "disabled"
    tier = signal.get("tier", "info")
    if tier_rank(tier) < tier_rank(settings.min_tier):
        return False, "below_min_tier"
    day = now_local.date().isoformat()
    if tier != "critical":
        if in_quiet_hours(settings, now_local):
            return False, "quiet_hours"
        if ledger.count_today(day) >= settings.daily_cap:
            return False, "daily_cap"
        ledger.bump(day)
    return True, "notify"


def local_now(epoch):
    return dt.datetime.fromtimestamp(epoch).astimezone()
