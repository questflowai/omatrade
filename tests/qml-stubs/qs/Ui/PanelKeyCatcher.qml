import QtQuick
FocusScope {
  property bool blocked: false
  signal moveRequested(int dx, int dy)
  signal activateRequested()
  signal deleteRequested()
  signal closeRequested()
  signal tabRequested(int direction)
  signal textKey(string text)
}
