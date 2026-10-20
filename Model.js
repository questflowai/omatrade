// Shaping the daemon's snapshot for the panel.
//
// Kept out of the QML so it can be reasoned about (and tested) on its own:
// the panel is four lists of rows, and working out what a row says is not a
// layout problem.

.pragma library

function parse(text) {
  if (!text || !text.trim()) return { data: null, error: "" }
  try {
    var parsed = JSON.parse(text)
    if (parsed === null || typeof parsed !== "object") return { data: null, error: "unreadable output" }
    return { data: parsed, error: parsed.ok === false ? String(parsed.error || "failed") : "" }
  } catch (e) {
    return { data: null, error: "unreadable output" }
  }
}

function list(value) { return Array.isArray(value) ? value : [] }

// The watchlist: section headers and symbols in one stream, so the list does
// not have to nest. One unnamed section gets no header at all.
function watchlistRows(watchlist) {
  var out = []
  var sections = list(watchlist && watchlist.sections)
  for (var s = 0; s < sections.length; s++) {
    var section = sections[s]
    var entries = list(section.entries)
    if (!entries.length) continue
    if (section.name) out.push({ header: true, name: section.name })
    for (var e = 0; e < entries.length; e++) {
      var entry = entries[e]
      out.push({
        header: false,
        symbol: entry.symbol,
        display: entry.display,
        exchange: entry.exchange,
        name: entry.found ? entry.name : (entry.name || "No quote yet"),
        last: entry.last,
        changePct: entry.changePct,
        url: entry.url || "",
        hasQuote: entry.last !== null && entry.last !== undefined
      })
    }
  }
  return out
}

function searchRows(results) {
  var out = []
  var rows = list(results)
  for (var i = 0; i < rows.length; i++) {
    var r = rows[i]
    out.push({ header: false, symbol: r.symbol, display: r.display, exchange: r.exchange,
               name: r.name, type: r.type })
  }
  return out
}

var TIER_LABEL = { critical: "CRITICAL", important: "IMPORTANT", info: "" }

function signalRows(signals) {
  var items = list(signals && signals.items)
  var out = []
  for (var i = 0; i < items.length; i++) {
    var s = items[i]
    out.push({
      header: false,
      id: s.id,
      title: s.title,
      body: s.body || "",
      symbols: list(s.symbols),
      source: s.source && s.source.name ? s.source.name : "",
      tier: s.tier || "info",
      tierLabel: TIER_LABEL[s.tier] || "",
      read: !!s.read,
      createdAt: s.createdAt || 0,
      url: s.url || ""
    })
  }
  return out
}

function liquidationText(pos) {
  if (pos.distanceToLiqPct === null || pos.distanceToLiqPct === undefined) return ""
  return "liq " + formatPrice(pos.liquidationPrice) + " · " + Number(pos.distanceToLiqPct).toFixed(1) + "% away"
}

// Accounts and their positions in one stream, headers and entries together.
function positionRows(accounts) {
  var out = []
  var accts = list(accounts)
  for (var a = 0; a < accts.length; a++) {
    var account = accts[a]
    var positions = list(account.positions)
    out.push({
      header: true,
      name: account.name,
      label: account.label || "",
      equity: account.equity,
      dayPnlPct: account.dayPnlPct,
      marginRatio: account.marginRatio,
      risk: account.risk || "ok",
      syncedAt: account.syncedAt || 0,
      stale: !!account.stale
    })
    for (var p = 0; p < positions.length; p++) {
      var pos = positions[p]
      var prediction = pos.kind === "prediction"
      var liq = liquidationText(pos)
      out.push({
        header: false,
        symbol: pos.symbol,
        // A market's question is what you recognise it by; the outcome you
        // hold is the detail, so the two trade places on prediction rows.
        title: prediction ? pos.name : pos.display,
        subtitle: pos.sizeLabel + (liq ? "  ·  " + liq : ""),
        kind: pos.kind,
        risk: pos.risk || "ok",
        last: pos.last,
        value: pos.value,
        pnl: pos.pnl,
        pnlPct: pos.pnlPct,
        spark: list(pos.spark)
      })
    }
  }
  return out
}

// Rules first, then the skill feeds you can subscribe to.
function ruleRows(rules, skills) {
  var out = []
  var r = list(rules)
  var s = list(skills)
  out.push({ header: true, name: "Your rules" })
  for (var i = 0; i < r.length; i++)
    out.push({ header: false, kind: "rule", id: r[i].id, title: r[i].name,
               subtitle: r[i].description, on: !!r[i].enabled })
  if (s.length) out.push({ header: true, name: "Skill feeds" })
  for (var j = 0; j < s.length; j++)
    out.push({ header: false, kind: "skill", id: s[j].id, title: s[j].name,
               subtitle: s[j].description, on: !!s[j].subscribed })
  return out
}

function selectableIndexes(rows) {
  var out = []
  for (var i = 0; i < rows.length; i++) if (!rows[i].header) out.push(i)
  return out
}

// What the header chip says about the connection, or "" when all is well.
function connectionLabel(snapshot) {
  if (!snapshot) return ""
  var mode = snapshot.connection ? snapshot.connection.mode : "connecting"
  if (mode === "offline") return "OFFLINE"
  if (mode === "rate_limited") return "RATE LIMITED"
  if (snapshot.stale) return "STALE"
  if (mode === "polling") return "POLLING"
  if (mode === "connecting") return "CONNECTING"
  return ""
}

function decimals(price) {
  var size = Math.abs(price)
  if (size >= 1) return 2
  if (size >= 0.01) return 4
  return 6
}

function groupThousands(fixed) {
  var parts = fixed.split(".")
  parts[0] = parts[0].replace(/\B(?=(\d{3})+(?!\d))/g, ",")
  return parts.join(".")
}

function formatPrice(value) {
  if (value === null || value === undefined) return "–"
  var fixed = Number(value).toFixed(decimals(value))
  return Math.abs(value) >= 1000 ? groupThousands(fixed) : fixed
}

function formatMoney(value, signed) {
  if (value === null || value === undefined) return "–"
  var sign = value < 0 ? "-" : (signed && value > 0 ? "+" : "")
  return sign + "$" + groupThousands(Math.abs(value).toFixed(2))
}

// For the narrow places — an account header, the tooltip — where cents are
// noise and five digits of dollars already say enough.
function formatCompactMoney(value, signed) {
  if (value === null || value === undefined) return "–"
  var size = Math.abs(value)
  var sign = value < 0 ? "-" : (signed && value > 0 ? "+" : "")
  if (size >= 1e6) return sign + "$" + (size / 1e6).toFixed(2) + "M"
  if (size >= 1e4) return sign + "$" + (size / 1e3).toFixed(1) + "k"
  return sign + "$" + groupThousands(size.toFixed(0))
}

function formatPercent(value) {
  if (value === null || value === undefined) return ""
  var sign = value > 0 ? "+" : ""
  return sign + Number(value).toFixed(2) + "%"
}

function formatRatio(value) {
  if (value === null || value === undefined || value <= 0) return ""
  return "margin " + (value * 100).toFixed(0) + "%"
}

// `ts` is epoch seconds, `now` epoch milliseconds.
function ago(ts, now) {
  if (!ts) return "never"
  var s = Math.max(0, Math.floor(now / 1000 - ts))
  if (s < 60) return "now"
  if (s < 3600) return Math.floor(s / 60) + "m"
  if (s < 86400) return Math.floor(s / 3600) + "h"
  return Math.floor(s / 86400) + "d"
}

// The colours the panel draws in, by role. Every one comes from the CLI,
// which derives them from the desktop theme and checks each against the
// panel's ground; until the first answer the panel's own text and ground
// stand in, so not one colour in the widget is its own.
function palette(colors, text, ground) {
  var given = colors || {}
  var pick = function (key, fallback) { return given[key] ? given[key] : fallback }
  var written = pick("text", text)
  var givenTags = given.tags || {}
  var tag = function (way, fill) {
    var t = givenTags[way] || {}
    return { fill: t.fill ? t.fill : fill, label: t.label ? t.label : ground }
  }
  var out = {
    text: written,
    muted: pick("muted", written),
    up: pick("up", written),
    down: pick("down", written),
    flat: pick("flat", pick("muted", written)),
    accent: pick("accent", written),
    hover: pick("hover", ground),
    rule: pick("rule", pick("muted", written))
  }
  out.tags = {
    up: tag("up", out.up),
    down: tag("down", out.down),
    flat: tag("flat", out.flat),
    accent: tag("accent", out.accent)
  }
  return out
}

function direction(value) {
  if (value === null || value === undefined) return "flat"
  if (value > 0) return "up"
  if (value < 0) return "down"
  return "flat"
}

// Risk to a palette role: danger reads as "down", watch as the accent.
function riskRole(risk) {
  if (risk === "danger") return "down"
  if (risk === "watch") return "accent"
  return "muted"
}

if (typeof module !== "undefined") {
  module.exports = {
    parse: parse, watchlistRows: watchlistRows, searchRows: searchRows, signalRows: signalRows,
    positionRows: positionRows, ruleRows: ruleRows, selectableIndexes: selectableIndexes,
    connectionLabel: connectionLabel, formatPrice: formatPrice, formatMoney: formatMoney,
    formatCompactMoney: formatCompactMoney, formatPercent: formatPercent, formatRatio: formatRatio,
    ago: ago, palette: palette, direction: direction, riskRole: riskRole, liquidationText: liquidationText
  }
}
