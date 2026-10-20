import QtQuick
Item {
  property var bar: null
  property string moduleName: ""
  property string ipcTarget: ""
  property bool manageIpc: true
  property bool opened: false
  function open(): void {}
  function close(): void {}
  function toggle(): void {}
  function switchPanel(direction: int): void {}
}
