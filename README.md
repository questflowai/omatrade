# Omatrade

**Your watchlist, the signals behind it, and the risk on your positions, in the
[Omarchy](https://omarchy.org) bar.** Made by [Questflow](https://questflow.ai).

- **Watchlist**: live quotes for the symbols you follow. Search and add from the panel,
  remove with one key. It works signed out with a default list; sign in to keep your own,
  synced across devices.
- **Signals**: one feed for news, skill signals you subscribe to and your own alert rules,
  each tagged *info*, *important* or *critical*. Critical alerts stay on screen until you
  dismiss them. Reading a signal marks it read everywhere.
- **Positions**: read-only. Each position shows its liquidation price, distance to
  liquidation and margin ratio, and the time it was last synced. Data that is out of date is
  marked stale and never passed off as live.
- **Rules**: describe an alert in plain words ("small caps with volume up 100% today").
  Questflow turns that into a precise rule, you read it and confirm, and the server watches
  the market for you, even while your laptop sleeps.

**One sign-in.** You sign in with Questflow once. There are no other accounts, API keys,
exports or connections to set up in Omatrade. Omatrade never places orders, moves funds,
signs transactions or asks for a password. It follows your Omarchy theme and is
keyboard-first.

> Not investment advice. Signals are informational and may be delayed or wrong.

## Install

```sh
omarchy plugin add https://github.com/questflowai/omatrade --enable
omatrade daemon install    # background client, a systemd user unit (recommended)
omatrade login             # once; opens your browser
```

Optionally put the CLI on your path:

```sh
ln -s ~/.config/omarchy/plugins/questflow.omatrade/bin/omatrade ~/.local/bin/omatrade
```

Requires Omarchy 4 and Python 3.11+ (standard library only). Sign-in needs a Secret
Service keyring with `secret-tool` (`libsecret`; GNOME Keyring or KeePassXC both work).
Without a keyring Omatrade refuses to sign in rather than store tokens in a file.
Public data works without one.

## Configure

The Questflow endpoints are configuration rather than code. Until they are set, Omatrade
points at placeholders that never resolve, and it says so. Put them in
`~/.config/omatrade/config.toml`:

```toml
api_base  = "https://api.questflow.example"   # Questflow API, https only
web_base  = "https://app.questflow.example"   # where "open Questflow" goes
client_id = "…"                               # the public OAuth client id
```

Or use `OMATRADE_API_BASE`, `OMATRADE_WEB_BASE` and `OMATRADE_CLIENT_ID`. `omatrade config`
shows what is in effect. Every request goes to that one host, and redirects to anywhere else
are refused. Plain `http` is allowed only for a loopback host during development
(`OMATRADE_DEV_ALLOW_HTTP_LOCALHOST=1`).

Notifications are set in the plugin settings: on or off, minimum tier, quiet hours
(critical alerts still come through), a daily cap (critical alerts don't count toward it)
and the polling interval used while the live stream is down.

## Use

Click the icon in the bar, or bind a key to `omarchy-shell questflow.omatrade toggle`.

| Key | |
|---|---|
| `←` `→` / `1` `2` `3` `4` | Watchlist · Signals · Positions · Rules |
| `↑` `↓` | select |
| `↵` | open the chart or story; in Rules, switch a rule or skill on or off |
| `a`, `+` or `/` | add a symbol; in Rules, describe a new rule |
| `x` | remove the symbol; in Rules, delete the rule (press twice) |
| `r` | refresh |

IPC: `omarchy-shell questflow.omatrade watchlist|signals|positions|rules|add|toggle|refresh`

### CLI

```sh
omatrade                              # status: watchlist, signals, positions
omatrade login | logout | whoami      # Questflow sign-in; logout revokes the session
omatrade watchlist [search TEXT | add SYMBOL [--section S] | remove SYMBOL]
omatrade signals [--unread] [--limit N]
omatrade ack ID...                    # mark read on every device
omatrade skills [subscribe ID | unsubscribe ID]
omatrade rules                        # list
omatrade rules add "alert me when …"  # shows the exact rule; asks before saving
omatrade rules enable|disable|delete ID
omatrade positions
omatrade notify-settings [--quiet-hours 22:00-07:00] [--daily-cap 20] [--min-tier important]
omatrade daemon install|uninstall|status|run
omatrade summary --json               # what the bar reads (local, no network)
```

Exit codes: `0` ok, `2` not configured, `3` not signed in or session expired, `4` keyring
unavailable, `5` offline, `6` rate limited, `7` unexpected server response, `1` other.

## How it works

Omatrade is a thin client. Watchlists, subscriptions and rules live on the Questflow
server, which also matches signals and watches position risk. That way an alert reaches
every device once, including while the laptop is closed.

- `omatrade daemon` keeps one Server-Sent Events stream open. It reconnects with jittered
  exponential backoff, resumes from the last event id, and falls back to polling while the
  stream is down. Every signal has a server id, so each one notifies once.
- The daemon writes a snapshot to `~/.local/state/omatrade/state.json`. The plugin
  (`Service.qml`, `Panel.qml`) watches that file and draws it. When offline, the last data
  stays on screen, marked stale.
- The client never evaluates rules or risk itself; it only displays what the server sends.
- The API is specified in [docs/api-contract.md](docs/api-contract.md).

## Privacy and security

- Sign-in is OAuth 2.0 with PKCE and a loopback redirect. There is no client secret, and
  only three read scopes are requested: `read:profile`, `read:signals` and
  `read:positions`. None of them can trade or move funds.
- Tokens live only in the system keyring. They are passed to `secret-tool` on stdin, never
  on a command line, and never written to disk. Signing out revokes the session on the
  server.
- Omatrade never asks for or accepts account API keys.
- It talks only to the configured Questflow host. Only `https://` links are opened, and
  server text is shown as plain text.
- Local state files are private to your user (`0600`).
- Plugins run unsandboxed in the Omarchy shell. `omarchy plugin update` shows the diff
  before applying it. Read it.

## Testing

```sh
python3 -m unittest discover -s tests -t .   # unit and end-to-end tests, standard library only
/usr/lib/qt6/bin/qmllint -I tests/qml-stubs Panel.qml Service.qml OmatradeIcon.qml Sparkline.qml
```

The end-to-end tests run the real CLI and daemon against `tests/mock_server.py`, a local
implementation of the API contract. They use test doubles for `secret-tool`,
`notify-send` and `xdg-open`, so no keyring, notifications or browser windows are touched
and nothing leaves the machine. Demo positions exist only as a labelled test fixture.

## Uninstall

```sh
omatrade logout
omatrade daemon uninstall
omarchy plugin remove questflow.omatrade
rm -rf ~/.local/state/omatrade ~/.config/omatrade ~/.local/bin/omatrade
```

## License

MIT © 2026 Questflow. `Sparkline.qml` is from [Omacharts](https://github.com/jorgemanrubia/omacharts),
MIT © 2026 Jorge Manrubia. See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
