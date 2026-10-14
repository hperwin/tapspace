import AppKit
import Observation
import SwiftUI

/// A white liquid selection body that travels between rects along one axis
/// (the tab switcher, the chat rail). The leading edge springs ahead so the
/// body stretches toward its target first; the trailing edge follows softer
/// and lands with a small wobble; a droplet is left behind at the trailing
/// edge and pulled back in through a metaball neck.
@MainActor @Observable
final class LiquidTrack: MotionDriver {
    enum Axis { case horizontal, vertical }

    let axis: Axis
    @ObservationIgnored let motion: Motion
    @ObservationIgnored var cornerRadius: CGFloat
    @ObservationIgnored var dropRadius: CGFloat
    @ObservationIgnored var anchorRadius: CGFloat
    /// How much the cross axis thins while the body is stretched (volume).
    @ObservationIgnored var squish: Double

    // Observed geometry (along-axis edges a/b, cross-axis edges c0/c1, droplet d).
    private(set) var a: Double = 0
    private(set) var b: Double = 0
    private(set) var c0: Double = 0
    private(set) var c1: Double = 0
    private(set) var d: Double = 0
    private(set) var visible = false

    @ObservationIgnored private var sa = Spring(0, .smooth)
    @ObservationIgnored private var sb = Spring(0, .smooth)
    @ObservationIgnored private var sc0 = Spring(0, .smooth)
    @ObservationIgnored private var sc1 = Spring(0, .smooth)
    @ObservationIgnored private var sd = Spring(0, SpringPreset(stiffness: 120, damping: 15))
    @ObservationIgnored private var dir: Double = 1
    @ObservationIgnored private var dropTravel: Double = 1
    @ObservationIgnored private var restLen: Double = 1

    static let lead = SpringPreset(stiffness: 520, damping: 30)
    static let trail = SpringPreset(stiffness: 210, damping: 22)
    static let cross = SpringPreset(stiffness: 300, damping: 26)

    init(axis: Axis, cornerRadius: CGFloat, dropRadius: CGFloat, anchorRadius: CGFloat,
         squish: Double = 0.5, motion: Motion? = nil) {
        self.axis = axis
        self.cornerRadius = cornerRadius
        self.dropRadius = dropRadius
        self.anchorRadius = anchorRadius
        self.squish = squish
        // Resolved here (main actor), not in a nonisolated default argument.
        self.motion = motion ?? .main
    }

    var targetRect: CGRect { rect(sa.target, sb.target, sc0.target, sc1.target) }

    func hide() {
        visible = false
    }

    func move(to r: CGRect, animated: Bool) {
        let (na, nb, n0, n1) = axis == .horizontal
            ? (Double(r.minX), Double(r.maxX), Double(r.minY), Double(r.maxY))
            : (Double(r.minY), Double(r.maxY), Double(r.minX), Double(r.maxX))
        restLen = max(nb - na, 1)
        guard visible, animated else {
            sa.snap(to: na); sb.snap(to: nb); sc0.snap(to: n0); sc1.snap(to: n1)
            sd.snap(to: na)
            visible = true
            publish()
            return
        }
        guard na != sa.target || nb != sb.target || n0 != sc0.target || n1 != sc1.target else { return }

        let oldCenter = (sa.target + sb.target) / 2
        let newCenter = (na + nb) / 2
        if abs(newCenter - oldCenter) > 4 {
            dir = newCenter >= oldCenter ? 1 : -1
            if dir > 0 { sb.use(Self.lead); sa.use(Self.trail) } else { sa.use(Self.lead); sb.use(Self.trail) }
            // The droplet is born at the trailing edge and lags behind it.
            let trailingNow = dir > 0 ? sa.value : sb.value
            let trailingTarget = dir > 0 ? na : nb
            let travel = abs(trailingTarget - trailingNow)
            if travel > 8 {
                sd.snap(to: trailingNow)
                sd.target = trailingTarget
                dropTravel = travel
            }
        } else {
            // A resize in place (a badge appearing): both edges ease.
            sa.use(.liquid); sb.use(.liquid)
        }
        sc0.use(Self.cross); sc1.use(Self.cross)
        sa.target = na; sb.target = nb; sc0.target = n0; sc1.target = n1
        motion.activate(self)
    }

    func advance(_ dt: Double) -> Bool {
        var moved = false
        moved = sa.advance(dt) || moved
        moved = sb.advance(dt) || moved
        moved = sc0.advance(dt) || moved
        moved = sc1.advance(dt) || moved
        moved = sd.advance(dt) || moved
        if moved { publish() }
        return !(sa.atRest && sb.atRest && sc0.atRest && sc1.atRest && sd.atRest)
    }

    func finish() {
        sa.snap(to: sa.target); sb.snap(to: sb.target)
        sc0.snap(to: sc0.target); sc1.snap(to: sc1.target); sd.snap(to: sd.target)
        publish()
    }

    private func publish() {
        if a != sa.value { a = sa.value }
        if b != sb.value { b = sb.value }
        if c0 != sc0.value { c0 = sc0.value }
        if c1 != sc1.value { c1 = sc1.value }
        if d != sd.value { d = sd.value }
    }

    private func rect(_ a: Double, _ b: Double, _ c0: Double, _ c1: Double) -> CGRect {
        axis == .horizontal
            ? CGRect(x: a, y: c0, width: b - a, height: c1 - c0)
            : CGRect(x: c0, y: a, width: c1 - c0, height: b - a)
    }

    var bodyRect: CGRect {
        let len = max(b - a, 1)
        let stretch = len / restLen
        let k = min(max(pow(1 / max(stretch, 0.05), squish), 0.8), 1.06)
        let mid = (c0 + c1) / 2
        let half = (c1 - c0) / 2 * k
        return rect(a, a + len, mid - half, mid + half)
    }

    var path: Path {
        guard visible else { return Path() }
        let r = bodyRect
        let rad = min(cornerRadius, r.width / 2, r.height / 2)
        var path = Path(roundedRect: r, cornerRadius: rad, style: .continuous)

        let gap = abs(sd.target - d)
        let rDrop = dropRadius * CGFloat(smoothstep(0, max(18, dropTravel * 0.3), gap))
        guard rDrop > 0.4 else { return path }

        let horizontal = axis == .horizontal
        let len = horizontal ? r.width : r.height
        let trailingEdge = dir > 0 ? (horizontal ? r.minX : r.minY) : (horizontal ? r.maxX : r.maxY)
        let crossMid = horizontal ? r.midY : r.midX
        let behind = (trailingEdge - d) * dir
        guard behind > -Double(rDrop) else { return path }

        let anchorR = min(anchorRadius, (horizontal ? r.height : r.width) / 2, len / 2)
        let anchorAlong = trailingEdge + dir * Double(anchorR)
        let dc = horizontal ? CGPoint(x: d, y: crossMid) : CGPoint(x: crossMid, y: d)
        let ac = horizontal ? CGPoint(x: anchorAlong, y: crossMid) : CGPoint(x: crossMid, y: anchorAlong)
        path = path.union(Path(ellipseIn: CGRect(x: dc.x - rDrop, y: dc.y - rDrop, width: rDrop * 2, height: rDrop * 2)))
        if let neck = metaballNeck(ac, anchorR, dc, rDrop) {
            path = path.union(neck)
        }
        return path
    }
}

/// Draws a LiquidTrack as one white body with a soft shadow.
struct LiquidCanvas: View {
    let track: LiquidTrack
    var fill: Color = .white
    var shadow: Double = 1

    var body: some View {
        let p = track.path
        Canvas { ctx, _ in
            var s = ctx
            s.addFilter(.shadow(color: .black.opacity(0.07 * shadow), radius: 8, x: 0, y: 4))
            s.addFilter(.shadow(color: .black.opacity(0.06 * shadow), radius: 1.2, x: 0, y: 0.6))
            s.fill(p, with: .color(fill))
            ctx.stroke(p, with: .color(.black.opacity(0.035)), lineWidth: 0.5)
        }
        .allowsHitTesting(false)
    }
}

// MARK: - The menu-bar pill (SnappWAU's Liquid, mirrored to sit right of the notch)

/// The whole pill shape system: one white liquid body that is a pill at rest,
/// grows a surface-tension bulge toward a nearby cursor, and spreads into the
/// card on hover. All coordinates are window-local, top-left origin; the body
/// is anchored on its LEFT edge and grows to the right.
@MainActor @Observable
final class PillLiquid {
    static let pillH: CGFloat = 24
    static let cardW: CGFloat = 316
    static let windowSize = CGSize(width: 400, height: 470)
    static let leftMargin: CGFloat = 28
    /// Cursor distance (pt, from the pill's surface) where the water starts reaching.
    static let reachRadius: CGFloat = 64

    var top: CGFloat = 7
    var anchorLeft: CGFloat { Self.leftMargin }
    private(set) var cardH: CGFloat = 180
    @ObservationIgnored var onNeedsFrames: (() -> Void)?

    func setPillContentWidth(_ w: CGFloat) {
        guard pw.target != w + 18 else { return }
        pw.target = w + 18
        onNeedsFrames?()
    }

    func setCardHeight(_ h: CGFloat) {
        guard cardH != h else { return }
        cardH = h
        onNeedsFrames?()
    }

    // Width leads height on the way out so the card spreads like poured
    // water; height retracts first on the way back.
    private(set) var pw = Spring(60, stiffness: 260, damping: 30)
    private(set) var ew = Spring(0, stiffness: 300, damping: 25)
    private(set) var eh = Spring(0, stiffness: 190, damping: 23)
    // The bulge droplet: slightly underdamped so it wobbles like water.
    private(set) var bx = Spring(0, stiffness: 420, damping: 19)
    private(set) var by = Spring(0, stiffness: 420, damping: 19)
    private(set) var br = Spring(0, stiffness: 340, damping: 21)

    private(set) var expanded = false
    @ObservationIgnored private(set) var hot = false
    @ObservationIgnored private var dwell: Double = 0
    @ObservationIgnored private var outside: Double = 0

    var shapeRect: CGRect {
        let w = lerp(pw.value, Self.cardW, ew.value)
        let h = lerp(Self.pillH, cardH, eh.value)
        return CGRect(x: anchorLeft, y: top, width: max(w, Self.pillH), height: max(h, Self.pillH))
    }

    var cornerRadius: CGFloat {
        let r = shapeRect
        return min(r.height / 2, lerp(Self.pillH / 2, 20, clamp01(eh.value)))
    }

    var cardRect: CGRect { CGRect(x: anchorLeft, y: top, width: Self.cardW, height: cardH) }
    var expansion: Double { clamp01(min(ew.value, eh.value)) }

    var headerOrigin: CGPoint {
        let r = shapeRect
        let x = r.minX + lerp(7, 13, clamp01(ew.value))
        let y = top + lerp(0, 8, clamp01(eh.value))
        return CGPoint(x: pixel(x), y: pixel(y))
    }

    var detailOpacity: Double { smoothstep(0.62, 0.97, ew.value) * smoothstep(0.55, 0.97, eh.value) }

    /// Per-row reveal, staggered top to bottom; the last rows still get a
    /// real fade instead of popping in at the very end of the spread.
    func rowProgress(_ i: Int) -> Double {
        smoothstep(0.48 + min(0.36, 0.045 * Double(max(i, 0))), 1.0, eh.value)
    }

    var debugState: String {
        String(format: "exp=%@ ew=%.3f eh=%.3f br=%.2f", expanded ? "1" : "0", ew.value, eh.value, br.value)
    }

    var isStill: Bool {
        [pw, ew, eh, bx, by, br].allSatisfy { $0.value == $0.target && $0.velocity == 0 }
    }

    var isTiming: Bool { dwell > 0 || outside > 0 }

    var bodyPath: Path {
        let r = shapeRect
        let rad = cornerRadius
        var path = Path(roundedRect: r, cornerRadius: rad, style: .continuous)
        let rb = br.value
        guard rb > 0.35 else { return path }
        let b = CGPoint(x: bx.value, y: by.value)
        let c = CGPoint(x: min(max(b.x, r.minX + rad), r.maxX - rad), y: r.midY)
        path = path.union(Path(ellipseIn: CGRect(x: b.x - rb, y: b.y - rb, width: rb * 2, height: rb * 2)))
        if let neck = metaballNeck(c, rad, b, rb) { path = path.union(neck) }
        return path
    }

    func setExpanded(_ on: Bool) {
        guard on != expanded else { return }
        expanded = on
        dwell = 0
        outside = 0
        if on {
            ew.stiffness = 300; ew.damping = 25
            eh.stiffness = 190; eh.damping = 23
        } else {
            ew.stiffness = 330; ew.damping = 34
            eh.stiffness = 420; eh.damping = 38
        }
        ew.target = on ? 1 : 0
        eh.target = on ? 1 : 0
    }

    func step(dt: Double, mouse: CGPoint?) {
        updateHover(dt: dt, mouse: mouse)
        updateBulgeTarget(mouse: mouse)
        let substeps = max(1, Int((dt * 480).rounded(.up)))
        let h = dt / Double(substeps)
        for spring in [\PillLiquid.pw, \.ew, \.eh, \.bx, \.by, \.br] {
            advance(spring, h, substeps)
        }
    }

    /// Snapshot helper: aim at `mouse`, then jump to rest.
    func settle(mouse: CGPoint?, expanded on: Bool) {
        setExpanded(on)
        updateBulgeTarget(mouse: mouse)
        if br.target > 0 {
            // A droplet reaches from the surface; settle it where it would rest.
            br.snap(to: br.target); bx.snap(to: bx.target); by.snap(to: by.target)
        }
        for key in [\PillLiquid.pw, \.ew, \.eh, \.bx, \.by, \.br] {
            self[keyPath: key].snap(to: self[keyPath: key].target)
        }
        dwell = 0
        outside = 0
    }

    private func advance(_ key: ReferenceWritableKeyPath<PillLiquid, Spring>, _ h: Double, _ n: Int) {
        var s = self[keyPath: key]
        guard s.value != s.target || s.velocity != 0 else { return }
        for _ in 0..<n { s.step(h) }
        if s.settled { s.value = s.target; s.velocity = 0 }
        self[keyPath: key] = s
    }

    private func aim(_ key: ReferenceWritableKeyPath<PillLiquid, Spring>, _ v: Double) {
        if self[keyPath: key].target != v { self[keyPath: key].target = v }
    }

    func distanceToPill(_ p: CGPoint) -> CGFloat {
        let r = restingPill
        let rad = Self.pillH / 2
        let cx = min(max(p.x, r.minX + rad), r.maxX - rad)
        return hypot(p.x - cx, p.y - r.midY) - rad
    }

    private var restingPill: CGRect {
        CGRect(x: anchorLeft, y: top, width: pw.target, height: Self.pillH)
    }

    private func updateHover(dt: Double, mouse: CGPoint?) {
        let pill = restingPill
        let pillZone = CGRect(x: pill.minX - 1, y: -1, width: pill.width + 2, height: pill.maxY + 2)
        let cardZone = cardRect.insetBy(dx: -10, dy: -10)

        guard let m = mouse else {
            hot = false
            dwell = 0
            if expanded { setExpanded(false) }
            return
        }
        if expanded {
            if cardZone.contains(m) || pillZone.contains(m) {
                outside = 0
            } else {
                outside += dt
                if outside > 0.14 { setExpanded(false) }
            }
            hot = cardZone.contains(m) || pillZone.contains(m)
        } else {
            if pillZone.contains(m) {
                dwell += dt
                if dwell >= 0.045 { setExpanded(true) }
            } else {
                dwell = 0
            }
            hot = pillZone.contains(m)
        }
    }

    private func updateBulgeTarget(mouse: CGPoint?) {
        let r = shapeRect
        let rad = cornerRadius
        let active = !expanded && ew.value < 0.08
        guard active, let m = mouse else { aim(\.br, 0); return }

        let c = CGPoint(x: min(max(m.x, r.minX + rad), r.maxX - rad), y: r.midY)
        let vx = m.x - c.x, vy = m.y - c.y
        let len = hypot(vx, vy)
        let dist = len - rad
        guard dist > 0.5, len > 0 else { aim(\.br, 0); return }

        let nx = vx / len, ny = vy / len
        let surface = CGPoint(x: c.x + nx * rad, y: c.y + ny * rad)
        let k = 1 - smoothstep(0, Double(Self.reachRadius), Double(dist))
        guard k > 0.001 else { aim(\.br, 0); return }

        let reach = min(CGFloat(17 * k), dist * 0.9)
        let radius = lerp(CGFloat(5), CGFloat(11), k)
        let center = CGPoint(x: surface.x + nx * (reach - radius), y: surface.y + ny * (reach - radius))
        if br.value < 0.3 {
            bx.snap(to: surface.x)
            by.snap(to: surface.y)
        }
        aim(\.bx, center.x)
        aim(\.by, center.y)
        aim(\.br, radius)
    }
}
