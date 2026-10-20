# Omatrade ⇄ Questflow API contract (v1, draft)

This is the contract the Omatrade desktop client is built against. The
backend does not exist yet; `tests/mock_server.py` implements this document
and is the reference for the black-box tests. Anything marked **Open** is a
decision for the backend team.

## 1. Principles

- **One sign-in.** The user signs in with Questflow once. The client never
  connects to brokers, exchanges, wallets or market-data vendors; the
  backend does that, with properly licensed data.
- **Public works signed out.** Quotes, symbol search, a default watchlist and
  public signals need no account. Sign-in unlocks the personal watchlist,
  personal signals, rules, skill subscriptions and positions.
- **No API keys on the desktop.** The client is a public OAuth client
  holding short-lived, narrowly scoped, revocable tokens. It never accepts or
  stores long-lived account API keys.
- **The server evaluates.** Rule matching, skill signals and risk monitoring
  run on the server, so alerts fire while the laptop sleeps and the same
  alert reaches every device once. The client displays and notifies.
- **Read-only.** No endpoint in this contract places orders, moves funds or
  changes account connections.

## 2. Transport

- Base URL: configurable (`api_base`), HTTPS only. Every path below is
  relative to it, e.g. `GET {api_base}/v1/me`.
- JSON bodies, UTF-8. Timestamps are RFC 3339 UTC strings
  (`2026-10-20T08:00:00Z`); the client also accepts epoch seconds.
- The API must **not** answer with redirects (the client refuses them so a
  bearer token can never follow a 30x to another host).
- Responses should stay under 2 MiB (the client rejects larger ones).
- Identifiers match `^[A-Za-z0-9_.:-]{1,128}$`. Symbols are
  `EXCHANGE:TICKER` (`^[A-Z0-9_]+:[A-Z0-9_.!&/-]+$`) and are percent-encoded
  when used as a path segment.

## 3. Authentication

OAuth 2.0 Authorization Code with PKCE (RFC 7636, `S256` only) for a native
public client (RFC 8252). No client secret exists.

| Scope            | Grants                                                      |
|------------------|-------------------------------------------------------------|
| `read:profile`   | `GET /v1/me` (display name, plan; no email)                 |
| `read:signals`   | personal signals, ack, watchlist, skills, subscriptions, rules |
| `read:positions` | `GET /v1/positions`                                         |

**Open:** watchlist, subscription and rule *edits* are authorised by
`read:signals` in v1 because they change only the user's own alert
preferences. A separate `write:preferences` scope may be cleaner.

### 3.1 Authorize

`GET /oauth/authorize` (opened in the user's browser)

| Parameter               | Value                                            |
|-------------------------|--------------------------------------------------|
| `response_type`         | `code`                                           |
| `client_id`             | the registered Omatrade client id                |
| `redirect_uri`          | `http://127.0.0.1:{port}/callback`               |
| `scope`                 | `read:profile read:signals read:positions`       |
| `state`                 | 43-character random value, checked by the client |
| `code_challenge`        | BASE64URL(SHA256(code_verifier)), no padding     |
| `code_challenge_method` | `S256`                                           |

Server requirements:

- Accept any port on `127.0.0.1` for this client (RFC 8252 §7.3); reject
  every other redirect host.
- Reject `plain` PKCE and missing challenges.
- Never grant more than the requested scopes. The client rejects a token
  response that lists any scope outside the three above.
- Redirect back with `code` and `state`, or `error` (+ optional
  `error_description`) and `state`.

### 3.2 Token

`POST /oauth/token`, `application/x-www-form-urlencoded`, no Authorization header.

```
grant_type=authorization_code&code=…&redirect_uri=…&client_id=…&code_verifier=…
grant_type=refresh_token&refresh_token=…&client_id=…
```

Response (`Cache-Control: no-store`):

```json
{"access_token": "…", "token_type": "Bearer", "expires_in": 3600,
 "refresh_token": "…", "scope": "read:profile read:signals read:positions"}
```

- Access tokens: short-lived (recommended 15–60 min).
- Refresh tokens: **rotated on every use**; reuse of a spent refresh token
  should revoke the whole session (reuse detection). The client serialises
  refreshes across its processes, so legitimate reuse does not happen.
- Errors follow RFC 6749: `{"error": "invalid_grant", "error_description": "…"}`.
  `invalid_grant` on refresh ends the session in the client ("sign in again").

### 3.3 Revoke

`POST /oauth/revoke` (RFC 7009): `token=…&token_type_hint=refresh_token|access_token&client_id=…`.
Called on sign-out, refresh token first. Always `200`.

### 3.4 Using tokens

`Authorization: Bearer {access_token}`. A `401` with
`WWW-Authenticate: Bearer error="invalid_token"` makes the client refresh once
and retry once; a second `401` ends the session. `403 insufficient_scope`
is shown as an error and never retried.

## 4. Endpoints

| Method | Path                               | Auth / scope       | Purpose |
|--------|------------------------------------|--------------------|---------|
| GET    | `/v1/public/watchlist`             | none               | default watchlist with quotes |
| GET    | `/v1/public/symbols?q=`            | none               | symbol search (≤ 64 chars) |
| GET    | `/v1/public/signals?limit=`        | none               | public signals (news, public skills) |
| GET    | `/v1/me`                           | `read:profile`     | `{"id","display_name","plan"}` |
| GET    | `/v1/watchlist`                    | `read:signals`     | the user's watchlist with quotes |
| PUT    | `/v1/watchlist/items/{symbol}`     | `read:signals`     | add; body `{"section": string|null}`; returns the watchlist |
| DELETE | `/v1/watchlist/items/{symbol}`     | `read:signals`     | remove; `204` |
| GET    | `/v1/signals?limit=`        | `read:signals`     | personal feed, newest first, with `read` flags |
| POST   | `/v1/signals/ack`                  | `read:signals`     | `{"ids": [...]}` mark read on every device |
| GET    | `/v1/skills`                       | `read:signals`     | skill feeds with `subscribed` |
| PUT    | `/v1/subscriptions/{skill_id}`     | `read:signals`     | subscribe |
| DELETE | `/v1/subscriptions/{skill_id}`     | `read:signals`     | unsubscribe |
| GET    | `/v1/rules`                        | `read:signals`     | `{"rules": [RuleRecord]}` |
| POST   | `/v1/rules/drafts`                 | `read:signals`     | `{"text": "…"}` (≤ 500 chars) → `{"draft": Draft}` |
| POST   | `/v1/rules`                        | `read:signals`     | `{"draft_id","rule_digest"}` → `{"rule": RuleRecord}` |
| PATCH  | `/v1/rules/{id}`                   | `read:signals`     | `{"enabled": bool}` |
| DELETE | `/v1/rules/{id}`                   | `read:signals`     | `204` |
| GET    | `/v1/positions`                    | `read:positions`   | `{"accounts": [Account]}` |
| GET    | `/v1/stream`                       | optional bearer    | SSE (section 6) |

## 5. Schemas

### 5.1 Signal (one shape for every source)

```json
{
  "id": "sig_01HZX…",
  "symbol": "NASDAQ:NVDA",
  "symbols": ["NASDAQ:NVDA", "NASDAQ:AMD"],
  "type": "news | price | volume | rule_match | skill | funding | odds_shift | whale_flow | risk | earnings | unlock",
  "strength": 0.72,
  "tier": "info | important | critical",
  "source": {"kind": "skill | rule | news | risk", "id": "skill_whales", "name": "Whale flows"},
  "title": "≤ 200 chars, plain text",
  "body": "≤ 600 chars, plain text",
  "created_at": "2026-10-20T08:00:00Z",
  "expires_at": "2026-10-20T12:00:00Z",
  "url": "https://… (https only; anything else is dropped by the client)",
  "rule_id": "rule_123",
  "read": false
}
```

- `id` and `title` are required; signals without them are dropped.
- `id` is stable and unique; it is the dedup key on every device.
- `tier` drives notification urgency: info → low, important → normal,
  critical → critical and persistent. Reserve `critical` for risk to money
  (liquidation distance, margin calls) and rules the user marked critical.
- Text is plain text; the client never renders markup.
- Unknown `type`/`tier` values degrade to `skill`/`info`.

### 5.2 Watchlist

```json
{"editable": true,
 "sections": [{"name": "Stocks", "entries": [
   {"symbol": "NASDAQ:NVDA", "name": "NVIDIA", "url": "https://…",
    "quote": {"last": 186.2, "change": 2.2, "change_pct": 1.2, "currency": "USD", "delay_sec": 0}}]}]}
```

`editable` is `false` for the public default list. `delay_sec` states how
delayed the quote is under the data licence.

### 5.3 Skill

`{"id": "skill_whales", "name": "Whale flows", "description": "…", "subscribed": true}`

### 5.4 Rule DSL (version 1)

A rule is data evaluated by the server. It never contains code.

```json
{
  "version": 1,
  "name": "Small caps waking up",
  "universe": {"watchlist": true} | {"symbols": ["CRYPTO:BTCUSD"]} | {"market": "crypto"},
  "when": {"all": [
    {"metric": "market_cap_usd", "op": "<", "value": 1000000000},
    {"metric": "volume_change_pct", "op": ">=", "value": 100, "window": "24h"},
    {"any": [{"metric": "news_sentiment", "op": ">", "value": 0.5}]}
  ]},
  "tier": "important",
  "cooldown_sec": 7200
}
```

- Groups: exactly one of `all` / `any`, non-empty; at most 4 levels and 32
  conditions.
- Condition: `metric` (`lower_snake_case`, server-defined catalogue), `op`
  (`< <= > >= == != crosses_above crosses_below in`), `value` (number,
  string ≤ 200, or list ≤ 50 of those), optional `window` (`^\d{1,4}[mhd]$`, e.g. `15m`, `4h`, `1d`).
- `cooldown_sec` 60 … 604800: at most one alert per symbol per cooldown.
- The client rejects unknown keys anywhere in a rule.

**Draft:** `{"id": "draft_…", "rule": Rule, "summary": "…", "expires_at": "…"}`.

**Confirm:** the client shows a description built from `rule` itself (not
from `summary`) and, on confirmation, sends
`rule_digest = "sha256:" + hex(SHA-256(canonical JSON of rule))`, where
canonical JSON is sorted keys, separators `,` and `:`, UTF-8, no escaping of
non-ASCII. The server must save the draft only if the digest matches
(`409 digest_mismatch` otherwise) and must not change the rule when saving.
Drafts expire (`410 draft_expired`).

**RuleRecord:** `{"id", "enabled", "created_at", "last_matched_at", "rule": Rule}`.

**Open:** natural-language drafting is an LLM call; it needs per-user
quotas and must never be triggered by events, only by an explicit request.

### 5.5 Account and position (read-only)

```json
{"accounts": [{
  "id": "acct_…", "venue": "broker | hyperliquid | polymarket | …",
  "name": "Display name", "label": "masked identifier, e.g. U•••4821",
  "equity": 9310.0, "maintenance_margin": 2048.2,
  "day_pnl": -120.5, "day_pnl_pct": -1.28,
  "synced_at": "2026-10-20T08:00:00Z", "stale_after_sec": 120,
  "positions": [{
    "id": "pos_…", "symbol": "HYPERLIQUID:ETH-PERP", "display": "ETH", "name": "3x Short perp",
    "kind": "stock | perp | spot | prediction | option | future",
    "side": "long | short", "size": 12, "entry_price": 3850.0, "mark_price": 3712.0,
    "liquidation_price": 3880.0, "leverage": 3, "unrealized_pnl": 1656.0,
    "value": 14848.0, "day_change_pct": -0.8, "spark": [3700.1, 3712.0]}]}]}
```

The client derives, and the server's risk monitor must use the same
definitions:

- distance to liquidation % = long: `(mark − liq) / mark × 100`;
  short: `(liq − mark) / mark × 100`; floored at 0; absent without `liq`.
- margin ratio = `maintenance_margin / equity` (1.0 = liquidation).
- stale when `now − synced_at > stale_after_sec` (default 120 s).

Account identifiers must be masked server-side; full account numbers and
wallet keys never reach the client.

## 6. Event stream

`GET /v1/stream`, `Accept: text/event-stream`, optional bearer token. An
anonymous stream carries public events only.

- Each event has `id:` (opaque, monotonic per user), `event:` and one JSON
  object in `data:`.
- Heartbeat: a comment line (`: ping`) at least every 15 s. The client
  treats 45 s of silence as a dead connection.
- Resume: the client sends `Last-Event-ID`. Replay what you can (recommended
  window ≥ 15 minutes); the client resyncs over REST on every reconnect, so a
  gap is harmless and replays are deduplicated by signal `id`.
- `retry:` is honoured as the minimum reconnect delay. Clients reconnect
  with full-jitter exponential backoff (1 s base, 5 min cap); servers should
  still shed load with `503` + `Retry-After` during deploys.

| Event       | `data`                                      | Effect on the client |
|-------------|---------------------------------------------|----------------------|
| `signal`    | Signal                                      | list, maybe notify |
| `risk`      | Signal (`type: risk`, usually `critical`)   | list, notify, persistent |
| `ack`       | `{"ids": [...]}`                            | mark read; never notify those ids |
| `quote`     | `{"symbol","last","change","change_pct"}`  | update watchlist row |
| `watchlist` | Watchlist                                   | replace watchlist |
| `positions` | `{"accounts": [Account]}`                   | replace positions |
| `sync`      | `{"resources": ["rules","skills",…]}`       | refetch those over REST |
| `session`   | `{"state": "revoked"}`                      | sign out locally |

Unknown events are ignored; malformed `data` is counted and dropped.

## 7. Errors

```json
{"error": {"code": "rate_limited", "message": "human readable, no secrets"}}
```

| Status | `code`                 | Client behaviour |
|--------|------------------------|------------------|
| 400    | `invalid_request`      | show error |
| 401    | `invalid_token`        | refresh once, retry once, else "sign in again" |
| 403    | `insufficient_scope`   | show error |
| 404    | `not_found`            | show error |
| 409    | `digest_mismatch`      | rule not saved; show error |
| 410    | `draft_expired`        | ask to draft again |
| 422    | `unparseable_rule`, `validation_failed` | show message |
| 429    | `rate_limited`         | wait `Retry-After` (else `RateLimit-Reset`) |
| 5xx    | `server_error`         | back off; keep last data, mark stale |

## 8. Rate limits

Every response carries `RateLimit-Limit`, `RateLimit-Remaining` and
`RateLimit-Reset` (seconds; IETF draft `ratelimit-headers`). `429` adds
`Retry-After` (seconds). Limits are per token (per IP when anonymous).
Suggested starting points: 120 requests/min per token, 5 rule drafts/min and
50/day per user, 1 open stream per device.

## 9. Client polling fallback

While the stream is down the client polls `/v1/signals` (or
`/v1/public/signals`) and the other resources every `refreshIntervalSec`
(default 60 s, 15 … 1800), and does a full resync every 15 minutes even
when live.
