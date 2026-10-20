import QtQml
QtObject {
  property bool running: false
  property var command: []
  property QtObject stdout: null
  property QtObject stderr: null
  signal started()
  signal exited(int code)
}
