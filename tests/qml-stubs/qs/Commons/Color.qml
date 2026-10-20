pragma Singleton
import QtQuick
QtObject {
  property color foreground: "white"
  property QtObject popups: QtObject { property color text: "white"; property color background: "black" }
  signal themeShellValuesChanged()
}
