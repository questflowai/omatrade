// Model.js under node: the panel's row shaping and formatting.
// Run by tests/test_model_js.py; node is a test-time tool only.
const fs = require("fs")
const path = require("path")
const assert = require("assert")
const src = fs.readFileSync(path.join(__dirname, "..", "Model.js"), "utf8").replace(".pragma library", "")
const m = {}
new Function("module", src)(m)
const M = m.exports

assert.deepStrictEqual(M.parse(""), { data: null, error: "" })
assert.strictEqual(M.parse("{bad").error, "unreadable output")
assert.strictEqual(M.parse('{"ok":false,"error":"not signed in"}').error, "not signed in")
assert.strictEqual(M.parse("null").error, "unreadable output")

const rows = M.watchlistRows({ sections: [{ name: "A", entries: [{ symbol: "X:Y", display: "Y", exchange: "X", found: false, last: null }] }, { name: "", entries: [] }] })
assert.strictEqual(rows.length, 2)
assert.strictEqual(rows[1].name, "No quote yet")
assert.strictEqual(rows[1].hasQuote, false)

const sig = M.signalRows({ items: [{ id: "a", title: "t", tier: "critical", symbols: ["NVDA"], source: { name: "S" }, read: false, createdAt: 1 }] })
assert.strictEqual(sig[0].tierLabel, "CRITICAL")
assert.strictEqual(M.signalRows({ items: [{ id: "b", title: "t", tier: "info" }] })[0].tierLabel, "")
assert.deepStrictEqual(M.signalRows(null), [])

const pos = M.positionRows([{ name: "P", marginRatio: 0.22, risk: "danger", stale: true, syncedAt: 5,
  positions: [{ display: "ETH", kind: "perp", sizeLabel: "3x Short 12 @ 3,850.00", liquidationPrice: 3880, distanceToLiqPct: 4.53, risk: "danger" }] }])
assert.strictEqual(pos[0].header, true)
assert.strictEqual(pos[0].stale, true)
assert.strictEqual(pos[1].subtitle, "3x Short 12 @ 3,850.00  ·  liq 3,880.00 · 4.5% away")
assert.strictEqual(M.riskRole("danger"), "down")
assert.strictEqual(M.formatRatio(0.22), "margin 22%")
assert.strictEqual(M.formatRatio(0), "")

const rr = M.ruleRows([{ id: "r1", name: "R", description: "d", enabled: false }], [{ id: "s1", name: "S", subscribed: true }])
assert.deepStrictEqual(rr.map(r => r.header ? "H" : r.kind + (r.on ? "+" : "-")), ["H", "rule-", "H", "skill+"])
assert.deepStrictEqual(M.selectableIndexes(rr), [1, 3])

assert.strictEqual(M.connectionLabel({ connection: { mode: "live" }, stale: false }), "")
assert.strictEqual(M.connectionLabel({ connection: { mode: "live" }, stale: true }), "STALE")
assert.strictEqual(M.connectionLabel({ connection: { mode: "offline" }, stale: true }), "OFFLINE")
assert.strictEqual(M.connectionLabel({ connection: { mode: "rate_limited" } }), "RATE LIMITED")

assert.strictEqual(M.formatPrice(112450), "112,450.00")
assert.strictEqual(M.formatPrice(0.61), "0.6100")
assert.strictEqual(M.formatMoney(-1234.5, true), "-$1,234.50")
assert.strictEqual(M.formatCompactMoney(48210), "$48.2k")
assert.strictEqual(M.ago(0, 1000), "never")
assert.strictEqual(M.ago(100, 100 * 1000 + 7200 * 1000), "2h")
console.log("model_test.js: all assertions passed")
