// pugrec countdown overlay (Quickshell): dims the screen, a spinner while the recorder
// starts ("Starting camera…"), then 3-2-1 in a circle.
//   click the circle / Pause / Space: pause or resume      Cancel / Esc: discard the recording
// Started by the recorder right after the password prompt; it spins until the recorder
// writes "go" to PUGREC_READY_FILE (screen + camera running), then counts. Exit code:
// 0 = go, 3 = cancel (console.log doesn't reach stdout).
// Env: PUGREC_COUNT (s), PUGREC_MONITOR, PUGREC_READY_FILE, PUGREC_WAITING (spinner text).
//
// Safety (James was once locked out by an exclusive-focus overlay): it has the keyboard
// only while it's up (Esc closes it), never the mouse (only the circle and buttons take
// clicks), starts the recording by itself after 2 minutes, and the recorder kills it at
// 2:10 whatever state it's in.
import QtQuick
import Quickshell
import Quickshell.Io        // FileView (the ready file); without it the overlay doesn't load at all
import Quickshell.Wayland

ShellRoot {
    id: root
    readonly property int total: parseInt(Quickshell.env("PUGREC_COUNT") || "3")
    readonly property string monitor: Quickshell.env("PUGREC_MONITOR") || ""
    readonly property int maxLifetimeMs: 2 * 60 * 1000   // then just start: never hold the keyboard long
    property real left: total          // seconds left, fractional
    property bool paused: false
    // PUGREC_MODE=stopping: just a spinner + "Saving…" while the recorder finalises the files -
    // no keyboard, no clicks (fully click-through), no dim; the recorder closes it.
    readonly property bool stopping: Quickshell.env("PUGREC_MODE") === "stopping"
    property bool ready: !stopping && !Quickshell.env("PUGREC_READY_FILE")   // no file: count at once
    property real spin: 0              // spinner angle, radians
    readonly property string waiting: Quickshell.env("PUGREC_WAITING") || "Getting ready…"
    property bool done: false

    readonly property color ink: "#16171a"
    readonly property color cream: "#f1e4d9"
    readonly property color orange: "#ff6b2c"

    function finish(result) {
        if (done) return
        done = true
        Qt.exit(result === "go" ? 0 : 3)
    }

    FileView {   // the recorder writes "go" here once the screen and camera are running
        id: readyFile
        path: Quickshell.env("PUGREC_READY_FILE") || ""
        onLoaded: if (text().trim() === "go") root.ready = true
    }
    Timer {   // poll it (a few seconds at most); reload() is cheap and needs no inotify
        interval: 100; repeat: true; running: !root.ready && !root.done
        onTriggered: readyFile.reload()
    }
    Timer {   // the spinner
        interval: 16; repeat: true; running: !root.ready && !root.done
        onTriggered: { root.spin = (root.spin + 0.12) % (2 * Math.PI); ring.requestPaint() }
    }

    Timer {   // smooth: 50 ms steps, so the ring sweeps instead of jumping
        interval: 50; repeat: true; running: root.ready && !root.paused && !root.done
        onTriggered: {
            root.left = Math.max(0, root.left - 0.05)
            ring.requestPaint()
            if (root.left <= 0.001) root.finish("go")
        }
    }
    Timer { interval: root.maxLifetimeMs; running: true; onTriggered: root.finish("go") }

    PanelWindow {
        id: win
        screen: Quickshell.screens.find(s => s.name === root.monitor) ?? Quickshell.screens[0]
        anchors { top: true; bottom: true; left: true; right: true }
        exclusionMode: ExclusionMode.Ignore
        WlrLayershell.layer: WlrLayer.Overlay
        // Exclusive, so Esc/Space work at once (OnDemand only focuses after a click). An exclusive
        // overlay once locked James out, so the escape hatches are: Esc, the mouse is never grabbed
        // (Cancel always works), a 2-minute self limit, and the recorder kills it at 2:10 regardless.
        WlrLayershell.keyboardFocus: root.stopping ? WlrKeyboardFocus.None : WlrKeyboardFocus.Exclusive
        WlrLayershell.namespace: "pugrec-countdown"
        color: "transparent"
        mask: root.stopping ? noInput : controlsOnly
        Region { id: controlsOnly; item: controls }   // input only on the controls
        Region { id: noInput }                          // stopping: no input at all

        Rectangle {   // the dim: alpha in the colour (an `opacity` here didn't render)
            x: 0; y: 0; width: win.width; height: win.height
            color: Qt.rgba(0.086, 0.09, 0.102, root.stopping ? 0.25 : 0.6)
        }

        Item {   // keys: Esc = cancel, Space = pause/resume
            anchors.fill: parent
            focus: true
            Keys.onEscapePressed: if (!root.stopping) root.finish("cancel")
            Keys.onSpacePressed: if (root.ready) root.paused = !root.paused
        }

        Column {
            id: controls
            anchors.centerIn: parent
            spacing: 28

            Item {
                id: circle
                width: 300; height: 300
                anchors.horizontalCenter: parent.horizontalCenter

                Rectangle {
                    anchors.fill: parent; radius: width / 2
                    color: root.ink; opacity: 0.92
                    border.color: Qt.rgba(1, 1, 1, 0.08); border.width: 2
                }
                Canvas {   // the progress ring for the current second
                    id: ring
                    anchors.fill: parent
                    onPaint: {
                        const ctx = getContext("2d")
                        ctx.reset()
                        ctx.lineWidth = 12
                        ctx.lineCap = "round"
                        ctx.strokeStyle = root.orange
                        ctx.beginPath()
                        if (!root.ready) {   // spinner: a quarter arc going round
                            ctx.arc(width / 2, height / 2, width / 2 - 14, root.spin, root.spin + Math.PI / 2, false)
                        } else {
                            const frac = root.left - Math.floor(root.left - 1e-6)   // 1 -> 0 within a second
                            ctx.arc(width / 2, height / 2, width / 2 - 14, -Math.PI / 2,
                                    -Math.PI / 2 + 2 * Math.PI * frac, false)
                        }
                        ctx.stroke()
                    }
                }
                Text {
                    anchors.centerIn: parent
                    visible: root.ready
                    text: root.paused ? "❚❚" : Math.max(1, Math.ceil(root.left - 1e-6))
                    color: root.cream
                    font.family: "Archivo"; font.pixelSize: root.paused ? 96 : 150; font.weight: Font.Bold
                }
                MouseArea {
                    anchors.fill: parent
                    cursorShape: root.ready ? Qt.PointingHandCursor : Qt.ArrowCursor
                    onClicked: if (root.ready) root.paused = !root.paused
                }
            }

            Text {
                anchors.horizontalCenter: parent.horizontalCenter
                text: !root.ready ? root.waiting
                    : root.paused ? "Paused - click the circle to continue" : "Recording starts"
                color: root.cream; opacity: 0.85
                font.family: "IBM Plex Sans"; font.pixelSize: 22
            }

            Row {
                anchors.horizontalCenter: parent.horizontalCenter
                spacing: 14
                Repeater {
                    model: root.stopping ? []
                        : root.ready
                        ? [{ label: root.paused ? "Resume" : "Pause", act: "pause" }, { label: "Cancel", act: "cancel" }]
                        : [{ label: "Cancel", act: "cancel" }]
                    delegate: Rectangle {
                        required property var modelData
                        width: 150; height: 48; radius: 6
                        color: modelData.act === "cancel" ? "transparent" : root.ink
                        border.color: modelData.act === "cancel" ? root.cream : root.orange
                        border.width: 2
                        Text {
                            anchors.centerIn: parent
                            text: modelData.label; color: root.cream
                            font.family: "IBM Plex Sans"; font.pixelSize: 19; font.weight: Font.DemiBold
                        }
                        MouseArea {
                            anchors.fill: parent
                            cursorShape: Qt.PointingHandCursor
                            onClicked: modelData.act === "cancel" ? root.finish("cancel") : (root.paused = !root.paused)
                        }
                    }
                }
            }
        }
    }
}
