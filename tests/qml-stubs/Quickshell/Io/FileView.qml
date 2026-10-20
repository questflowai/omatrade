import QtQml
QtObject {
  property string path: ""
  property bool watchChanges: false
  signal fileChanged()
  signal loaded()
  signal loadFailed(var error)
  function reload(): void {}
  function text(): string { return "" }
}
