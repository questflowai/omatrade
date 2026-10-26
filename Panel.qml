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
// Three lists, in the order you reach for them: what you watch on
// TradingView, the trading news behind it, and what you hold across the
// accounts connected to Questflow.
Panel {
  id: root
  moduleName: "questflow.omatrade"
  ipcTarget: "questflow.omatrade"
  // Its own handler, so the CLI can push a refresh the moment the watchlist
  // changes and a keybinding can open the panel on any list:
  //   omarchy-shell questflow.omatrade watchlist|signals|positions|add|toggle
  manageIpc: false

  readonly property var views: [
    { key: "watchlist", label: "Watchlist" },
    { key: "signals", label: "Signals" },
    { key: "positions", label: "Positions" }
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

  // Reordering the watchlist by dragging a row's grip: the row being dragged,
  // and the row index the drop line sits above (watchRows.length is the end).
  property int dragFrom: -1
  property int dropIndex: -1

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  readonly property bool gone: service.missing
  visible: !gone

  QtObject {
    id: dummyService
    property string view: "watchlist"
    property bool pendingAdd: false
    property var watchlist: null
    property var news: null
    property var questflow: ({ connected: false })
    property bool connected: false
    property var totals: null
    property var accounts: []
    property var colors: null
    property bool mock: false
    property var searchResults: []
    property string searchError: ""
    property bool searching: false
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
    function connectQuestflow() {}
    function disconnectQuestflow() {}
    function openTradingView() {}
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
  readonly property var newsRows: Model.newsRows(service.news)
  readonly property var positionRows: Model.positionRows(service.accounts)
  readonly property var rows: view === "signals" ? newsRows
    : (view === "positions" ? positionRows : watchRows)
  readonly property var totals: service.totals

  readonly property color foreground: bar ? bar.foreground : Color.foreground
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family

  readonly property var palette: Model.palette(service.colors, Color.popups.text, Color.popups.background)
  readonly property color text: palette.text
  readonly property color muted: palette.muted
  readonly property color hover: palette.hover

  // The first quote on the watchlist: the tooltip, and the number the bar
  // wears so the top symbol's price is on screen without opening the panel.
  readonly property var headline: {
    for (var i = 0; i < watchRows.length; i++)
      if (!watchRows[i].header && watchRows[i].hasQuote) return watchRows[i]
    return null
  }
  // Vertical bars have no room for a label; until the first quote arrives
  // the bar keeps the icon alone.
  readonly property bool showPrice: headline !== null && !root.vertical
  readonly property string priceLabel: headline
    ? headline.display + "  " + Model.formatPrice(headline.last) : ""

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

  function showView(name) {
    if (view === name) return
    stopAdding()
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
  }

  function activate(row) {
    if (view === "watchlist") {
      if (row && !row.header) service.openTradingView(row.symbol)
      else return
    } else if (view === "signals") {
      if (row && row.url) service.openUrl(row.url)
      else return
    } else {
      if (!service.connected) { service.connectQuestflow(); return }
      service.openQuestflow()
    }
    close()
  }

  function removeSelected() {
    var row = rows[selectedIndex]
    if (view !== "watchlist" || !row || row.header) return
    service.removeSymbol(row.symbol)
    // Stay on the row below, which is now at this index.
    Qt.callLater(function () {
      var picks = Model.selectableIndexes(root.rows)
      if (!picks.length) root.selectedIndex = -1
      else if (picks.indexOf(root.selectedIndex) < 0) root.selectedIndex = picks[picks.length - 1]
    })
  }

  function selectSymbol(symbol) {
    for (var i = 0; i < watchRows.length; i++)
      if (!watchRows[i].header && watchRows[i].symbol === symbol) { selectedIndex = i; return }
  }

  // Turn "drop above row `to`" into a move the CLI understands: next to the
  // nearest symbol, or to the top of a section when the drop lands under its
  // heading with nothing below.
  function moveRow(from, to) {
    var rows = watchRows
    var row = rows[from]
    if (!row || row.header || to === from || to === from + 1) return
    var next = null, prev = null
    for (var i = to; i < rows.length; i++) if (i !== from) { next = rows[i]; break }
    for (var j = to - 1; j >= 0; j--) if (j !== from) { prev = rows[j]; break }
    if (next && !next.header) service.moveSymbol(row.symbol, next.symbol, false)
    else if (prev && !prev.header) service.moveSymbol(row.symbol, prev.symbol, true)
    else if (prev && prev.header) service.moveSymbol(row.symbol, "", false, prev.name)
    else if (next && next.header) service.moveSymbol(row.symbol, "", false, next.name)
    else return
    Qt.callLater(function () { root.selectSymbol(row.symbol) })
  }

  // One place up or down; across a heading it lands at the edge of the
  // neighbouring section rather than jumping past its first symbol.
  function stepSymbol(delta) {
    if (view !== "watchlist" || adding) return
    var rows = watchRows
    var row = rows[selectedIndex]
    if (!row || row.header) return
    var crossed = false
    for (var k = selectedIndex + delta; k >= 0 && k < rows.length; k += delta) {
      if (rows[k].header) { crossed = true; continue }
      var after = delta > 0 ? !crossed : crossed
      service.moveSymbol(row.symbol, rows[k].symbol, after)
      Qt.callLater(function () { root.selectSymbol(row.symbol) })
      return
    }
  }

  function dragStart(i) {
    dragFrom = i
    dropIndex = i
    selectedIndex = i
  }

  function dragMove(y) {
    var at = watchRows.length
    for (var i = 0; i < watchRows.length; i++) {
      var item = watchRepeater.itemAt(i)
      if (item && y < item.y + item.height / 2) { at = i; break }
    }
    dropIndex = at
  }

  function dragEnd() {
    var from = dragFrom, to = dropIndex
    dragFrom = -1
    dropIndex = -1
    if (from >= 0 && to >= 0) moveRow(from, to)
  }

  function startAdding() {
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
    function add(): void { root.openOn("watchlist", true) }
  }

  Timer {
    id: searchDebounce
    interval: 250
    onTriggered: root.service.search(searchField.text)
  }

  WidgetButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    labelVisible: false
    hasVisualContent: true
    fixedWidth: root.vertical ? -1 : (root.showPrice
      ? Style.bar.iconSlot + Style.space(6) + priceText.implicitWidth
      : Style.bar.iconSlot)
    fixedHeight: root.vertical ? Style.bar.iconSlot : -1

    Row {
      anchors.centerIn: parent
      spacing: Style.space(6)
      OmatradeIcon {
        anchors.verticalCenter: parent.verticalCenter
        iconSize: Style.space(13)
        color: root.foreground
      }
      Text {
        id: priceText
        anchors.verticalCenter: parent.verticalCenter
        textFormat: Text.PlainText
        visible: root.showPrice
        text: root.priceLabel
        font.family: root.fontFamily
        font.pixelSize: Style.font.body
        color: root.foreground
      }
    }

    tooltipText: root.headline
      ? root.headline.display + "  " + Model.formatPrice(root.headline.last)
        + "  " + Model.formatPercent(root.headline.changePct)
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
    contentWidth: panel.fittedContentWidth(Style.space(390))
    contentHeight: panel.fittedContentHeight(
      Math.min(top.implicitHeight + foot.implicitHeight + body.implicitHeight + Style.space(40),
        Style.space(660)),
      Style.space(660))

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      // While adding, every key is the search field's: the catcher would
      // otherwise take Enter as "open the selected row" and j/k/h/l/x as
      // navigation, before the field ever saw them.
      blocked: root.adding
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
        else if (text === "a" || text === "/" || text === "+") root.startAdding()
        else if (text === "J") root.stepSymbol(1)
        else if (text === "K") root.stepSymbol(-1)
      }

      ColumnLayout {
        anchors.fill: parent
        spacing: Style.space(10)

        // ── Title and tabs ───────────────────────────────────────────────
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

            // Positions are demo data until Questflow OAuth ships, and the
            // panel says so wherever they show. Quotes and news are real.
            Rectangle {
              visible: root.view === "positions" && root.service.mock
              implicitWidth: demoLabel.implicitWidth + Style.space(10)
              implicitHeight: Style.space(18)
              radius: Style.space(4)
              color: root.palette.tags.flat.fill
              Text {
                textFormat: Text.PlainText
                id: demoLabel
                anchors.centerIn: parent
                text: "DEMO DATA"
                font.family: root.fontFamily
                font.pixelSize: Style.space(9)
                font.bold: true
                font.letterSpacing: 0.6
                color: root.palette.tags.flat.label
              }
            }

            Item {
              id: spinner
              visible: root.service.refreshing || root.service.searching
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

            // Add to the watchlist: the one action this list has besides
            // reading it.
            Rectangle {
              visible: root.view === "watchlist" && !root.adding
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
                  text: modelData.label
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

          // Search, in place of the list, while adding.
          RowLayout {
            Layout.fillWidth: true
            visible: root.adding
            spacing: Style.space(8)

            TextField {
              id: searchField
              Layout.fillWidth: true
              placeholderText: "Search TradingView — NVDA, BTC, Tencent…"
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
            text: root.service.searchError || "Nothing on TradingView matches that."
            wrapMode: Text.WordWrap
            font.family: root.fontFamily
            font.pixelSize: Style.space(11)
            color: root.muted
          }

          // Positions, before connecting: what you get, and the one button.
          ColumnLayout {
            Layout.fillWidth: true
            visible: root.view === "positions" && !root.service.connected
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
              text: "Connect your Questflow account"
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
              text: "Positions from your brokers, Hyperliquid and Polymarket in one place. Read-only: Omatrade never trades."
              font.family: root.fontFamily
              font.pixelSize: Style.space(11)
              color: root.muted
            }
            Rectangle {
              Layout.alignment: Qt.AlignHCenter
              implicitWidth: connectLabel.implicitWidth + Style.space(28)
              implicitHeight: Style.space(32)
              radius: Style.space(7)
              color: root.palette.tags.accent.fill
              opacity: connectHover.hovered ? 0.88 : 1
              HoverHandler { id: connectHover }
              TapHandler { onTapped: root.service.connectQuestflow() }
              Text {
                textFormat: Text.PlainText
                id: connectLabel
                anchors.centerIn: parent
                text: "Connect Questflow"
                font.family: root.fontFamily
                font.pixelSize: Style.space(12)
                font.bold: true
                color: root.palette.tags.accent.label
              }
            }
            Text {
              textFormat: Text.PlainText
              Layout.fillWidth: true
              horizontalAlignment: Text.AlignHCenter
              wrapMode: Text.WordWrap
              text: "Prototype: connects demo accounts until Questflow sign-in ships."
              font.family: root.fontFamily
              font.pixelSize: Style.space(9)
              color: root.muted
              opacity: 0.8
            }
            Item { implicitHeight: Style.space(8) }
          }

          // Positions, connected: everything you hold, and today.
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
                  textFormat: Text.PlainText
                  id: dayLabel
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
              Layout.fillWidth: true
              elide: Text.ElideRight
              textFormat: Text.StyledText
              text: root.totals
                ? "Open P&amp;L <font color='" + root.colorFor(root.totals.openPnl) + "'>"
                  + Model.formatMoney(root.totals.openPnl, true) + "</font>"
                  + "  ·  " + root.totals.positions + " positions  ·  "
                  + root.service.accounts.length + " accounts"
                : ""
              font.family: root.fontFamily
              font.pixelSize: Style.space(11)
              color: root.muted
            }
          }

          Text {
            textFormat: Text.PlainText
            Layout.fillWidth: true
            visible: !root.adding && root.rows.length === 0 && root.service.lastError === ""
              && root.view !== "positions"
            text: root.view === "signals"
              ? "No news yet for the watchlist."
              : "Watchlist is empty. Press + to add a symbol."
            font.family: root.fontFamily
            font.pixelSize: Style.space(12)
            color: root.muted
          }

          ScrollView {
            id: listScroll
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true

            // A drag is a reorder, not a scroll.
            Binding {
              target: listScroll.contentItem
              property: "interactive"
              value: false
              when: root.dragFrom >= 0
            }
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
              // is a chart on TradingView; ⠿ drags it into place; × takes it
              // off the list.
              Repeater {
                id: watchRepeater
                model: root.view === "watchlist" && !root.adding ? root.watchRows : []

                delegate: Item {
                  id: watchRow
                  readonly property bool dragged: root.dragFrom === index
                  readonly property bool lit: dragged || (root.dragFrom < 0
                    && (index === root.selectedIndex || watchHover.hovered))
                  Layout.fillWidth: true
                  implicitHeight: modelData && modelData.header ? Style.space(26) : Style.space(40)
                  opacity: dragged ? 0.5 : 1

                  // Where the dragged row will land: above this row, or below
                  // the last one.
                  Rectangle {
                    visible: root.dragFrom >= 0 && (root.dropIndex === index
                      || (root.dropIndex === root.watchRows.length && index === root.watchRows.length - 1))
                    anchors.left: parent.left
                    anchors.right: parent.right
                    y: root.dropIndex === index ? -Style.space(3) : parent.height + Style.space(1)
                    height: Style.space(2)
                    radius: height / 2
                    color: root.palette.accent
                    z: 2
                  }

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
                    spacing: Style.space(6)

                    // The grip: the only part of the row that drags, so a
                    // click stays a click and the list still scrolls.
                    Item {
                      id: grip
                      Layout.fillHeight: true
                      implicitWidth: Style.space(10)
                      Text {
                        textFormat: Text.PlainText
                        anchors.centerIn: parent
                        visible: watchRow.lit || gripHover.hovered
                        text: "⠿"
                        font.pixelSize: Style.space(12)
                        color: gripHover.hovered || watchRow.dragged ? root.text : root.muted
                      }
                      HoverHandler {
                        id: gripHover
                        cursorShape: watchRow.dragged ? Qt.ClosedHandCursor : Qt.OpenHandCursor
                      }
                      DragHandler {
                        id: gripDrag
                        target: null
                        xAxis.enabled: false
                        grabPermissions: PointerHandler.CanTakeOverFromAnything
                        onActiveChanged: active ? root.dragStart(index) : root.dragEnd()
                        onCentroidChanged: if (active)
                          root.dragMove(grip.mapToItem(listColumn, 0, centroid.position.y).y)
                      }
                    }

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
                        visible: watchRow.lit
                      }
                      Text {
                        textFormat: Text.PlainText
                        anchors.centerIn: parent
                        visible: watchRow.lit
                        text: "×"
                        font.family: root.fontFamily
                        font.pixelSize: Style.space(14)
                        font.bold: true
                        color: removeHover.hovered ? root.palette.tags.down.label : root.muted
                      }
                      HoverHandler { id: removeHover }
                      TapHandler {
                        onTapped: {
                          root.selectedIndex = index
                          root.removeSelected()
                        }
                      }
                    }
                  }
                }
              }

              // ── Signals: trading news for the watchlist ────────────────
              Repeater {
                model: root.view === "signals" ? root.newsRows : []

                delegate: Item {
                  Layout.fillWidth: true
                  implicitHeight: newsColumn.implicitHeight + Style.space(12)

                  Rectangle {
                    anchors.fill: parent
                    anchors.leftMargin: -Style.space(6)
                    anchors.rightMargin: -Style.space(6)
                    radius: Style.space(6)
                    color: root.hover
                    opacity: index === root.selectedIndex || newsHover.hovered ? 1 : 0
                    Behavior on opacity { NumberAnimation { duration: 90 } }
                  }

                  HoverHandler {
                    id: newsHover
                    onHoveredChanged: if (hovered) root.selectedIndex = index
                  }
                  TapHandler { onTapped: root.activate(modelData) }

                  ColumnLayout {
                    id: newsColumn
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.verticalCenter: parent.verticalCenter
                    spacing: Style.space(3)

                    RowLayout {
                      Layout.fillWidth: true
                      spacing: Style.space(6)

                      // Breaking is the only thing loud enough for a fill.
                      Rectangle {
                        visible: !!modelData && modelData.urgency <= 1
                        implicitWidth: breakingLabel.implicitWidth + Style.space(8)
                        implicitHeight: Style.space(15)
                        radius: Style.space(3)
                        color: root.palette.tags.down.fill
                        Text {
                          textFormat: Text.PlainText
                          id: breakingLabel
                          anchors.centerIn: parent
                          text: "BREAKING"
                          font.family: root.fontFamily
                          font.pixelSize: Style.space(8)
                          font.bold: true
                          color: root.palette.tags.down.label
                        }
                      }
                      Text {
                        textFormat: Text.PlainText
                        text: modelData && modelData.symbols ? modelData.symbols.slice(0, 3).join("  ") : ""
                        font.family: root.fontFamily
                        font.pixelSize: Style.space(10)
                        font.bold: true
                        color: root.palette.accent
                      }
                      Item { Layout.fillWidth: true }
                      Text {
                        textFormat: Text.PlainText
                        text: modelData ? Model.ago(modelData.published, root.now) : ""
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
                      font.bold: true
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
                      text: modelData && modelData.header ? modelData.label : ""
                      font.family: root.fontFamily
                      font.pixelSize: Style.space(10)
                      color: root.muted
                      opacity: 0.8
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
                      Text {
                        textFormat: Text.PlainText
                        Layout.fillWidth: true
                        text: modelData && modelData.subtitle ? modelData.subtitle : ""
                        elide: Text.ElideRight
                        font.family: root.fontFamily
                        font.pixelSize: Style.space(10)
                        color: root.muted
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
                        textFormat: Text.PlainText
                        id: pctLabel
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
              && (root.service.watchlist.source === "starter" || !!root.service.watchlist.error)
            text: root.service.watchlist && root.service.watchlist.error
              ? root.service.watchlist.error
              : "Starter list. Press + to add symbols, or bring a TradingView export: omatrade tv import"
            wrapMode: Text.WordWrap
            font.family: root.fontFamily
            font.pixelSize: Style.space(10)
            color: root.muted
          }

          Text {
            textFormat: Text.PlainText
            Layout.fillWidth: true
            visible: root.view === "signals" && !!root.service.news && !!root.service.news.error
            text: root.service.news ? root.service.news.error : ""
            wrapMode: Text.WordWrap
            font.family: root.fontFamily
            font.pixelSize: Style.space(10)
            color: root.muted
          }

          RowLayout {
            Layout.fillWidth: true

            Text {
              textFormat: Text.PlainText
              Layout.fillWidth: true
              horizontalAlignment: Text.AlignHCenter
              text: root.adding ? "↑/↓ choose    ↵ add    esc cancel"
                : root.view === "watchlist" ? "←/→ switch   ↵ chart   a add   x remove   ⇧J/K move"
                : root.view === "signals" ? "←/→ switch    ↵ read on TradingView    r refresh"
                : root.service.connected ? "←/→ switch    ↵ open Questflow    r refresh"
                : "←/→ switch    ↵ connect Questflow"
              font.family: root.fontFamily
              font.pixelSize: Style.space(9)
              color: root.muted
              opacity: 0.8
            }

            Text {
              textFormat: Text.PlainText
              visible: root.view === "positions" && root.service.connected
              text: "Disconnect"
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
