import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import "Model.js" as Model

// The bar's side of Omatrade: reads the snapshot the daemon writes, and runs
// the CLI for the few things a person does from the panel.
//
// One service for the whole shell, however many monitors have a bar. It
// makes no network requests and spawns nothing on a timer: the daemon
// (`omatrade daemon run`, a systemd user unit) holds the Questflow stream,
// sends the notifications and rewrites the snapshot; this file watches it.
Item {
  id: root

  property var settings: ({})
  property var data: null
  // Shared by every monitor's panel: the list showing, and whether the next
  // panel to open should start on the search field.
  property string view: "watchlist"
  property bool pendingAdd: false
  property string lastError: ""
  // The CLI could not be started at all: the panel hides rather than
  // reporting an error nobody can fix from the bar.
  property bool missing: false
  property double updatedAt: 0
  readonly property bool refreshing: false

  // The CLI that ships inside this plugin, so `omarchy plugin add` is the
  // whole install and nothing has to be on the PATH.
  readonly property string cli: decodeURIComponent(
    String(Qt.resolvedUrl("bin/omatrade")).replace(/^(file|qs):\/\//, ""))

  readonly property string stateHome: {
    var xdg = Quickshell.env("XDG_STATE_HOME")
    return xdg ? String(xdg) : String(Quickshell.env("HOME")) + "/.local/state"
  }
  readonly property string snapshotPath: stateHome + "/omatrade/state.json"

  readonly property var watchlist: data && data.watchlist ? data.watchlist : null
  readonly property var signalFeed: data && data.signals ? data.signals : null
  readonly property var auth: data && data.auth ? data.auth : ({ signedIn: false, state: "signed_out" })
  readonly property bool connected: !!auth.signedIn
  readonly property bool configured: !!(data && data.config && data.config.configured)
  readonly property var positions: data && data.positions ? data.positions : null
  readonly property var totals: positions && positions.totals ? positions.totals : null
  readonly property var accounts: positions && positions.accounts ? positions.accounts : []
  readonly property var rules: data && data.rules ? data.rules : []
  readonly property var skills: data && data.skills ? data.skills : []
  readonly property var colors: data && data.colors ? data.colors : null
  readonly property string connectionLabel: Model.connectionLabel(data)
  readonly property bool stale: !!(data && data.stale)
  readonly property int unread: signalFeed && signalFeed.unread ? signalFeed.unread : 0
  readonly property string disclaimer: signalFeed && signalFeed.disclaimer
    ? signalFeed.disclaimer : "Not investment advice."

  // Symbol search, for adding to the watchlist from the panel.
  property var searchResults: []
  property string searchError: ""
  property string searchQuery: ""
  readonly property bool searching: searchProcess.running
  property string _pendingSearch: ""

  // A rule being drafted from words: the server's structured answer waits
  // here until the person confirms it.
  property var ruleDraft: null
  property string ruleError: ""
  readonly property bool drafting: draftProcess.running

  readonly property int pollIntervalSec: {
    var value = parseInt(String(settings && settings.refreshIntervalSec ? settings.refreshIntervalSec : 60), 10)
    return isFinite(value) ? Math.max(15, Math.min(1800, value)) : 60
  }
  readonly property bool notify: !(settings && settings.notify === false)
  readonly property string quietHours: {
    var start = settings && settings.quietHoursStart ? String(settings.quietHoursStart) : ""
    var end = settings && settings.quietHoursEnd ? String(settings.quietHoursEnd) : ""
    return start && end ? start + "-" + end : "off"
  }
  readonly property int dailyCap: {
    var value = parseInt(String(settings && settings.dailyNotificationCap !== undefined ? settings.dailyNotificationCap : 20), 10)
    return isFinite(value) ? Math.max(0, Math.min(500, value)) : 20
  }
  readonly property string minTier: {
    var t = settings && settings.minNotificationTier ? String(settings.minNotificationTier) : "important"
    return (t === "info" || t === "important" || t === "critical") ? t : "important"
  }
  readonly property string settingsKey: [notify, quietHours, dailyCap, minTier, pollIntervalSec].join("|")
  onSettingsKeyChanged: Qt.callLater(pushSettings)

  function load() {
    var parsed = Model.parse(snapshot.text())
    if (!parsed.data || parsed.data.version !== 2) return
    root.data = parsed.data
    root.updatedAt = Date.now()
  }

  function refresh() {
    snapshot.reload()
    run([root.cli, "daemon", "poke", "--json", "refresh"])
  }

  function refreshIfStale() {
    snapshot.reload()
    if (Date.now() - updatedAt > 15000) refresh()
  }

  function pushSettings() {
    run([root.cli, "notify-settings", "--enabled", root.notify ? "on" : "off",
         "--quiet-hours", root.quietHours, "--daily-cap", String(root.dailyCap),
         "--min-tier", root.minTier, "--poll-interval", String(root.pollIntervalSec), "--json"])
  }

  function search(text) {
    var query = (text || "").trim()
    searchQuery = query
    if (!query) {
      searchResults = []
      searchError = ""
      return
    }
    if (searchProcess.running) {
      _pendingSearch = query
      return
    }
    searchProcess.command = [root.cli, "watchlist", "search", "--json", "--", query]
    searchProcess.running = true
  }

  function clearSearch() {
    searchQuery = ""
    _pendingSearch = ""
    searchResults = []
    searchError = ""
  }

  // Edits go through the CLI, which calls Questflow and nudges the daemon to
  // resync. The removal shows at once rather than a sync later.
  function addSymbol(symbol) { run([root.cli, "watchlist", "add", "--json", "--", symbol]) }

  function removeSymbol(symbol) {
    if (data && data.watchlist) {
      var copy = JSON.parse(JSON.stringify(data))
      var sections = copy.watchlist.sections
      for (var s = 0; s < sections.length; s++)
        sections[s].entries = sections[s].entries.filter(function (e) { return e.symbol !== symbol })
      copy.watchlist.sections = sections.filter(function (sec) { return sec.entries.length > 0 })
      data = copy
    }
    run([root.cli, "watchlist", "remove", "--json", "--", symbol])
  }

  function openSignal(row) {
    if (row.url) Quickshell.execDetached([root.cli, "open", "--", row.url])
    if (!row.read && root.connected) run([root.cli, "ack", "--json", "--", row.id])
  }

  function toggleRule(row) {
    if (row.kind === "rule")
      run([root.cli, "rules", row.on ? "disable" : "enable", "--json", "--", row.id])
    else
      run([root.cli, "skills", row.on ? "unsubscribe" : "subscribe", "--json", "--", row.id])
  }

  function deleteRule(id) { run([root.cli, "rules", "delete", "--yes", "--json", "--", id]) }

  function draftRule(text) {
    var words = (text || "").trim()
    ruleDraft = null
    ruleError = ""
    if (!words || draftProcess.running) return
    draftProcess.command = [root.cli, "rules", "draft", "--json", "--", words]
    draftProcess.running = true
  }

  function confirmRule() {
    if (!ruleDraft) return
    run([root.cli, "rules", "confirm", "--json", "--digest", ruleDraft.digest, "--", ruleDraft.draftId])
    ruleDraft = null
  }

  function cancelRule() {
    ruleDraft = null
    ruleError = ""
  }

  // Sign-in waits on the browser for up to five minutes, so it gets its own
  // process rather than holding up the action queue.
  readonly property bool signingIn: loginProcess.running
  function connectQuestflow() {
    if (loginProcess.running) return
    root.lastError = ""
    loginProcess.command = [root.cli, "login", "--json"]
    loginProcess.running = true
  }
  function disconnectQuestflow() { run([root.cli, "logout", "--json"]) }
  function openUrl(url) { Quickshell.execDetached([root.cli, "open", "--", url]) }
  function openQuestflow() { Quickshell.execDetached([root.cli, "open"]) }

  function run(command) { actions.run(command) }

  Component.onCompleted: {
    // Starts the daemon if it is not running (systemd unit, else detached).
    run([root.cli, "daemon", "ensure"])
    pushSettings()
  }

  // A theme switch is a new palette; the daemon re-reads the theme itself,
  // the nudge only makes it immediate.
  Connections {
    target: Color
    function onThemeShellValuesChanged() { Qt.callLater(root.refresh) }
  }

  FileView {
    id: snapshot
    path: root.snapshotPath
    watchChanges: true
    onFileChanged: reload()
    onLoaded: root.load()
  }

  // The daemon replaces the file atomically, which a file watch can miss;
  // a cheap re-read covers it. Reading a local file, not polling a server.
  Timer {
    interval: 10000
    repeat: true
    running: true
    onTriggered: snapshot.reload()
  }

  // If the daemon has not written anything for a while, make sure it runs.
  Timer {
    interval: 120000
    repeat: true
    running: true
    onTriggered: {
      var written = root.data && root.data.updatedAt ? root.data.updatedAt : 0
      if (Date.now() - written > 120000) root.run([root.cli, "daemon", "ensure"])
    }
  }

  Process {
    id: searchProcess
    running: false
    command: []
    property string query: ""
    onStarted: query = command.length > 0 ? command[command.length - 1] : ""
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        // An answer to a question no longer being asked is dropped.
        if (searchProcess.query !== root.searchQuery) return
        var parsed = Model.parse(text)
        root.searchResults = parsed.data && parsed.data.results ? parsed.data.results : []
        root.searchError = parsed.error
      }
    }
    onRunningChanged: {
      if (running || !root._pendingSearch) return
      var next = root._pendingSearch
      root._pendingSearch = ""
      Qt.callLater(function () { root.search(next) })
    }
  }

  Process {
    id: loginProcess
    running: false
    command: []
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        var parsed = Model.parse(text)
        root.lastError = parsed.error
        snapshot.reload()
      }
    }
  }

  Process {
    id: draftProcess
    running: false
    command: []
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        var parsed = Model.parse(text)
        if (parsed.error || !parsed.data || !parsed.data.draftId) {
          root.ruleError = parsed.error || "Questflow could not draft that rule"
          return
        }
        root.ruleDraft = parsed.data
      }
    }
  }

  // One action at a time, in order: an add and a remove racing each other
  // would leave whichever finished last.
  Process {
    id: actions
    running: false
    command: []
    property var queue: []
    property bool started: false
    function run(command) {
      queue = queue.concat([command])
      if (!running) next()
    }
    function next() {
      if (!queue.length) {
        snapshot.reload()
        return
      }
      command = queue[0]
      queue = queue.slice(1)
      started = false
      running = true
    }
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        var parsed = Model.parse(text)
        root.lastError = parsed.error
      }
    }
    // A binary that is not there never starts: Quickshell emits neither
    // started nor exited, and only drops running.
    onStarted: {
      started = true
      root.missing = false
    }
    onRunningChanged: {
      if (running) return
      if (!started) root.missing = true
      Qt.callLater(next)
    }
  }
}
