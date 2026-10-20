import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import "Model.js" as Model

// Polling omatrade for the watchlist, the news and the positions.
//
// One service for the whole shell, however many monitors have a bar. It is
// also what notifies: the CLI sends a notification for each breaking story it
// has not announced before, so a second bar never means a second notification.
Item {
  id: root

  property var settings: ({})
  property var data: null
  // Shared by every monitor's panel: the list showing, and whether the next
  // panel to open should start on the search field.
  property string view: "watchlist"
  property bool pendingAdd: false
  property string lastError: ""
  // The CLI is not on the path: the panel hides rather than reporting an
  // error nobody can fix from the bar. The next poll that starts clears it.
  property bool missing: false
  property double updatedAt: 0
  readonly property bool refreshing: summaryProcess.running

  // The CLI that ships inside this plugin, so `omarchy plugin add` is the
  // whole install and nothing has to be on the PATH.
  readonly property string cli: decodeURIComponent(
    String(Qt.resolvedUrl("bin/omatrade")).replace(/^(file|qs):\/\//, ""))

  readonly property var watchlist: data && data.watchlist ? data.watchlist : null
  readonly property var news: data && data.news ? data.news : null
  readonly property var questflow: data && data.questflow ? data.questflow : ({ connected: false })
  readonly property bool connected: !!questflow.connected
  readonly property var totals: data && data.totals ? data.totals : null
  readonly property var accounts: data && data.accounts ? data.accounts : []
  readonly property var colors: data && data.colors ? data.colors : null
  readonly property bool mock: !!(data && data.mock)

  // Symbol search, for adding to the watchlist from the panel.
  property var searchResults: []
  property string searchError: ""
  property string searchQuery: ""
  readonly property bool searching: searchProcess.running
  property string _pendingSearch: ""

  readonly property int refreshIntervalSec: {
    var value = parseInt(String(settings && settings.refreshIntervalSec
      ? settings.refreshIntervalSec : 60), 10)
    return isFinite(value) ? Math.max(15, Math.min(1800, value)) : 60
  }
  readonly property bool notify: !(settings && settings.notify === false)

  property string _output: ""
  property string _error: ""
  property bool _started: false
  property bool _refreshPending: false

  function refresh() {
    if (summaryProcess.running) {
      _refreshPending = true
      return
    }
    var command = [root.cli, "summary", "--json"]
    if (root.notify) command.push("--notify")
    summaryProcess.command = command
    root._started = false
    summaryProcess.running = true
  }

  function refreshIfStale() {
    if (Date.now() - updatedAt > 15000) refresh()
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
    searchProcess.command = [root.cli, "tv", "search", "--json", "--", query]
    searchProcess.running = true
  }

  function clearSearch() {
    searchQuery = ""
    _pendingSearch = ""
    searchResults = []
    searchError = ""
  }

  // Edits go through the CLI, which owns the file and pokes the bar to
  // refresh. The removal shows at once rather than a poll later.
  function addSymbol(symbol) {
    edit.run([root.cli, "tv", "add", "--", symbol])
  }

  function removeSymbol(symbol) {
    if (data && data.watchlist) {
      var copy = JSON.parse(JSON.stringify(data))
      var sections = copy.watchlist.sections
      for (var s = 0; s < sections.length; s++)
        sections[s].entries = sections[s].entries.filter(function (e) { return e.symbol !== symbol })
      copy.watchlist.sections = sections.filter(function (sec) { return sec.entries.length > 0 })
      data = copy
    }
    edit.run([root.cli, "tv", "remove", "--", symbol])
  }

  // Reorder: next to another symbol (before or after it), or to the top of a
  // section. Shown at once, written by the CLI.
  function moveSymbol(symbol, anchor, after, section) {
    if (data && data.watchlist) {
      var copy = JSON.parse(JSON.stringify(data))
      var sections = copy.watchlist.sections
      var moving = null
      for (var s = 0; s < sections.length && !moving; s++) {
        for (var e = 0; e < sections[s].entries.length; e++) {
          if (sections[s].entries[e].symbol === symbol) {
            moving = sections[s].entries.splice(e, 1)[0]
            break
          }
        }
      }
      if (!moving) return
      var placed = false
      for (s = 0; s < sections.length && !placed; s++) {
        var entries = sections[s].entries
        if (anchor) {
          for (e = 0; e < entries.length; e++) {
            if (entries[e].symbol === anchor) {
              entries.splice(e + (after ? 1 : 0), 0, moving)
              placed = true
              break
            }
          }
        } else if (sections[s].name === section) {
          entries.unshift(moving)
          placed = true
        }
      }
      if (!placed) return
      copy.watchlist.sections = sections.filter(function (sec) { return sec.entries.length > 0 })
      data = copy
    }
    var command = [root.cli, "tv", "move"]
    command.push(anchor ? (after ? "--after=" : "--before=") + anchor : "--section=" + section)
    command.push("--", symbol)
    edit.run(command)
  }

  function connectQuestflow() { edit.run([root.cli, "login"]) }
  function disconnectQuestflow() { edit.run([root.cli, "logout"]) }

  function openTradingView(symbol) {
    Quickshell.execDetached([root.cli, "tv", "open", "--", symbol])
  }

  function openUrl(url) {
    Quickshell.execDetached([root.cli, "open", "--", url])
  }

  function openQuestflow() {
    Quickshell.execDetached([root.cli, "open"])
  }

  Component.onCompleted: refresh()

  // A theme switch is a new palette, and the CLI is what reads it.
  Connections {
    target: Color
    function onThemeShellValuesChanged() { Qt.callLater(root.refresh) }
  }

  Timer {
    interval: root.refreshIntervalSec * 1000
    repeat: true
    running: true
    onTriggered: root.refresh()
  }

  Process {
    id: summaryProcess
    running: false
    command: []
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: root._output = text
    }
    stderr: StdioCollector {
      waitForEnd: true
      onStreamFinished: root._error = text
    }
    // A binary that is not there never starts: Quickshell emits neither
    // started nor exited, and only drops running.
    onStarted: {
      root._started = true
      root.missing = false
    }
    onRunningChanged: {
      if (running) return
      if (!root._started) root.missing = true
      if (root._refreshPending) {
        root._refreshPending = false
        Qt.callLater(root.refresh)
      }
    }
    onExited: function (code) {
      if (code !== 0) {
        root.lastError = root._error.trim() || "omatrade exited with code " + code
        return
      }
      var parsed = Model.parse(root._output)
      if (parsed.error || !parsed.data) {
        root.lastError = parsed.error || "omatrade returned nothing"
        return
      }
      // Replaced only on a good answer: a failed refresh leaves the last
      // numbers on screen rather than blanking the bar.
      root.data = parsed.data
      root.lastError = ""
      root.updatedAt = Date.now()
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
        root.searchError = parsed.data && parsed.data.error ? parsed.data.error : parsed.error
      }
    }
    onRunningChanged: {
      if (running || !root._pendingSearch) return
      var next = root._pendingSearch
      root._pendingSearch = ""
      Qt.callLater(function () { root.search(next) })
    }
  }

  // One edit at a time, in order: an add and a remove racing each other on
  // the same file would leave whichever finished last.
  Process {
    id: edit
    running: false
    command: []
    property var queue: []
    function run(command) {
      queue = queue.concat([command])
      if (!running) next()
    }
    function next() {
      if (!queue.length) {
        root.refresh()
        return
      }
      command = queue[0]
      queue = queue.slice(1)
      running = true
    }
    onRunningChanged: if (!running) Qt.callLater(next)
  }
}
