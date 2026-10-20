"""Risk arithmetic for display. The server is the source of truth for risk
alerts (it watches positions while the laptop sleeps); these are the same
formulas, so what the panel shows agrees with what the server alerts on."""

import math

# Display bands. Alert thresholds are the server's; these only colour rows.
DANGER_DISTANCE_PCT = 5.0
WATCH_DISTANCE_PCT = 15.0
DANGER_MARGIN_RATIO = 0.8
WATCH_MARGIN_RATIO = 0.5
DEFAULT_STALE_AFTER_SEC = 120


def _num(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def distance_to_liquidation_pct(side, mark, liquidation):
    """How far the mark price can move against the position before it is
    liquidated, as a percentage of the mark. 0 when already through it;
    None when there is no liquidation price (spot, fully collateralised)."""
    mark, liquidation = _num(mark), _num(liquidation)
    if mark is None or liquidation is None or mark <= 0 or liquidation <= 0:
        return None
    if side == "long":
        distance = (mark - liquidation) / mark
    elif side == "short":
        distance = (liquidation - mark) / mark
    else:
        return None
    return round(max(0.0, distance) * 100, 2)


def margin_ratio(maintenance_margin, equity):
    """Maintenance margin over account equity: 1.0 (100%) is liquidation."""
    mm, eq = _num(maintenance_margin), _num(equity)
    if mm is None or eq is None or mm < 0:
        return None
    if eq <= 0:
        return 1.0 if mm > 0 else None
    return round(min(mm / eq, 10.0), 4)


def risk_level(distance_pct=None, ratio=None):
    if (distance_pct is not None and distance_pct <= DANGER_DISTANCE_PCT) or \
            (ratio is not None and ratio >= DANGER_MARGIN_RATIO):
        return "danger"
    if (distance_pct is not None and distance_pct <= WATCH_DISTANCE_PCT) or \
            (ratio is not None and ratio >= WATCH_MARGIN_RATIO):
        return "watch"
    return "ok"


def is_stale(synced_at, now, stale_after=DEFAULT_STALE_AFTER_SEC):
    if synced_at is None:
        return True
    return now - synced_at > stale_after


def worst(levels):
    order = {"ok": 0, "watch": 1, "danger": 2}
    return max(levels, key=lambda lvl: order.get(lvl, 0), default="ok")
