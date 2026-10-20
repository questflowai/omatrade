pragma Singleton
import QtQml
QtObject {
  function env(name: string): var { return "" }
  function execDetached(command: var): void {}
}
