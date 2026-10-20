import QtQuick
Item {
  property var bar: null
  property Component iconComponent: null
  property string tooltipText: ""
  signal pressed(int buttonCode)
}
