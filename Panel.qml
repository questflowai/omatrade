import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui
import "Model.js" as Model
import "ServiceHost.js" as ServiceHost

// Questflow, in the bar.
//
// Four lists, in the order you reach for them: what you watch, the signals
// that matter to you, what you hold across your Questflow accounts, and the
// rules and skill feeds that decide which signals you get. One sign-in
// unlocks the personal parts; nothing else is ever connected from here.
Panel {
  id: root
  moduleName: "questflow.omatrade"
  ipcTarget: "questflow.omatrade"
  // Its own handler, so the CLI can push a refresh the moment the snapshot
  // changes and a keybinding can open the panel on any list:
  //   omarchy-shell questflow.omatrade watchlist|signals|positions|rules|add|toggle
  manageIpc: false

  readonly property var views: [
    { key: "watchlist", label: "Watchlist" },
    { key: "signals", label: "Signals" },
    { key: "positions", label: "Positions" },
    { key: "rules", label: "Rules" }
  ]
  // Which list is showing lives in the service, not here: there is one panel
  // per monitor, IPC reaches only one of them, and the one that opens has to
  // agree with it.
  readonly property string view: service.view
  property int selectedIndex: -1

  // Adding to the watchlist: a search field takes the list's place, and the
  // results are what the arrows and Enter move through.
  property bool adding: false
  property int searchIndex: 0
  // Writing a rule in words; the server's draft is confirmed with Enter.
  property bool composing: false
  // Deleting a rule takes two presses of x on the same row.
  property string confirmDeleteId: ""

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  readonly property bool gone: service.missing
  visible: !gone

  QtObject {
    id: dummyService
    property string view: "watchlist"
    property bool pendingAdd: false
    property var data: null
    property var watchlist: null
    property var signalFeed: null
    property var auth: ({ signedIn: false, state: "signed_out" })
    property bool connected: false
    property bool configured: false
    property var positions: null
    property var totals: null
    property var accounts: []
    property var rules: []
    property var skills: []
    property var colors: null
    property string connectionLabel: ""
    property bool stale: false
    property int unread: 0
    property string disclaimer: "Not investment advice."
    property var searchResults: []
    property string searchError: ""
    property bool searching: false
    property var ruleDraft: null
    property string ruleError: ""
    property bool drafting: false
    property bool signingIn: false
    property string lastError: ""
    property bool missing: false
    property bool refreshing: false
    property double updatedAt: 0
    function refresh() {}
    function refreshIfStale() {}
    function search() {}
    function clearSearch() {}
    function addSymbol() {}
    function removeSymbol() {}
    function openSignal() {}
    function toggleRule() {}
    function deleteRule() {}
    function draftRule() {}
    function confirmRule() {}
    function cancelRule() {}
    function connectQuestflow() {}
    function disconnectQuestflow() {}
    function openUrl() {}
    function openQuestflow() {}
  }

  readonly property var hostedService: {
    var _ = bar && bar.shell ? bar.shell._services : null
    return ServiceHost.hostedService(bar)
  }
  readonly property var service: hostedService !== null ? hostedService : dummyService

  readonly property var watchRows: Model.watchlistRows(service.watchlist)
  readonly property var searchRows: Model.searchRows(service.searchResults)
  readonly property var signalRows: Model.signalRows(service.signalFeed)
  readonly property var positionRows: Model.positionRows(service.accounts)
  readonly property var ruleRows: Model.ruleRows(service.rules, service.skills)
  readonly property var rows: view === "signals" ? signalRows
    : view === "positions" ? positionRows
    : view === "rules" ? ruleRows : watchRows
  readonly property var totals: service.totals
  readonly property bool personalView: view === "positions" || view === "rules"

  readonly property color foreground: bar ? bar.foreground : Color.foreground
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family

  readonly property var palette: Model.palette(service.colors, Color.popups.text, Color.popups.background)
  readonly property color text: palette.text
  readonly property color muted: palette.muted
  readonly property color hover: palette.hover

  // The first quote on the watchlist, for the tooltip.
  readonly property var headline: {
    for (var i = 0; i < watchRows.length; i++)
      if (!watchRows[i].header && watchRows[i].hasQuote) return watchRows[i]
    return null
  }

  // Ticks the "12m" column along while the panel is open.
  property double now: Date.now()
  Timer {
    interval: 30000
    repeat: true
    running: root.opened
    triggeredOnStart: true
    onTriggered: root.now = Date.now()
  }

  function colorFor(value) { return palette[Model.direction(value)] }
  function tagFor(value) { return palette.tags[Model.direction(value)] }
  function riskColor(risk) { return palette[Model.riskRole(risk)] }

  function showView(name) {
    if (view === name) return
    stopAdding()
    stopComposing()
    confirmDeleteId = ""
    service.view = name
    selectedIndex = -1
  }

  function stepView(delta) {
    var at = 0
    for (var i = 0; i < views.length; i++) if (views[i].key === view) at = i
    showView(views[Math.max(0, Math.min(views.length - 1, at + delta))].key)
  }

  function moveSelection(delta) {
    var picks = Model.selectableIndexes(rows)
    if (!picks.length) return
    var at = picks.indexOf(selectedIndex)
    if (at < 0) at = delta > 0 ? 0 : picks.length - 1
    else at = Math.max(0, Math.min(picks.length - 1, at + delta))
    selectedIndex = picks[at]
    confirmDeleteId = ""
  }

  function activate(row) {
    if (personalView && !service.connected) {
      service.connectQuestflow()
      return
    }
    if (view === "watchlist") {
      if (!row || row.header || !row.url) return
      service.openUrl(row.url)
    } else if (view === "signals") {
      if (!row || row.header) return
      service.openSignal(row)
    } else if (view === "rules") {
      if (row && !row.header) service.toggleRule(row)
      return
    } else {
      service.openQuestflow()
    }
    close()
  }

  function removeSelected() {
    var row = rows[selectedIndex]
    if (!row || row.header || !service.connected) return
    if (view === "rules") {
      if (row.kind !== "rule") return
      if (confirmDeleteId !== row.id) {
        confirmDeleteId = row.id
        return
      }
      confirmDeleteId = ""
      service.deleteRule(row.id)
      return
    }
    if (view !== "watchlist") return
    service.removeSymbol(row.symbol)
    // Stay on the row below, which is now at this index.
    Qt.callLater(function () {
      var picks = Model.selectableIndexes(root.rows)
      if (!picks.length) root.selectedIndex = -1
      else if (picks.indexOf(root.selectedIndex) < 0) root.selectedIndex = picks[picks.length - 1]
    })
  }

  function startAdding() {
    if (view === "rules") {
      startComposing()
      return
    }
    if (!service.connected) {
      service.connectQuestflow()
      return
    }
    if (view !== "watchlist") showView("watchlist")
    adding = true
    searchIndex = 0
    searchField.text = ""
    service.clearSearch()
    Qt.callLater(function () { searchField.forceActiveFocus() })
  }

  function stopAdding() {
    if (!adding) return
    adding = false
    searchDebounce.stop()
    service.clearSearch()
    keyCatcher.forceActiveFocus()
  }

  function commitAdd(index) {
    var picked = searchRows[index !== undefined ? index : searchIndex]
    if (!picked) return
    service.addSymbol(picked.symbol)
    stopAdding()
    selectedIndex = -1
  }

  function startComposing() {
    if (!service.connected) {
      service.connectQuestflow()
      return
    }
    composing = true
    ruleField.text = ""
    service.cancelRule()
    Qt.callLater(function () { ruleField.forceActiveFocus() })
  }

  function stopComposing() {
    if (!composing) return
    composing = false
    service.cancelRule()
    keyCatcher.forceActiveFocus()
  }

  // First Enter asks Questflow to draft; the second saves exactly what is
  // shown. Editing the words throws the draft away.
  function commitRule() {
    if (service.ruleDraft) {
      service.confirmRule()
      stopComposing()
    } else {
      service.draftRule(ruleField.text)
    }
  }

  function openOn(name, add) {
    if (gone) return
    showView(name)
    if (opened) {
      if (add) startAdding()
      return
    }
    service.pendingAdd = !!add
    open()
  }

  onOpenedChanged: {
    if (!opened) {
      stopAdding()
      stopComposing()
      confirmDeleteId = ""
      return
    }
    service.refreshIfStale()
    selectedIndex = -1
    if (service.pendingAdd) {
      service.pendingAdd = false
      Qt.callLater(startAdding)
    }
  }
  onGoneChanged: if (gone) close()

  IpcHandler {
    target: "questflow.omatrade"

    function open(): void { if (!root.gone) root.open() }
    function close(): void { root.close() }
    function toggle(): void { if (!root.gone || root.opened) root.toggle() }
    function refresh(): void { root.service.refresh() }
    function watchlist(): void { root.openOn("watchlist") }
    function signals(): void { root.openOn("signals") }
    function positions(): void { root.openOn("positions") }
    function rules(): void { root.openOn("rules") }
    function add(): void { root.openOn("watchlist", true) }
  }

  Timer {
    id: searchDebounce
    interval: 250
    onTriggered: root.service.search(searchField.text)
  }

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    iconComponent: Component {
      Item {
        OmatradeIcon {
          anchors.centerIn: parent
          iconSize: Style.space(13)
          color: root.foreground
        }
        // Unread signals: a dot, not a number. The bar is not an inbox.
        Rectangle {
          visible: root.service.unread > 0
          width: Style.space(5)
          height: width
          radius: width / 2
          anchors.right: parent.right
          anchors.top: parent.top
          color: root.palette.accent
        }
      }
    }
    tooltipText: root.headline
      ? root.headline.display + "  " + Model.formatPrice(root.headline.last)
        + "  " + Model.formatPercent(root.headline.changePct)
        + (root.service.unread > 0 ? "  ·  " + root.service.unread + " unread" : "")
      : "Omatrade"
    onPressed: function (buttonCode) {
      if (buttonCode === Qt.RightButton || buttonCode === Qt.MiddleButton) root.service.refresh()
      else root.toggle()
    }
  }

  KeyboardPanel {
    id: panel
    anchorItem: button
    owner: root
    bar: root.bar
    open: root.opened
    focusTarget: keyCatcher
    contentWidth: panel.fittedContentWidth(Style.space(400))
    contentHeight: panel.fittedContentHeight(
      Math.min(top.implicitHeight + foot.implicitHeight + body.implicitHeight + Style.space(40),
        Style.space(680)),
      Style.space(680))

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      // While typing, every key is the text field's: the catcher would
      // otherwise take Enter as "open the selected row" and j/k/h/l/x as
      // navigation, before the field ever saw them.
      blocked: root.adding || root.composing
      onMoveRequested: function (dx, dy) {
        if (dx !== 0) root.stepView(dx)
        else if (dy !== 0) root.moveSelection(dy)
      }
      onActivateRequested: root.activate(root.rows[root.selectedIndex])
      onDeleteRequested: root.removeSelected()
      onCloseRequested: root.close()
      onTabRequested: function (direction) { root.switchPanel(direction) }
      onTextKey: function (text) {
        if (text === "r" || text === "R") root.service.refresh()
        else if (text === "1") root.showView("watchlist")
        else if (text === "2") root.showView("signals")
        else if (text === "3") root.showView("positions")
        else if (text === "4") root.showView("rules")
        else if (text === "a" || text === "/" || text === "+") root.startAdding()
      }

      ColumnLayout {
        anchors.fill: parent
        spacing: Style.space(10)

        // ── Title, status and tabs ───────────────────────────────────────
        ColumnLayout {
          id: top
          Layout.fillWidth: true
          spacing: Style.space(10)

          RowLayout {
            Layout.fillWidth: true
            spacing: Style.space(8)

            Item {
              implicitWidth: titleRow.implicitWidth
              implicitHeight: titleRow.implicitHeight + Style.space(6)

              Rectangle {
                anchors.fill: parent
                anchors.leftMargin: -Style.space(6)
                anchors.rightMargin: -Style.space(6)
                radius: Style.space(6)
                color: root.hover
                opacity: titleHover.hovered ? 1 : 0
                Behavior on opacity { NumberAnimation { duration: 90 } }
              }

              HoverHandler { id: titleHover }
              TapHandler { onTapped: { root.service.openQuestflow(); root.close() } }

              RowLayout {
                id: titleRow
                anchors.centerIn: parent
                spacing: Style.space(7)

                OmatradeIcon {
                  Layout.alignment: Qt.AlignVCenter
                  iconSize: Style.space(15)
                  color: root.text
                }
                Text {
                  textFormat: Text.PlainText
                  text: "Omatrade"
                  font.family: root.fontFamily
                  font.pixelSize: Style.space(14)
                  font.bold: true
                  color: root.text
                }
                Text {
                  textFormat: Text.PlainText
                  text: "Powered by Questflow"
                  font.family: root.fontFamily
                  font.pixelSize: Style.space(11)
                  color: root.muted
                }
              }
            }

            Item { Layout.fillWidth: true }

            // Not live: say so. Offline data stays on screen, marked.
            Rectangle {
              visible: root.service.connectionLabel !== ""
              implicitWidth: statusLabel.implicitWidth + Style.space(10)
              implicitHeight: Style.space(18)
              radius: Style.space(4)
              color: root.service.connectionLabel === "OFFLINE" || root.service.connectionLabel === "STALE"
                ? root.palette.tags.down.fill : root.palette.tags.flat.fill
              Text {
                id: statusLabel
                textFormat: Text.PlainText
                anchors.centerIn: parent
                text: root.service.connectionLabel
                font.family: root.fontFamily
                font.pixelSize: Style.space(9)
                font.bold: true
                font.letterSpacing: 0.6
                color: root.service.connectionLabel === "OFFLINE" || root.service.connectionLabel === "STALE"
                  ? root.palette.tags.down.label : root.palette.tags.flat.label
              }
            }

            Item {
              id: spinner
              visible: root.service.searching || root.service.drafting || root.service.signingIn
              implicitWidth: Style.space(13)
              implicitHeight: Style.space(13)
              Layout.alignment: Qt.AlignVCenter

              Repeater {
                model: 8
                Rectangle {
                  width: Style.space(2.5)
                  height: width
                  radius: width / 2
                  color: root.muted
                  opacity: 0.25 + 0.75 * (index / 7)
                  x: spinner.width / 2 - width / 2
                     + Math.cos(index * Math.PI / 4) * (spinner.width / 2 - width / 2)
                  y: spinner.height / 2 - height / 2
                     + Math.sin(index * Math.PI / 4) * (spinner.height / 2 - height / 2)
                }
              }

              RotationAnimation on rotation {
                from: 0
                to: 360
                duration: 1200
                loops: Animation.Infinite
                running: spinner.visible
              }
            }

            // Add: a symbol on the watchlist, a rule on the rules list.
            Rectangle {
              visible: (root.view === "watchlist" || root.view === "rules")
                && root.service.connected && !root.adding && !root.composing
              implicitWidth: Style.space(24)
              implicitHeight: Style.space(24)
              radius: Style.space(6)
              color: addHover.hovered ? root.hover : "transparent"
              HoverHandler { id: addHover }
              TapHandler { onTapped: root.startAdding() }
              Text {
                textFormat: Text.PlainText
                anchors.centerIn: parent
                text: "+"
                font.family: root.fontFamily
                font.pixelSize: Style.space(17)
                font.bold: true
                color: root.text
              }
            }
          }

          RowLayout {
            Layout.fillWidth: true
            spacing: Style.space(4)

            Repeater {
              model: root.views

              delegate: Item {
                readonly property bool current: root.view === modelData.key
                Layout.fillWidth: true
                implicitHeight: Style.space(30)

                Rectangle {
                  anchors.fill: parent
                  radius: Style.space(6)
                  color: root.hover
                  opacity: parent.current ? 1 : (tabHover.hovered ? 0.5 : 0)
                  Behavior on opacity { NumberAnimation { duration: 90 } }
                }

                HoverHandler { id: tabHover }
                TapHandler { onTapped: root.showView(modelData.key) }

                Text {
                  textFormat: Text.PlainText
                  anchors.centerIn: parent
                  text: modelData.label + (modelData.key === "signals" && root.service.unread > 0
                    ? " · " + root.service.unread : "")
                  font.family: root.fontFamily
                  font.pixelSize: Style.space(12)
                  font.bold: parent.current
                  color: parent.current ? root.text : root.muted
                }
              }
            }
          }

          Text {
            textFormat: Text.PlainText
            Layout.fillWidth: true
            visible: root.service.lastError !== ""
            text: root.service.lastError
            wrapMode: Text.WordWrap
            font.family: root.fontFamily
            font.pixelSize: Style.space(11)
            color: root.muted
          }
        }

        // ── Body ─────────────────────────────────────────────────────────
        ColumnLayout {
          id: body
          Layout.fillWidth: true
          Layout.fillHeight: true
          spacing: Style.space(8)

          // Not configured: nothing to sign in to yet.
          Text {
            textFormat: Text.PlainText
            Layout.fillWidth: true
            visible: !!root.service.data && !root.service.configured
            text: "Omatrade has no Questflow API configured yet. See the README: Configure."
            wrapMode: Text.WordWrap
            font.family: root.fontFamily
            font.pixelSize: Style.space(11)
            color: root.muted
          }

          // Search, in place of the list, while adding to the watchlist.
          RowLayout {
            Layout.fillWidth: true
            visible: root.adding
            spacing: Style.space(8)

            TextField {
              id: searchField
              Layout.fillWidth: true
              placeholderText: "Search symbols — NVDA, BTC, Tencent…"
              foreground: root.text
              font.family: root.fontFamily
              onTextChanged: {
                if (!root.adding) return
                root.searchIndex = 0
                searchDebounce.restart()
              }
              Keys.onPressed: function (event) {
                if (event.key === Qt.Key_Escape) {
                  root.stopAdding()
                  event.accepted = true
                } else if (event.key === Qt.Key_Down) {
                  root.searchIndex = Math.min(root.searchRows.length - 1, root.searchIndex + 1)
                  event.accepted = true
                } else if (event.key === Qt.Key_Up) {
                  root.searchIndex = Math.max(0, root.searchIndex - 1)
                  event.accepted = true
                } else if (event.key === Qt.Key_Return || event.key === Qt.Key_Enter) {
                  root.commitAdd()
                  event.accepted = true
                }
              }
            }

            Text {
              textFormat: Text.PlainText
              text: "Cancel"
              font.family: root.fontFamily
              font.pixelSize: Style.space(11)
              color: cancelHover.hovered ? root.text : root.muted
              HoverHandler { id: cancelHover }
              TapHandler { onTapped: root.stopAdding() }
            }
          }

          Text {
            textFormat: Text.PlainText
            Layout.fillWidth: true
            visible: root.adding && (root.service.searchError !== ""
              || (searchField.text.trim() !== "" && !root.service.searching && root.searchRows.length === 0))
            text: root.service.searchError || "Nothing matches that."
            wrapMode: Text.WordWrap
            font.family: root.fontFamily
            font.pixelSize: Style.space(11)
            color: root.muted
          }

          // A rule in words, then the structured rule to confirm.
          ColumnLayout {
            Layout.fillWidth: true
            visible: root.composing
            spacing: Style.space(6)

            TextField {
              id: ruleField
              Layout.fillWidth: true
              placeholderText: "Describe an alert — e.g. BTC funding above 0.05% on 8h"
              foreground: root.text
              font.family: root.fontFamily
              onTextChanged: if (root.composing) root.service.cancelRule()
              Keys.onPressed: function (event) {
                if (event.key === Qt.Key_Escape) {
                  root.stopComposing()
                  event.accepted = true
                } else if (event.key === Qt.Key_Return || event.key === Qt.Key_Enter) {
                  root.commitRule()
                  event.accepted = true
                }
              }
            }

            Text {
              textFormat: Text.PlainText
              Layout.fillWidth: true
              visible: !!root.service.ruleDraft || root.service.ruleError !== ""
              text: root.service.ruleDraft ? root.service.ruleDraft.description : root.service.ruleError
              wrapMode: Text.WordWrap
              font.family: root.fontFamily
              font.pixelSize: Style.space(11)
              font.bold: !!root.service.ruleDraft
              color: root.service.ruleDraft ? root.text : root.muted
            }
          }

          // Personal views before signing in: what you get, and the button.
          ColumnLayout {
            Layout.fillWidth: true
            visible: root.personalView && !root.service.connected && root.service.configured
            spacing: Style.space(10)

            Item { implicitHeight: Style.space(8) }

            OmatradeIcon {
              Layout.alignment: Qt.AlignHCenter
              iconSize: Style.space(30)
              color: root.text
            }
            Text {
              textFormat: Text.PlainText
              Layout.fillWidth: true
              horizontalAlignment: Text.AlignHCenter
              text: root.service.auth.state === "expired" || root.service.auth.state === "revoked"
                ? "Your Questflow session ended" : "Sign in with Questflow"
              font.family: root.fontFamily
              font.pixelSize: Style.space(14)
              font.bold: true
              color: root.text
            }
            Text {
              textFormat: Text.PlainText
              Layout.fillWidth: true
              horizontalAlignment: Text.AlignHCenter
              wrapMode: Text.WordWrap
              text: root.service.auth.state === "keyring_unavailable"
                ? "No system keyring is available. Install and unlock one (for example gnome-keyring); Omatrade never stores sign-in tokens in plain files."
                : root.view === "positions"
                  ? "See the positions in your Questflow accounts, with liquidation distance and margin. Read-only: Omatrade never trades."
                  : "Write alert rules in plain words and subscribe to skill feeds. Matching runs on Questflow, so alerts arrive even while this laptop sleeps."
              font.family: root.fontFamily
              font.pixelSize: Style.space(11)
              color: root.muted
            }
            Rectangle {
              Layout.alignment: Qt.AlignHCenter
              visible: root.service.auth.state !== "keyring_unavailable"
              implicitWidth: connectLabel.implicitWidth + Style.space(28)
              implicitHeight: Style.space(32)
              radius: Style.space(7)
              color: root.palette.tags.accent.fill
              opacity: connectHover.hovered ? 0.88 : 1
              HoverHandler { id: connectHover }
              TapHandler { onTapped: root.service.connectQuestflow() }
              Text {
                id: connectLabel
                textFormat: Text.PlainText
                anchors.centerIn: parent
                text: root.service.signingIn ? "Waiting for the browser…"
                  : (root.service.auth.state === "expired" || root.service.auth.state === "revoked"
                    ? "Sign in again" : "Sign in with Questflow")
                font.family: root.fontFamily
                font.pixelSize: Style.space(12)
                font.bold: true
                color: root.palette.tags.accent.label
              }
            }
            Item { implicitHeight: Style.space(8) }
          }

          // Positions, signed in: everything you hold, and how close to the edge.
          ColumnLayout {
            Layout.fillWidth: true
            visible: root.view === "positions" && root.service.connected && !!root.totals
            spacing: Style.space(3)

            Text {
              textFormat: Text.PlainText
              text: "TOTAL EQUITY"
              font.family: root.fontFamily
              font.pixelSize: Style.space(10)
              font.letterSpacing: 0.6
              color: root.muted
            }

            RowLayout {
              Layout.fillWidth: true
              spacing: Style.space(10)

              Text {
                textFormat: Text.PlainText
                text: root.totals ? Model.formatMoney(root.totals.equity) : ""
                font.family: root.fontFamily
                font.pixelSize: Style.space(26)
                font.bold: true
                color: root.text
              }

              Item { Layout.fillWidth: true }

              Rectangle {
                readonly property var tag: root.tagFor(root.totals ? root.totals.dayPnl : 0)
                Layout.alignment: Qt.AlignVCenter
                implicitWidth: dayLabel.implicitWidth + Style.space(14)
                implicitHeight: Style.space(22)
                radius: Style.space(5)
                color: tag.fill
                Text {
                  id: dayLabel
                  textFormat: Text.PlainText
                  anchors.centerIn: parent
                  text: root.totals
                    ? Model.formatMoney(root.totals.dayPnl, true) + "  " + Model.formatPercent(root.totals.dayPnlPct)
                    : ""
                  font.family: root.fontFamily
                  font.pixelSize: Style.space(11)
                  font.bold: true
                  color: parent.tag.label
                }
              }
            }

            Text {
              textFormat: Text.PlainText
              Layout.fillWidth: true
              elide: Text.ElideRight
              text: root.totals
                ? root.totals.positions + " positions  ·  " + root.service.accounts.length + " accounts  ·  synced "
                  + Model.ago(root.totals.oldestSyncAt, root.now)
                  + (root.totals.stale || root.service.stale ? "  ·  STALE" : "")
                : ""
              font.family: root.fontFamily
              font.pixelSize: Style.space(11)
              color: root.totals && (root.totals.stale || root.service.stale) ? root.palette.down : root.muted
            }
          }

          Text {
            textFormat: Text.PlainText
            Layout.fillWidth: true
            visible: !root.adding && !root.composing && root.rows.length === 0 && root.service.lastError === ""
              && (!root.personalView || root.service.connected)
            text: root.view === "signals" ? "No signals yet."
              : root.view === "positions" ? "No positions in your Questflow accounts."
              : "Watchlist is empty. Press + to add a symbol."
            font.family: root.fontFamily
            font.pixelSize: Style.space(12)
            color: root.muted
          }

          ScrollView {
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            ScrollBar.horizontal.policy: ScrollBar.AlwaysOff

            ColumnLayout {
              id: listColumn
              // Clear of the scroll bar, which would otherwise sit on the ×.
              width: Math.max(0, parent.width - Style.space(10))
              spacing: Style.space(4)
              // ── Search results ─────────────────────────────────────────
              Repeater {
                model: root.adding ? root.searchRows : []

                delegate: Item {
                  Layout.fillWidth: true
                  implicitHeight: Style.space(40)

                  Rectangle {
                    anchors.fill: parent
                    anchors.leftMargin: -Style.space(6)
                    anchors.rightMargin: -Style.space(6)
                    radius: Style.space(6)
                    color: root.hover
                    opacity: index === root.searchIndex || resultHover.hovered ? 1 : 0
                  }
                  HoverHandler {
                    id: resultHover
                    onHoveredChanged: if (hovered) root.searchIndex = index
                  }
                  TapHandler { onTapped: root.commitAdd(index) }

                  RowLayout {
                    anchors.fill: parent
                    spacing: Style.space(8)

                    ColumnLayout {
                      Layout.fillWidth: true
                      Layout.minimumWidth: 0
                      spacing: 0
                      RowLayout {
                        spacing: Style.space(6)
                        Text {
                          textFormat: Text.PlainText
                          text: modelData ? modelData.display : ""
                          font.family: root.fontFamily
                          font.pixelSize: Style.space(13)
                          font.bold: true
                          color: root.text
                        }
                        Text {
                          textFormat: Text.PlainText
                          text: modelData ? modelData.exchange : ""
                          font.family: root.fontFamily
                          font.pixelSize: Style.space(9)
                          color: root.muted
                        }
                      }
                      Text {
                        textFormat: Text.PlainText
                        Layout.fillWidth: true
                        elide: Text.ElideRight
                        text: modelData ? modelData.name : ""
                        font.family: root.fontFamily
                        font.pixelSize: Style.space(10)
                        color: root.muted
                      }
                    }
                    Text {
                      textFormat: Text.PlainText
                      text: modelData ? modelData.type : ""
                      font.family: root.fontFamily
                      font.pixelSize: Style.space(10)
                      color: root.muted
                    }
                    Text {
                      textFormat: Text.PlainText
                      text: "Add"
                      visible: index === root.searchIndex
                      font.family: root.fontFamily
                      font.pixelSize: Style.space(11)
                      font.bold: true
                      color: root.palette.accent
                    }
                  }
                }
              }

              // ── Watchlist ──────────────────────────────────────────────
              // Omacharts' row, as close as it can be: ticker and name on
              // the left, price and the move as a pill on the right. A click
              // opens the symbol on Questflow; × takes it off your list.
              Repeater {
                model: root.view === "watchlist" && !root.adding ? root.watchRows : []

                delegate: Item {
                  id: watchRow
                  readonly property bool lit: index === root.selectedIndex || watchHover.hovered
                  readonly property bool removable: root.service.connected
                    && !!root.service.watchlist && root.service.watchlist.editable === true
                  Layout.fillWidth: true
                  implicitHeight: modelData && modelData.header ? Style.space(26) : Style.space(40)

                  Text {
                    textFormat: Text.PlainText
                    visible: !!modelData && !!modelData.header
                    anchors.left: parent.left
                    anchors.bottom: parent.bottom
                    anchors.bottomMargin: Style.space(4)
                    text: modelData && modelData.header ? modelData.name.toUpperCase() : ""
                    font.family: root.fontFamily
                    font.pixelSize: Style.space(10)
                    font.bold: true
                    font.letterSpacing: 0.6
                    color: root.muted
                  }

                  Rectangle {
                    anchors.fill: parent
                    anchors.leftMargin: -Style.space(6)
                    anchors.rightMargin: -Style.space(6)
                    radius: Style.space(6)
                    visible: !!modelData && !modelData.header
                    color: root.hover
                    opacity: watchRow.lit ? 1 : 0
                    Behavior on opacity { NumberAnimation { duration: 90 } }
                  }

                  HoverHandler {
                    id: watchHover
                    enabled: !!modelData && !modelData.header
                    onHoveredChanged: if (hovered) root.selectedIndex = index
                  }
                  TapHandler {
                    enabled: !!modelData && !modelData.header
                    onTapped: root.activate(modelData)
                  }

                  RowLayout {
                    visible: !modelData || !modelData.header
                    anchors.fill: parent
                    spacing: Style.space(8)

                    ColumnLayout {
                      Layout.fillWidth: true
                      Layout.minimumWidth: 0
                      spacing: 0
                      RowLayout {
                        Layout.fillWidth: true
                        spacing: Style.space(6)
                        Text {
                          textFormat: Text.PlainText
                          text: modelData && modelData.display ? modelData.display : ""
                          font.family: root.fontFamily
                          font.pixelSize: Style.space(13)
                          font.bold: true
                          color: root.text
                        }
                        Text {
                          textFormat: Text.PlainText
                          Layout.fillWidth: true
                          elide: Text.ElideRight
                          text: modelData && modelData.exchange ? modelData.exchange : ""
                          font.family: root.fontFamily
                          font.pixelSize: Style.space(9)
                          color: root.muted
                        }
                      }
                      Text {
                        textFormat: Text.PlainText
                        Layout.fillWidth: true
                        text: modelData && modelData.name ? modelData.name : ""
                        elide: Text.ElideRight
                        font.family: root.fontFamily
                        font.pixelSize: Style.space(10)
                        color: root.muted
                      }
                    }

                    Text {
                      textFormat: Text.PlainText
                      text: modelData ? Model.formatPrice(modelData.last) : ""
                      font.family: root.fontFamily
                      font.pixelSize: Style.space(13)
                      color: modelData && modelData.hasQuote === true ? root.text : root.muted
                    }

                    Rectangle {
                      readonly property var tag: root.tagFor(modelData ? modelData.changePct : 0)
                      visible: !!modelData && modelData.hasQuote === true
                      implicitWidth: Math.max(Style.space(62), watchPct.implicitWidth + Style.space(12))
                      implicitHeight: Style.space(20)
                      radius: Style.space(5)
                      color: tag.fill
                      Text {
                        textFormat: Text.PlainText
                        id: watchPct
                        anchors.centerIn: parent
                        text: modelData ? Model.formatPercent(modelData.changePct) : ""
                        font.family: root.fontFamily
                        font.pixelSize: Style.space(11)
                        font.bold: true
                        color: parent.tag.label
                      }
                    }

                    // Always holds its place, so the pills do not jump left
                    // when the pointer arrives.
                    Item {
                      implicitWidth: Style.space(18)
                      implicitHeight: Style.space(18)
                      Rectangle {
                        anchors.fill: parent
                        radius: width / 2
                        color: removeHover.hovered ? root.palette.tags.down.fill : "transparent"
                        visible: watchRow.lit && watchRow.removable
                      }
                      Text {
                        textFormat: Text.PlainText
                        anchors.centerIn: parent
                        visible: watchRow.lit && watchRow.removable
                        text: "×"
                        font.family: root.fontFamily
                        font.pixelSize: Style.space(14)
                        font.bold: true
                        color: removeHover.hovered ? root.palette.tags.down.label : root.muted
                      }
                      HoverHandler { id: removeHover }
                      TapHandler {
                        enabled: watchRow.removable
                        onTapped: {
                          root.selectedIndex = index
                          root.removeSelected()
                        }
                      }
                    }
                  }
                }
              }

              // ── Signals: what matched, newest first ────────────────────
              Repeater {
                model: root.view === "signals" ? root.signalRows : []

                delegate: Item {
                  Layout.fillWidth: true
                  implicitHeight: signalColumn.implicitHeight + Style.space(12)

                  Rectangle {
                    anchors.fill: parent
                    anchors.leftMargin: -Style.space(6)
                    anchors.rightMargin: -Style.space(6)
                    radius: Style.space(6)
                    color: root.hover
                    opacity: index === root.selectedIndex || signalHover.hovered ? 1 : 0
                    Behavior on opacity { NumberAnimation { duration: 90 } }
                  }

                  HoverHandler {
                    id: signalHover
                    onHoveredChanged: if (hovered) root.selectedIndex = index
                  }
                  TapHandler { onTapped: root.activate(modelData) }

                  ColumnLayout {
                    id: signalColumn
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.verticalCenter: parent.verticalCenter
                    spacing: Style.space(3)

                    RowLayout {
                      Layout.fillWidth: true
                      spacing: Style.space(6)

                      // Unread on any device: a dot. Read anywhere: gone everywhere.
                      Rectangle {
                        visible: !!modelData && !modelData.read
                        implicitWidth: Style.space(6)
                        implicitHeight: Style.space(6)
                        radius: Style.space(3)
                        color: root.palette.accent
                      }
                      // Only important and critical get a fill.
                      Rectangle {
                        visible: !!modelData && modelData.tierLabel !== ""
                        implicitWidth: tierLabel.implicitWidth + Style.space(8)
                        implicitHeight: Style.space(15)
                        radius: Style.space(3)
                        color: modelData && modelData.tier === "critical"
                          ? root.palette.tags.down.fill : root.palette.tags.accent.fill
                        Text {
                          id: tierLabel
                          textFormat: Text.PlainText
                          anchors.centerIn: parent
                          text: modelData ? modelData.tierLabel : ""
                          font.family: root.fontFamily
                          font.pixelSize: Style.space(8)
                          font.bold: true
                          color: modelData && modelData.tier === "critical"
                            ? root.palette.tags.down.label : root.palette.tags.accent.label
                        }
                      }
                      Text {
                        textFormat: Text.PlainText
                        text: modelData ? modelData.symbols.slice(0, 3).join("  ") : ""
                        font.family: root.fontFamily
                        font.pixelSize: Style.space(10)
                        font.bold: true
                        color: root.palette.accent
                      }
                      Item { Layout.fillWidth: true }
                      Text {
                        textFormat: Text.PlainText
                        text: modelData ? Model.ago(modelData.createdAt, root.now) : ""
                        font.family: root.fontFamily
                        font.pixelSize: Style.space(10)
                        color: root.muted
                      }
                    }

                    Text {
                      textFormat: Text.PlainText
                      Layout.fillWidth: true
                      text: modelData ? modelData.title : ""
                      wrapMode: Text.WordWrap
                      maximumLineCount: 2
                      elide: Text.ElideRight
                      font.family: root.fontFamily
                      font.pixelSize: Style.space(12)
                      font.bold: !!modelData && !modelData.read
                      color: root.text
                    }

                    Text {
                      textFormat: Text.PlainText
                      text: modelData ? modelData.source : ""
                      font.family: root.fontFamily
                      font.pixelSize: Style.space(10)
                      color: root.muted
                    }
                  }
                }
              }

              // ── Positions ──────────────────────────────────────────────
              Repeater {
                model: root.view === "positions" && root.service.connected ? root.positionRows : []

                delegate: Item {
                  Layout.fillWidth: true
                  implicitHeight: modelData && modelData.header ? Style.space(30) : Style.space(42)

                  RowLayout {
                    visible: !!modelData && !!modelData.header
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.bottom: parent.bottom
                    anchors.bottomMargin: Style.space(4)
                    spacing: Style.space(6)

                    Text {
                      textFormat: Text.PlainText
                      text: modelData && modelData.header ? modelData.name.toUpperCase() : ""
                      font.family: root.fontFamily
                      font.pixelSize: Style.space(10)
                      font.bold: true
                      font.letterSpacing: 0.6
                      color: root.muted
                    }
                    Text {
                      textFormat: Text.PlainText
                      Layout.fillWidth: true
                      elide: Text.ElideRight
                      text: modelData && modelData.header
                        ? modelData.label + (modelData.stale ? "  ·  STALE " + Model.ago(modelData.syncedAt, root.now) : "")
                        : ""
                      font.family: root.fontFamily
                      font.pixelSize: Style.space(10)
                      color: modelData && modelData.stale ? root.palette.down : root.muted
                      opacity: 0.8
                    }
                    Text {
                      textFormat: Text.PlainText
                      text: modelData && modelData.header ? Model.formatRatio(modelData.marginRatio) : ""
                      font.family: root.fontFamily
                      font.pixelSize: Style.space(10)
                      font.bold: !!modelData && modelData.risk !== "ok"
                      color: root.riskColor(modelData ? modelData.risk : "ok")
                    }
                    Text {
                      textFormat: Text.PlainText
                      text: modelData && modelData.header ? Model.formatCompactMoney(modelData.equity) : ""
                      font.family: root.fontFamily
                      font.pixelSize: Style.space(10)
                      color: root.muted
                    }
                    Text {
                      textFormat: Text.PlainText
                      text: modelData && modelData.header ? Model.formatPercent(modelData.dayPnlPct) : ""
                      font.family: root.fontFamily
                      font.pixelSize: Style.space(10)
                      font.bold: true
                      color: root.colorFor(modelData ? modelData.dayPnlPct : 0)
                    }
                  }

                  Rectangle {
                    anchors.fill: parent
                    anchors.leftMargin: -Style.space(6)
                    anchors.rightMargin: -Style.space(6)
                    radius: Style.space(6)
                    visible: !!modelData && !modelData.header
                    color: root.hover
                    opacity: index === root.selectedIndex || posHover.hovered ? 1 : 0
                    Behavior on opacity { NumberAnimation { duration: 90 } }
                  }

                  HoverHandler {
                    id: posHover
                    enabled: !!modelData && !modelData.header
                    onHoveredChanged: if (hovered) root.selectedIndex = index
                  }
                  TapHandler {
                    enabled: !!modelData && !modelData.header
                    onTapped: root.activate(modelData)
                  }

                  RowLayout {
                    visible: !modelData || !modelData.header
                    anchors.fill: parent
                    spacing: Style.space(8)

                    ColumnLayout {
                      Layout.fillWidth: true
                      Layout.minimumWidth: 0
                      spacing: 0
                      Text {
                        textFormat: Text.PlainText
                        Layout.fillWidth: true
                        text: modelData && modelData.title ? modelData.title : ""
                        elide: Text.ElideRight
                        font.family: root.fontFamily
                        font.pixelSize: Style.space(13)
                        font.bold: true
                        color: root.text
                      }
                      // Size, and how far the price can move before liquidation.
                      Text {
                        textFormat: Text.PlainText
                        Layout.fillWidth: true
                        text: modelData && modelData.subtitle ? modelData.subtitle : ""
                        elide: Text.ElideRight
                        font.family: root.fontFamily
                        font.pixelSize: Style.space(10)
                        font.bold: !!modelData && modelData.risk === "danger"
                        color: modelData && modelData.risk !== "ok" ? root.riskColor(modelData.risk) : root.muted
                      }
                    }

                    Sparkline {
                      // A market's question needs the room more than its
                      // odds need a shape.
                      visible: !!modelData && modelData.kind !== "prediction"
                        && !!modelData.spark && modelData.spark.length > 1
                      Layout.preferredWidth: Style.space(44)
                      Layout.preferredHeight: Style.space(20)
                      points: modelData && modelData.spark ? modelData.spark : []
                      stroke: root.colorFor(modelData && modelData.spark && modelData.spark.length > 1
                        ? modelData.spark[modelData.spark.length - 1] - modelData.spark[0] : 0)
                    }

                    ColumnLayout {
                      spacing: 0
                      Text {
                        textFormat: Text.PlainText
                        Layout.alignment: Qt.AlignRight
                        text: modelData ? Model.formatMoney(modelData.value) : ""
                        font.family: root.fontFamily
                        font.pixelSize: Style.space(12)
                        color: root.text
                      }
                      Text {
                        textFormat: Text.PlainText
                        Layout.alignment: Qt.AlignRight
                        text: modelData ? Model.formatMoney(modelData.pnl, true) : ""
                        font.family: root.fontFamily
                        font.pixelSize: Style.space(10)
                        color: root.colorFor(modelData ? modelData.pnl : 0)
                      }
                    }

                    Rectangle {
                      readonly property var tag: root.tagFor(modelData ? modelData.pnlPct : 0)
                      implicitWidth: Math.max(Style.space(62), pctLabel.implicitWidth + Style.space(12))
                      implicitHeight: Style.space(20)
                      radius: Style.space(5)
                      color: tag.fill
                      Text {
                        id: pctLabel
                        textFormat: Text.PlainText
                        anchors.centerIn: parent
                        text: modelData ? Model.formatPercent(modelData.pnlPct) : ""
                        font.family: root.fontFamily
                        font.pixelSize: Style.space(11)
                        font.bold: true
                        color: parent.tag.label
                      }
                    }
                  }
                }
              }

              // ── Rules and skill feeds ──────────────────────────────────
              Repeater {
                model: root.view === "rules" && root.service.connected && !root.composing ? root.ruleRows : []

                delegate: Item {
                  id: ruleRow
                  readonly property bool lit: index === root.selectedIndex || ruleHover.hovered
                  Layout.fillWidth: true
                  implicitHeight: modelData && modelData.header ? Style.space(26) : ruleColumn.implicitHeight + Style.space(12)

                  Text {
                    textFormat: Text.PlainText
                    visible: !!modelData && !!modelData.header
                    anchors.left: parent.left
                    anchors.bottom: parent.bottom
                    anchors.bottomMargin: Style.space(4)
                    text: modelData && modelData.header ? modelData.name.toUpperCase() : ""
                    font.family: root.fontFamily
                    font.pixelSize: Style.space(10)
                    font.bold: true
                    font.letterSpacing: 0.6
                    color: root.muted
                  }

                  Rectangle {
                    anchors.fill: parent
                    anchors.leftMargin: -Style.space(6)
                    anchors.rightMargin: -Style.space(6)
                    radius: Style.space(6)
                    visible: !!modelData && !modelData.header
                    color: root.hover
                    opacity: ruleRow.lit ? 1 : 0
                  }

                  HoverHandler {
                    id: ruleHover
                    enabled: !!modelData && !modelData.header
                    onHoveredChanged: if (hovered) root.selectedIndex = index
                  }
                  TapHandler {
                    enabled: !!modelData && !modelData.header
                    onTapped: root.activate(modelData)
                  }

                  RowLayout {
                    visible: !!modelData && !modelData.header
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.verticalCenter: parent.verticalCenter
                    spacing: Style.space(8)

                    ColumnLayout {
                      id: ruleColumn
                      Layout.fillWidth: true
                      Layout.minimumWidth: 0
                      spacing: Style.space(2)
                      Text {
                        textFormat: Text.PlainText
                        Layout.fillWidth: true
                        elide: Text.ElideRight
                        text: modelData && modelData.title ? modelData.title : ""
                        font.family: root.fontFamily
                        font.pixelSize: Style.space(12)
                        font.bold: true
                        color: modelData && modelData.on ? root.text : root.muted
                      }
                      Text {
                        textFormat: Text.PlainText
                        Layout.fillWidth: true
                        wrapMode: Text.WordWrap
                        maximumLineCount: 2
                        elide: Text.ElideRight
                        text: modelData && root.confirmDeleteId === modelData.id
                          ? "Press x again to delete this rule."
                          : (modelData && modelData.subtitle ? modelData.subtitle : "")
                        font.family: root.fontFamily
                        font.pixelSize: Style.space(10)
                        color: modelData && root.confirmDeleteId === modelData.id ? root.palette.down : root.muted
                      }
                    }

                    Rectangle {
                      readonly property var tag: modelData && modelData.on ? root.palette.tags.accent : root.palette.tags.flat
                      implicitWidth: Math.max(Style.space(40), onLabel.implicitWidth + Style.space(12))
                      implicitHeight: Style.space(20)
                      radius: Style.space(5)
                      color: tag.fill
                      Text {
                        id: onLabel
                        textFormat: Text.PlainText
                        anchors.centerIn: parent
                        text: modelData && modelData.on ? "ON" : "OFF"
                        font.family: root.fontFamily
                        font.pixelSize: Style.space(10)
                        font.bold: true
                        color: parent.tag.label
                      }
                    }
                  }
                }
              }
            }
          }
        }

        // ── Footer ───────────────────────────────────────────────────────
        ColumnLayout {
          id: foot
          Layout.fillWidth: true
          spacing: Style.space(6)

          Text {
            textFormat: Text.PlainText
            Layout.fillWidth: true
            visible: root.view === "watchlist" && !root.adding && !!root.service.watchlist
              && root.service.watchlist.source === "default" && root.service.configured
            text: "Default list. Sign in with Questflow to keep your own, on every device."
            wrapMode: Text.WordWrap
            font.family: root.fontFamily
            font.pixelSize: Style.space(10)
            color: root.muted
          }

          Text {
            textFormat: Text.PlainText
            Layout.fillWidth: true
            visible: root.view === "signals" && !root.service.connected && root.service.configured
            text: "Public signals. Sign in for your own rules and skill feeds."
            wrapMode: Text.WordWrap
            font.family: root.fontFamily
            font.pixelSize: Style.space(10)
            color: root.muted
          }

          // Always on the signals view.
          Text {
            textFormat: Text.PlainText
            Layout.fillWidth: true
            visible: root.view === "signals"
            text: root.service.disclaimer
            wrapMode: Text.WordWrap
            font.family: root.fontFamily
            font.pixelSize: Style.space(9)
            font.italic: true
            color: root.muted
          }

          RowLayout {
            Layout.fillWidth: true

            Text {
              textFormat: Text.PlainText
              Layout.fillWidth: true
              horizontalAlignment: Text.AlignHCenter
              text: root.adding ? "↑/↓ choose    ↵ add    esc cancel"
                : root.composing ? (root.service.ruleDraft ? "↵ save this rule    esc cancel" : "↵ draft    esc cancel")
                : root.personalView && !root.service.connected ? "←/→ switch    ↵ sign in"
                : root.view === "watchlist" ? "←/→ switch    ↵ open    a add    x remove"
                : root.view === "signals" ? "←/→ switch    ↵ open and mark read    r refresh"
                : root.view === "rules" ? "←/→ switch    ↵ on/off    a new rule    x delete"
                : "←/→ switch    ↵ open Questflow    r refresh"
              font.family: root.fontFamily
              font.pixelSize: Style.space(9)
              color: root.muted
              opacity: 0.8
            }

            Text {
              textFormat: Text.PlainText
              visible: root.personalView && root.service.connected
              text: "Sign out"
              font.family: root.fontFamily
              font.pixelSize: Style.space(9)
              color: disconnectHover.hovered ? root.text : root.muted
              HoverHandler { id: disconnectHover }
              TapHandler { onTapped: root.service.disconnectQuestflow() }
            }
          }
        }
      }
    }
  }
}
