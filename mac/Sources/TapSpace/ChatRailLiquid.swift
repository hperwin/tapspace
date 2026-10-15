import AppKit
import QuartzCore
import SwiftUI

/// The rail's liquid selection body, drawn by Core Animation instead of a
/// SwiftUI Canvas.
///
/// A Canvas behind the rail is as tall as the rail and is re-rasterized on the
/// main thread (RBLayer display, a fresh surface) on every spring frame. Here
/// the body is a CAShapeLayer whose path the spring updates: the render server
/// fills it and draws the two soft shadows from `shadowPath`, so a frame costs
/// one path assignment and no backing store at all.
struct ChatRailLiquid: View {
    let track: LiquidTrack

    var body: some View {
        // Reading the path here (not in the representable) keeps the per-frame
        // invalidation to this one tiny view.
        let path = track.path
        let _ = ChatHarness.railTiming ? ChatHarness.railFrame(path.boundingRect) : ()
        ChatShapeLayerView(path: path.cgPath)
            .allowsHitTesting(false)
    }
}

private struct ChatShapeLayerView: NSViewRepresentable {
    let path: CGPath

    final class Coordinator {
        let ambient = CAShapeLayer()   // the wide, soft drop shadow
        let contact = CAShapeLayer()   // the tight contact shadow
        let body = CAShapeLayer()      // white fill + hairline
    }

    func makeCoordinator() -> Coordinator { Coordinator() }

    func makeNSView(context: Context) -> LayerHost {
        let v = LayerHost()
        let c = context.coordinator
        // Matches LiquidCanvas: shadow(.07, r 8, y 4) + shadow(.06, r 1.2, y .6), white fill, .035 stroke.
        for (l, opacity, radius, y) in [(c.ambient, Float(0.07), CGFloat(8), CGFloat(4)),
                                         (c.contact, Float(0.06), CGFloat(1.2), CGFloat(0.6))] {
            l.fillColor = NSColor.white.cgColor
            l.shadowColor = NSColor.black.cgColor
            l.shadowOpacity = opacity
            l.shadowRadius = radius
            l.shadowOffset = CGSize(width: 0, height: y)   // the host is flipped: +y is down
            v.layer?.addSublayer(l)
        }
        c.body.fillColor = NSColor.white.cgColor
        c.body.strokeColor = NSColor.black.withAlphaComponent(0.035).cgColor
        c.body.lineWidth = 0.5
        v.layer?.addSublayer(c.body)
        v.onLayout = { host in
            for l in [c.ambient, c.contact, c.body] { l.frame = host.bounds }
        }
        apply(c)
        return v
    }

    func updateNSView(_ v: LayerHost, context: Context) { apply(context.coordinator) }

    private func apply(_ c: Coordinator) {
        CATransaction.begin()
        CATransaction.setDisableActions(true)
        c.ambient.path = path
        c.ambient.shadowPath = path
        c.contact.path = path
        c.contact.shadowPath = path
        c.body.path = path
        CATransaction.commit()
    }
}

/// Runs work on the next display-link frame of the main window: after the
/// current run-loop turn has laid out and committed. The rail's liquid starts
/// here when the thread changes, so its first spring frame is never the long
/// frame that builds the newly opened thread.
@MainActor
final class ChatNextFrame: MotionDriver {
    private var work: (() -> Void)?

    private init(_ work: @escaping () -> Void) { self.work = work }

    static func run(_ work: @escaping () -> Void) {
        Motion.main.activate(ChatNextFrame(work))
    }

    func advance(_ dt: Double) -> Bool {
        let w = work
        work = nil
        w?()
        return false
    }

    func finish() { _ = advance(0) }
}
