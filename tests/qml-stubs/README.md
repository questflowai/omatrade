Minimal type stubs for the Quickshell and Omarchy shell modules the plugin
imports, so `qmllint` can check names and properties offline. They mirror
only what this plugin uses and are never loaded at runtime.

    /usr/lib/qt6/bin/qmllint -I tests/qml-stubs Panel.qml Service.qml OmatradeIcon.qml Sparkline.qml
