import AppKit
import Observation
import SwiftUI

/// Where the window's traffic lights sit, so the top bar (wordmark, the
/// Visual | Chat control, the presence HUD) shares their centerline.
@MainActor @Observable
final class ShellChrome {
    static let shared = ShellChrome()

    /// Vertical center of the traffic lights, in content (top-left) points.
    private(set) var lightsMidY: CGFloat = 26
    /// Right edge of the zoom button, in content points.
    private(set) var lightsMaxX: CGFloat = 76
    private(set) var fullScreen = false
    /// The presence HUD's footprint with its activity rows open (as on the
    /// Visual tab), in content points: the board starts below it wherever its
    /// top row would run under it.
    var hudBottom: CGFloat = 0
    var hudWidth: CGFloat = 0

    func measure(_ window: NSWindow) {
        guard let close = window.standardWindowButton(.closeButton),
              let zoom = window.standardWindowButton(.zoomButton),
              let content = window.contentView,
              let closeSuper = close.superview, let zoomSuper = zoom.superview else { return }
        let c = content.convert(closeSuper.convert(close.frame, to: nil), from: nil)
        let z = content.convert(zoomSuper.convert(zoom.frame, to: nil), from: nil)
        let mid = content.isFlipped ? c.midY : content.bounds.height - c.midY
        let fs = window.styleMask.contains(.fullScreen)
        let y = pixel(fs ? 26 : mid)
        let x = pixel(fs ? 0 : z.maxX)
        if lightsMidY != y, y > 4, y < 80 { lightsMidY = y }
        if lightsMaxX != x { lightsMaxX = x }
        if fullScreen != fs { fullScreen = fs }
    }
}

/// A press that squashes on a spring and lands back with a little give.
struct ShellPressStyle: ButtonStyle {
    var scale: CGFloat = 0.96
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .scaleEffect(configuration.isPressed ? scale : 1)
            .animation(.spring(response: 0.24, dampingFraction: 0.58), value: configuration.isPressed)
    }
}

/// The top strip behaves like a titlebar: drag moves the window, a double
/// click does what the user picked in System Settings (zoom or minimize).
struct ShellDragArea: NSViewRepresentable {
    final class DragView: NSView {
        override var mouseDownCanMoveWindow: Bool { true }
        override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }
        override func mouseDown(with event: NSEvent) {
            guard let window else { return }
            if event.clickCount == 2 {
                let action = UserDefaults.standard.string(forKey: "AppleActionOnDoubleClick") ?? "Maximize"
                switch action {
                case "Minimize": window.performMiniaturize(nil)
                case "None": break
                default: window.performZoom(nil)
                }
            } else {
                window.performDrag(with: event)
            }
        }
    }

    func makeNSView(context: Context) -> DragView { DragView() }
    func updateNSView(_ v: DragView, context: Context) {}
}
