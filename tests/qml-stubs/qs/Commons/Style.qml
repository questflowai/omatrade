pragma Singleton
import QtQuick
QtObject {
  property QtObject font: QtObject { property string family: "monospace" }
  function space(n: real): real { return n }
}
