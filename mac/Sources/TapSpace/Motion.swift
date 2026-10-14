import AppKit
import Observation
import QuartzCore
import SwiftUI

// The motion language, lifted from SnappWAU: damped harmonic springs,
// substepped per display frame, snapped once settled, driven by one
// CADisplayLink per surface that sleeps whenever nothing moves.

/// Damped harmonic spring, integrated per display frame.
struct Spring {
    var value: Double
    var velocity: Double = 0
    var target: Double
    var stiffness: Double
    var damping: Double

    init(_ value: Double, stiffness: Double, damping: Double) {
        self.value = value
        self.target = value
        self.stiffness = stiffness
        self.damping = damping
    }

    init(_ value: Double, _ p: SpringPreset) {
        self.init(value, stiffness: p.stiffness, damping: p.damping)
    }

    var settled: Bool { abs(value - target) < 0.002 && abs(velocity) < 0.002 }
    var atRest: Bool { value == target && velocity == 0 }

    mutating func step(_ h: Double) {
        velocity += (-stiffness * (value - target) - damping * velocity) * h
        value += velocity * h
    }

    mutating func snap(to v: Double) { value = v; target = v; velocity = 0 }

    mutating func use(_ p: SpringPreset) { stiffness = p.stiffness; damping = p.damping }

    /// Substepped advance; snaps when settled. Returns true when the value moved.
    mutating func advance(_ dt: Double) -> Bool {
        guard !atRest else { return false }
        let n = max(1, Int((dt * 480).rounded(.up)))
        let h = dt / Double(n)
        for _ in 0..<n { step(h) }
        if settled { value = target; velocity = 0 }
        return true
    }
}

struct SpringPreset: Equatable {
    var stiffness: Double
    var damping: Double

    /// Critically-ish damped, the default for layout moves.
    static let smooth = SpringPreset(stiffness: 260, damping: 30)
    /// Slightly underdamped: lands with a small wobble.
    static let liquid = SpringPreset(stiffness: 260, damping: 24)
    /// Quick and firm.
    static let snappy = SpringPreset(stiffness: 420, damping: 36)
    /// Underdamped pop, for dots and badges.
    static let pop = SpringPreset(stiffness: 380, damping: 15)
    /// Soft and slow, for blur-ins.
    static let gentle = SpringPreset(stiffness: 120, damping: 21)
    /// Strokes that draw themselves.
    static let draw = SpringPreset(stiffness: 70, damping: 16)
    /// SnappWAU's droplet: wobbles like water.
    static let droplet = SpringPreset(stiffness: 420, damping: 19)
    /// SnappWAU's spread (out) and retract (back) springs.
    static let spreadW = SpringPreset(stiffness: 300, damping: 25)
    static let spreadH = SpringPreset(stiffness: 190, damping: 23)
    static let retractW = SpringPreset(stiffness: 330, damping: 34)
    static let retractH = SpringPreset(stiffness: 420, damping: 38)
}

/// Anything the display link advances. Returns true while it still needs frames.
@MainActor protocol MotionDriver: AnyObject {
    func advance(_ dt: Double) -> Bool
    /// Jump straight to the resting state (snapshots).
    func finish()
}

/// One display link per surface. It runs only while some driver is moving.
@MainActor final class Motion: NSObject {
    static let main = Motion("main")
    static let pill = Motion("pill")

    let name: String
    private(set) var link: CADisplayLink?
    private var drivers: [ObjectIdentifier: MotionDriver] = [:]
    private var lastTimestamp: CFTimeInterval?
    /// Frames since the last read (the --fps logger resets it).
    var frames = 0
    /// Extra condition that keeps the link awake (the pill's hover timers).
    var keepAwake: (() -> Bool)?

    init(_ name: String) { self.name = name }

    var isPaused: Bool { link?.isPaused ?? true }
    var isStill: Bool { drivers.isEmpty }
    var activeCount: Int { drivers.count }

    func attach(to view: NSView) {
        link?.invalidate()
        let l = view.displayLink(target: self, selector: #selector(tick(_:)))
        l.preferredFrameRateRange = CAFrameRateRange(minimum: 80, maximum: 120, preferred: 120)
        l.add(to: .main, forMode: .common)
        l.isPaused = drivers.isEmpty
        link = l
    }

    func activate(_ d: MotionDriver) {
        drivers[ObjectIdentifier(d)] = d
        wake()
    }

    func wake() {
        guard let link, link.isPaused else { return }
        lastTimestamp = nil
        link.isPaused = false
    }

    @objc private func tick(_ link: CADisplayLink) {
        let now = link.timestamp
        let dt = lastTimestamp.map { min(max(now - $0, 1.0 / 240), 1.0 / 30) } ?? (1.0 / 120)
        lastTimestamp = now
        frames += 1
        for (k, d) in drivers where !d.advance(dt) {
            // A completion may have re-armed the same driver; keep it then.
            if let again = d as? SpringBox, !again.atRest { continue }
            drivers[k] = nil
        }
        if drivers.isEmpty && !(keepAwake?() ?? false) {
            link.isPaused = true
        }
    }

    /// Snap everything to rest (snapshots). Completions may arm more drivers,
    /// so it loops until the set drains.
    func settleAll() {
        var rounds = 0
        while !drivers.isEmpty && rounds < 40 {
            let batch = drivers
            drivers.removeAll()
            for (_, d) in batch { d.finish() }
            rounds += 1
        }
    }

    /// Advance in fixed steps without a display link (offscreen verification).
    func run(seconds: Double, step: Double = 1.0 / 120) {
        var t = 0.0
        while t < seconds {
            for (k, d) in drivers where !d.advance(step) {
                if let again = d as? SpringBox, !again.atRest { continue }
                drivers[k] = nil
            }
            t += step
        }
    }
}

/// One observable spring value. Views read `value`; only they re-render.
@MainActor @Observable
final class SpringBox: MotionDriver {
    private(set) var value: Double
    @ObservationIgnored private var spring: Spring
    @ObservationIgnored let motion: Motion
    @ObservationIgnored private var delay: Double = 0
    @ObservationIgnored private var pending: Double?
    @ObservationIgnored private var completion: (() -> Void)?
    /// Called on every frame the value moves (layout followers).
    @ObservationIgnored var onStep: ((Double) -> Void)?

    init(_ v: Double, _ p: SpringPreset = .smooth, motion: Motion = .main) {
        value = v
        spring = Spring(v, p)
        self.motion = motion
    }

    var target: Double { pending ?? spring.target }
    var velocity: Double { spring.velocity }
    var atRest: Bool { pending == nil && spring.atRest }

    func set(_ t: Double, _ p: SpringPreset? = nil, delay: Double = 0, then: (() -> Void)? = nil) {
        if let p { spring.use(p) }
        completion = then
        if delay > 0 {
            self.delay = delay
            pending = t
        } else {
            pending = nil
            self.delay = 0
            spring.target = t
        }
        motion.activate(self)
    }

    func snap(_ v: Double) {
        pending = nil
        delay = 0
        spring.snap(to: v)
        if value != v { value = v; onStep?(v) }
    }

    func kick(_ velocity: Double) {
        spring.velocity += velocity
        motion.activate(self)
    }

    func advance(_ dt: Double) -> Bool {
        if let p = pending {
            delay -= dt
            if delay > 0 { return true }
            spring.target = p
            pending = nil
        }
        if spring.advance(dt), value != spring.value {
            value = spring.value
            onStep?(value)
        }
        if atRest, let c = completion {
            completion = nil
            c()
        }
        return !atRest
    }

    func finish() {
        if let p = pending { spring.target = p; pending = nil; delay = 0 }
        spring.snap(to: spring.target)
        if value != spring.value { value = spring.value; onStep?(value) }
        if let c = completion { completion = nil; c() }
    }
}

// MARK: - Math

func lerp(_ a: CGFloat, _ b: CGFloat, _ t: Double) -> CGFloat { a + (b - a) * CGFloat(t) }
func lerp(_ a: Double, _ b: Double, _ t: Double) -> Double { a + (b - a) * t }
func clamp01(_ v: Double) -> Double { min(max(v, 0), 1) }
func smoothstep(_ e0: Double, _ e1: Double, _ x: Double) -> Double {
    let t = clamp01((x - e0) / (e1 - e0))
    return t * t * (3 - 2 * t)
}
func lerpRect(_ a: CGRect, _ b: CGRect, _ t: Double) -> CGRect {
    CGRect(x: lerp(a.minX, b.minX, t), y: lerp(a.minY, b.minY, t),
           width: lerp(a.width, b.width, t), height: lerp(a.height, b.height, t))
}
/// Half-point rounding keeps text crisp on 2x displays.
func pixel(_ v: CGFloat) -> CGFloat { (v * 2).rounded() / 2 }

func aspectFit(_ aspect: CGFloat, in r: CGRect) -> CGRect {
    guard aspect > 0, r.width > 0, r.height > 0 else { return r }
    if aspect > r.width / r.height {
        let h = r.width / aspect
        return CGRect(x: r.minX, y: r.midY - h / 2, width: r.width, height: h)
    }
    let w = r.height * aspect
    return CGRect(x: r.midX - w / 2, y: r.minY, width: w, height: r.height)
}

/// The tangent-bezier bridge between two circles (metaball / gooey neck).
/// Returns nil when one circle swallows the other or they're too far apart.
func metaballNeck(_ c1: CGPoint, _ r1: CGFloat, _ c2: CGPoint, _ r2: CGFloat,
                  spread v: CGFloat = 0.85, handleSize: CGFloat = 2.4) -> Path? {
    let d = hypot(c2.x - c1.x, c2.y - c1.y)
    let maxDist = r1 + r2 * 2.6
    guard r1 > 0, r2 > 0, d <= maxDist, d > abs(r1 - r2) else { return nil }

    var u1: CGFloat = 0, u2: CGFloat = 0
    if d < r1 + r2 {
        u1 = acos(min(max((r1 * r1 + d * d - r2 * r2) / (2 * r1 * d), -1), 1))
        u2 = acos(min(max((r2 * r2 + d * d - r1 * r1) / (2 * r2 * d), -1), 1))
    }
    let between = atan2(c2.y - c1.y, c2.x - c1.x)
    let maxSpread = acos(min(max((r1 - r2) / d, -1), 1))

    let a1 = between + u1 + (maxSpread - u1) * v
    let a2 = between - u1 - (maxSpread - u1) * v
    let a3 = between + .pi - u2 - (.pi - u2 - maxSpread) * v
    let a4 = between - .pi + u2 + (.pi - u2 - maxSpread) * v

    func polar(_ c: CGPoint, _ a: CGFloat, _ r: CGFloat) -> CGPoint {
        CGPoint(x: c.x + cos(a) * r, y: c.y + sin(a) * r)
    }
    let p1 = polar(c1, a1, r1), p2 = polar(c1, a2, r1)
    let p3 = polar(c2, a3, r2), p4 = polar(c2, a4, r2)

    let total = r1 + r2
    let d2 = min(v * handleSize, hypot(p3.x - p1.x, p3.y - p1.y) / total) * min(1, d * 2 / total)
    let h1 = polar(p1, a1 - .pi / 2, r1 * d2)
    let h2 = polar(p2, a2 + .pi / 2, r1 * d2)
    let h3 = polar(p3, a3 + .pi / 2, r2 * d2)
    let h4 = polar(p4, a4 - .pi / 2, r2 * d2)

    var path = Path()
    path.move(to: p1)
    path.addCurve(to: p3, control1: h1, control2: h3)
    path.addLine(to: p4)
    path.addCurve(to: p2, control1: h4, control2: h2)
    path.closeSubpath()
    return path
}
