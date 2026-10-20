import QtQuick
Item {
  property Item anchorItem: null
  property Item owner: null
  property var bar: null
  property bool open: false
  property Item focusTarget: null
  property real contentWidth: 0
  property real contentHeight: 0
  default property alias content: holder.data
  Item { id: holder }
  function fittedContentWidth(w: real): real { return w }
  function fittedContentHeight(h: real, max: real): real { return h }
}
