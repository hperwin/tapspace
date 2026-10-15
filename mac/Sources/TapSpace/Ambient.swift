import AppKit
import QuartzCore
import SwiftUI

// Ambient loops (spinning arcs, breathing dots, the thinking orb, typing
// dots) run as Core Animation on the render server: zero main-thread cost,
// so the springs' display link can sleep while these keep moving.

enum Palette {
    static let ink = NSColor(srgbRed: 0x1d / 255, green: 0x1d / 255, blue: 0x1f / 255, alpha: 1)
    static let dim = NSColor(srgbRed: 0x86 / 255, green: 0x86 / 255, blue: 0x8b / 255, alpha: 1)
    static let faint = NSColor(srgbRed: 0xc7 / 255, green: 0xc7 / 255, blue: 0xcc / 255, alpha: 1)
    static let green = NSColor(srgbRed: 0x30 / 255, green: 0xa1 / 255, blue: 0x4e / 255, alpha: 1)
    static let blue = NSColor(srgbRed: 0, green: 122 / 255, blue: 1, alpha: 1)
}

extension Color {
    static let ink = Color(nsColor: Palette.ink)
    static let dim = Color(nsColor: Palette.dim)
    static let faint = Color(nsColor: Palette.faint)
    static let up = Color(nsColor: Palette.green)
    static let tapBlue = Color(nsColor: Palette.blue)
    static let well = Color(red: 0xf2 / 255, green: 0xf2 / 255, blue: 0xf3 / 255)
    static let bubbleGrey = Color(red: 0xf2 / 255, green: 0xf2 / 255, blue: 0xf4 / 255)
    static let hairline = Color.black.opacity(0.055)
}

extension View {
    /// The web page's three-layer card shadow.
    func softShadow(_ k: Double = 1) -> some View {
        shadow(color: .black.opacity(0.04 * k), radius: 1, x: 0, y: 1)
            .shadow(color: .black.opacity(0.07 * k), radius: 14, x: 0, y: 8)
            .shadow(color: .black.opacity(0.05 * k), radius: 32, x: 0, y: 24)
    }
}

/// Flipped, layer-hosting view that never takes clicks.
final class LayerHost: NSView {
    var onLayout: ((LayerHost) -> Void)?
    override var isFlipped: Bool { true }
    override init(frame: NSRect) {
        super.init(frame: frame)
        wantsLayer = true
        layer?.masksToBounds = false
    }
    required init?(coder: NSCoder) { fatalError() }
    override func layout() {
        super.layout()
        CATransaction.begin()
        CATransaction.setDisableActions(true)
        onLayout?(self)
        CATransaction.commit()
    }
    override func hitTest(_ point: NSPoint) -> NSView? { nil }
}

private func loop(_ key: String, from: Any, to: Any, duration: Double, autoreverses: Bool = false,
                  timing: CAMediaTimingFunctionName = .easeInEaseOut, offset: Double = 0) -> CABasicAnimation {
    let a = CABasicAnimation(keyPath: key)
    a.fromValue = from
    a.toValue = to
    a.duration = duration
    a.autoreverses = autoreverses
    a.repeatCount = .infinity
    a.timingFunction = CAMediaTimingFunction(name: timing)
    a.isRemovedOnCompletion = false
    if offset != 0 {
        a.beginTime = CACurrentMediaTime() + offset
        a.fillMode = .backwards
    }
    return a
}

// MARK: - Ring (presence rows, tap avatars)

enum RingStyle: Equatable {
    /// A drawing arc that chases itself while the ring turns (building).
    case chase(NSColor)
    /// A short arc spinning (a tap in flight).
    case spin(NSColor, length: CGFloat, period: Double)
    /// A dotted ring turning slowly (boarding).
    case dotted(NSColor, period: Double)
    /// A full ring breathing (your move).
    case breathe(NSColor, period: Double)
    /// A full, still ring.
    case solid(NSColor)
}

struct RingView: NSViewRepresentable {
    var style: RingStyle
    var lineWidth: CGFloat = 2
    var track: NSColor? = nil

    final class Coordinator {
        var style: RingStyle?
        let trackLayer = CAShapeLayer()
        let arc = CAShapeLayer()
    }

    func makeCoordinator() -> Coordinator { Coordinator() }

    func makeNSView(context: Context) -> LayerHost {
        let v = LayerHost()
        let c = context.coordinator
        for l in [c.trackLayer, c.arc] {
            l.fillColor = nil
            l.lineCap = .round
            v.layer?.addSublayer(l)
        }
        v.onLayout = { host in
            let b = host.bounds
            for l in [c.trackLayer, c.arc] {
                l.frame = b
                let inset = lineWidth / 2 + 0.5
                // Start at 12 o'clock and run clockwise.
                let path = CGMutablePath()
                let r = min(b.width, b.height) / 2 - inset
                path.addArc(center: CGPoint(x: b.midX, y: b.midY), radius: max(r, 1),
                            startAngle: -.pi / 2, endAngle: 1.5 * .pi, clockwise: false)
                l.path = path
            }
        }
        return v
    }

    func updateNSView(_ v: LayerHost, context: Context) {
        let c = context.coordinator
        c.trackLayer.strokeColor = track?.cgColor
        c.trackLayer.lineWidth = lineWidth
        c.trackLayer.isHidden = track == nil
        c.arc.lineWidth = lineWidth
        guard c.style != style else { return }
        c.style = style
        let arc = c.arc
        arc.removeAllAnimations()
        arc.lineDashPattern = nil
        arc.strokeStart = 0
        arc.strokeEnd = 1
        arc.opacity = 1
        switch style {
        case .chase(let color):
            arc.strokeColor = color.cgColor
            arc.opacity = 0.75
            let head = CAKeyframeAnimation(keyPath: "strokeEnd")
            head.values = [0, 1, 1]
            head.keyTimes = [0, 0.5, 1]
            let tail = CAKeyframeAnimation(keyPath: "strokeStart")
            tail.values = [0, 0, 1]
            tail.keyTimes = [0, 0.5, 1]
            let g = CAAnimationGroup()
            g.animations = [head, tail]
            g.duration = 1.9
            g.repeatCount = .infinity
            g.timingFunction = CAMediaTimingFunction(name: .easeInEaseOut)
            arc.add(g, forKey: "draw")
            arc.strokeStart = 0
            arc.strokeEnd = 0.62
            arc.add(loop("transform.rotation.z", from: 0, to: 2 * Double.pi, duration: 2.4, timing: .linear), forKey: "spin")
        case .spin(let color, let length, let period):
            arc.strokeColor = color.cgColor
            arc.strokeEnd = length
            arc.add(loop("transform.rotation.z", from: 0, to: 2 * Double.pi, duration: period, timing: .linear), forKey: "spin")
        case .dotted(let color, let period):
            arc.strokeColor = color.cgColor
            arc.lineDashPattern = [3, 6]
            arc.lineCap = .round
            arc.add(loop("transform.rotation.z", from: 0, to: 2 * Double.pi, duration: period, timing: .linear), forKey: "spin")
        case .breathe(let color, let period):
            arc.strokeColor = color.cgColor
            arc.add(loop("opacity", from: 0.3, to: 1.0, duration: period / 2, autoreverses: true), forKey: "breathe")
        case .solid(let color):
            arc.strokeColor = color.cgColor
        }
    }
}

// MARK: - Breathing dot

struct BreathingDot: NSViewRepresentable {
    var color: NSColor
    var period: Double = 2
    var low: Float = 0.35
    var animated = true

    final class Coordinator { var sig = ""; let dot = CALayer() }
    func makeCoordinator() -> Coordinator { Coordinator() }

    func makeNSView(context: Context) -> LayerHost {
        let v = LayerHost()
        let dot = context.coordinator.dot
        v.layer?.addSublayer(dot)
        v.onLayout = { host in
            dot.frame = host.bounds
            dot.cornerRadius = min(host.bounds.width, host.bounds.height) / 2
        }
        return v
    }

    func updateNSView(_ v: LayerHost, context: Context) {
        let c = context.coordinator
        let sig = "\(color)|\(period)|\(low)|\(animated)"
        guard sig != c.sig else { return }
        c.sig = sig
        c.dot.backgroundColor = color.cgColor
        c.dot.removeAllAnimations()
        if animated {
            c.dot.add(loop("opacity", from: low, to: 1.0, duration: period / 2, autoreverses: true), forKey: "breathe")
        }
    }
}

// MARK: - Agent orb: glossy blue sphere

struct OrbView: NSViewRepresentable {
    var state: String   // thinking | here | away | switching

    final class Coordinator {
        var state = ""
        let glow = CALayer()
        let ripple = CAShapeLayer()
        let body = CAGradientLayer()
        let shade = CAGradientLayer()
        let shine = CAGradientLayer()
    }
    func makeCoordinator() -> Coordinator { Coordinator() }

    func makeNSView(context: Context) -> LayerHost {
        let v = LayerHost()
        let c = context.coordinator
        c.body.type = .radial
        c.body.masksToBounds = true
        c.shade.masksToBounds = true
        c.shine.type = .radial
        c.shine.masksToBounds = true
        c.ripple.fillColor = nil
        c.ripple.lineWidth = 1.5
        c.glow.shadowOffset = CGSize(width: 0, height: 4)
        c.glow.shadowRadius = 7
        for l in [c.ripple, c.glow, c.body] as [CALayer] { v.layer?.addSublayer(l) }
        c.body.addSublayer(c.shade)
        c.body.addSublayer(c.shine)
        v.onLayout = { host in
            let b = host.bounds
            let r = min(b.width, b.height) / 2
            c.glow.frame = b
            c.glow.cornerRadius = r
            c.glow.backgroundColor = NSColor.white.cgColor
            c.glow.shadowPath = CGPath(ellipseIn: b, transform: nil)
            c.body.frame = b
            c.body.cornerRadius = r
            c.shade.frame = c.body.bounds
            c.shine.frame = c.body.bounds
            c.ripple.frame = b
            c.ripple.path = CGPath(ellipseIn: b.insetBy(dx: -4, dy: -4), transform: nil)
        }
        return v
    }

    func updateNSView(_ v: LayerHost, context: Context) {
        let c = context.coordinator
        guard c.state != state else { return }
        c.state = state
        let away = state == "away"
        // radial-gradient(circle at 32% 28%, #fff 0, #cfe3ff 18%, #5aa2ff 46%, #1f5fd6 78%, #173f8f 100%)
        let lively: [NSColor] = [
            NSColor(srgbRed: 1, green: 1, blue: 1, alpha: 1),
            NSColor(srgbRed: 0xcf / 255, green: 0xe3 / 255, blue: 1, alpha: 1),
            NSColor(srgbRed: 0x5a / 255, green: 0xa2 / 255, blue: 1, alpha: 1),
            NSColor(srgbRed: 0x1f / 255, green: 0x5f / 255, blue: 0xd6 / 255, alpha: 1),
            NSColor(srgbRed: 0x17 / 255, green: 0x3f / 255, blue: 0x8f / 255, alpha: 1),
        ]
        let colors = away ? lively.map { desaturate($0, 0.25) } : lively
        c.body.colors = colors.map(\.cgColor)
        c.body.locations = [0, 0.18, 0.46, 0.78, 1]
        c.body.startPoint = CGPoint(x: 0.32, y: 0.28)
        c.body.endPoint = CGPoint(x: 1.31, y: 1.27)
        c.shade.colors = [NSColor.black.withAlphaComponent(0).cgColor, NSColor.black.withAlphaComponent(0.18).cgColor]
        c.shade.locations = [0.6, 1]
        c.shade.startPoint = CGPoint(x: 0.5, y: 0)
        c.shade.endPoint = CGPoint(x: 0.5, y: 1)
        c.shine.colors = [NSColor.white.withAlphaComponent(0.85).cgColor, NSColor.white.withAlphaComponent(0).cgColor]
        c.shine.startPoint = CGPoint(x: 0.34, y: 0.24)
        c.shine.endPoint = CGPoint(x: 0.58, y: 0.46)
        c.glow.shadowColor = (away ? Palette.faint : Palette.blue).cgColor
        c.glow.shadowOpacity = away ? 0.25 : 0.35
        c.ripple.strokeColor = Palette.blue.withAlphaComponent(0.35).cgColor
        c.ripple.opacity = 0

        for l in [c.body, c.glow, c.ripple] as [CALayer] { l.removeAllAnimations() }
        switch state {
        case "thinking":
            c.body.add(loop("transform.scale", from: 1.0, to: 1.06, duration: 0.8, autoreverses: true), forKey: "pulse")
            c.glow.add(loop("transform.scale", from: 1.0, to: 1.06, duration: 0.8, autoreverses: true), forKey: "pulse")
            let g = CAAnimationGroup()
            let s = CABasicAnimation(keyPath: "transform.scale"); s.fromValue = 0.85; s.toValue = 1.35
            let o = CABasicAnimation(keyPath: "opacity"); o.fromValue = 0.9; o.toValue = 0
            g.animations = [s, o]
            g.duration = 1.6
            g.repeatCount = .infinity
            g.timingFunction = CAMediaTimingFunction(name: .easeOut)
            c.ripple.add(g, forKey: "ripple")
        case "switching":
            c.body.add(loop("transform.scale", from: 1.0, to: 0.86, duration: 0.7, autoreverses: true), forKey: "swap")
            c.glow.add(loop("shadowOpacity", from: 0.2, to: 0.7, duration: 0.7, autoreverses: true), forKey: "swap")
        case "here":
            c.glow.add(loop("shadowOpacity", from: 0.25, to: 0.6, duration: 1.6, autoreverses: true), forKey: "glow")
            c.body.add(loop("transform.scale", from: 1.0, to: 1.025, duration: 1.6, autoreverses: true), forKey: "float")
        default:
            break
        }
    }

    private func desaturate(_ c: NSColor, _ k: CGFloat) -> NSColor {
        guard let s = c.usingColorSpace(.sRGB) else { return c }
        let grey = 0.3 * s.redComponent + 0.59 * s.greenComponent + 0.11 * s.blueComponent
        func mix(_ v: CGFloat) -> CGFloat { min(1, (grey + (v - grey) * k) * 1.08) }
        return NSColor(srgbRed: mix(s.redComponent), green: mix(s.greenComponent), blue: mix(s.blueComponent), alpha: 1)
    }
}

// MARK: - Typing dots

struct TypingDots: NSViewRepresentable {
    final class Coordinator { var dots: [CALayer] = [] }
    func makeCoordinator() -> Coordinator { Coordinator() }

    func makeNSView(context: Context) -> LayerHost {
        let v = LayerHost()
        let c = context.coordinator
        for i in 0..<3 {
            let d = CALayer()
            d.backgroundColor = Palette.dim.cgColor
            d.cornerRadius = 3
            d.opacity = 0.35
            v.layer?.addSublayer(d)
            c.dots.append(d)
            let y = CAKeyframeAnimation(keyPath: "transform.translation.y")
            y.values = [0, -4, 0, 0]
            let o = CAKeyframeAnimation(keyPath: "opacity")
            o.values = [0.35, 1, 0.35, 0.35]
            let g = CAAnimationGroup()
            for a in [y, o] { a.keyTimes = [0, 0.3, 0.6, 1] }
            g.animations = [y, o]
            g.duration = 1.2
            g.repeatCount = .infinity
            g.timingFunction = CAMediaTimingFunction(name: .easeInEaseOut)
            g.beginTime = CACurrentMediaTime() + 0.15 * Double(i)
            g.fillMode = .backwards
            d.add(g, forKey: "bounce")
        }
        v.onLayout = { host in
            let b = host.bounds
            for (i, d) in c.dots.enumerated() {
                d.frame = CGRect(x: CGFloat(i) * 10, y: b.midY - 3, width: 6, height: 6)
            }
        }
        return v
    }

    func updateNSView(_ v: LayerHost, context: Context) {}
}

// MARK: - Session dots (presence HUD)

struct SessionDots: NSViewRepresentable {
    var working: Int
    var idle: Int

    final class Coordinator { var sig = ""; var dots: [CALayer] = [] }
    func makeCoordinator() -> Coordinator { Coordinator() }

    func makeNSView(context: Context) -> LayerHost {
        let v = LayerHost()
        let c = context.coordinator
        v.onLayout = { host in
            for (i, d) in c.dots.enumerated() {
                d.frame = CGRect(x: CGFloat(i) * 10, y: host.bounds.midY - 2.5, width: 5, height: 5)
            }
        }
        return v
    }

    func updateNSView(_ v: LayerHost, context: Context) {
        let c = context.coordinator
        let sig = "\(working)|\(idle)"
        guard sig != c.sig else { return }
        c.sig = sig
        c.dots.forEach { $0.removeFromSuperlayer() }
        c.dots = []
        for i in 0..<(working + idle) {
            let d = CALayer()
            d.cornerRadius = 2.5
            let on = i < working
            d.backgroundColor = (on ? Palette.green : Palette.faint).cgColor
            d.opacity = on ? 1 : 0.45
            d.frame = CGRect(x: CGFloat(i) * 10, y: v.bounds.midY - 2.5, width: 5, height: 5)
            if on {
                let g = CAAnimationGroup()
                let s = CABasicAnimation(keyPath: "transform.scale"); s.fromValue = 0.8; s.toValue = 1.3
                let o = CABasicAnimation(keyPath: "opacity"); o.fromValue = 0.45; o.toValue = 1
                g.animations = [s, o]
                g.duration = 0.85
                g.autoreverses = true
                g.repeatCount = .infinity
                g.timingFunction = CAMediaTimingFunction(name: .easeInEaseOut)
                g.beginTime = CACurrentMediaTime() + 0.14 * Double(i)
                g.fillMode = .backwards
                d.add(g, forKey: "pulse")
            }
            v.layer?.addSublayer(d)
            c.dots.append(d)
        }
    }
}

// MARK: - Breathing container (any SwiftUI content, breathed by Core Animation)

struct Breathing<Content: View>: NSViewRepresentable {
    var low: Float = 0.35
    var high: Float = 1
    var period: Double = 4
    var scale: Double = 0
    @ViewBuilder var content: Content

    func makeNSView(context: Context) -> NSView {
        let host = NSHostingView(rootView: content)
        host.sizingOptions = [.intrinsicContentSize]
        let v = BreathHost()
        v.wantsLayer = true
        v.addSubview(host)
        host.translatesAutoresizingMaskIntoConstraints = false
        NSLayoutConstraint.activate([
            host.centerXAnchor.constraint(equalTo: v.centerXAnchor),
            host.centerYAnchor.constraint(equalTo: v.centerYAnchor),
        ])
        host.wantsLayer = true
        v.inner = host
        v.anim = { [low, high, period, scale] layer in
            layer.add(loop("opacity", from: low, to: high, duration: period / 2, autoreverses: true), forKey: "breathe")
            if scale > 0 {
                layer.add(loop("transform.scale", from: 1, to: 1 + scale, duration: period / 2, autoreverses: true), forKey: "swell")
            }
        }
        return v
    }

    func updateNSView(_ v: NSView, context: Context) {
        (v as? BreathHost)?.inner.flatMap { $0 as? NSHostingView<Content> }?.rootView = content
    }

    final class BreathHost: NSView {
        var inner: NSView?
        var anim: ((CALayer) -> Void)?
        private var armed = false
        override func layout() {
            super.layout()
            guard let layer = inner?.layer, !armed, inner?.bounds.width ?? 0 > 0 else { return }
            armed = true
            // Scale around the content's center.
            let f = inner!.frame
            layer.anchorPoint = CGPoint(x: 0.5, y: 0.5)
            layer.position = CGPoint(x: f.midX, y: f.midY)
            anim?(layer)
        }
        override func hitTest(_ point: NSPoint) -> NSView? { nil }
    }
}
