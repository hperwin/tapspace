import SwiftUI

// Update card motion (contract 4, R9): when each piece of a card plays, and
// the small modifiers that play it. Every spring, mapping and stagger here is
// one the app already uses; a card only sequences them. A card that did not
// just arrive builds at rest, and no card plays twice in a session.

/// The beat table, in seconds after the card arrives (the web page and Glint
/// run the same table with their own staggers): face and line, the visual,
/// the items (the rail's 0.06 s), each moved item 0.04 s after the one before
/// once the last item has landed, the approval images, the ask.
struct CardTimes: Equatable {
    static let stagger = 0.06
    static let cap = 10
    static let imageCap = 5
    var face = 0.10
    var line = 0.10
    var visual = 0.25
    var ask = 0.80
    var pills: [Double] = []
    var items: [Double] = []
    /// One per moved item, in item order.
    var changes: [Double] = []
    var images: [Double] = []

    init(items n: Int, changed k: Int, images m: Int, pills p: Int = 0) {
        pills = (0..<p).map { 0.25 + 0.04 * Double($0) }
        items = (0..<n).map { 0.45 + Self.stagger * Double(min($0, Self.cap)) }
        let landed = 0.45 + Self.stagger * Double(min(max(n - 1, 0), Self.cap)) + 0.30
        changes = (0..<k).map { landed + 0.04 * Double($0) }
        images = (0..<m).map { 0.65 + Self.stagger * Double(min($0, Self.imageCap)) }
    }
}

@MainActor enum CardMotion {
    private static var played: Set<String> = []

    /// True the first time a card's row key asks in this app session: a card plays once.
    static func claim(_ key: String) -> Bool { played.insert(key).inserted }

    /// An item's snapshot moved since the thread's previous card (contract 1, `was`): its change beat plays.
    nonisolated static func moved(wasPhase: String?, phase: String?, wasGoals: [Int]?, goals: [Int]?,
                                  wasCurrent: Int?, current: Int?) -> Bool {
        if let w = wasPhase, w != phase { return true }
        if let w = wasGoals, w != goals { return true }
        if let w = wasCurrent, w != current { return true }
        return false
    }

    /// The pure rules, for the harness to print (the package has no test target).
    static func checks() -> [String] {
        var bad: [String] = []
        func near(_ a: [Double], _ b: [Double]) -> Bool { a.count == b.count && zip(a, b).allSatisfy { abs($0 - $1) < 1e-9 } }
        let r = CardTimes(items: 3, changed: 2, images: 2, pills: 4)
        if !near(r.items, [0.45, 0.51, 0.57]) { bad.append("items \(r.items)") }
        if !near(r.changes, [0.87, 0.91]) { bad.append("changes \(r.changes)") }
        if !near(r.images, [0.65, 0.71]) { bad.append("images \(r.images)") }
        if !near(r.pills, [0.25, 0.29, 0.33, 0.37]) { bad.append("pills \(r.pills)") }
        let big = CardTimes(items: 8, changed: 8, images: 10)
        if !near([big.items[7], big.changes[0], big.images[9]], [0.87, 1.17, 0.95]) { bad.append("big card") }
        if !moved(wasPhase: "enroute", phase: "landed", wasGoals: nil, goals: nil, wasCurrent: nil, current: nil)
            || moved(wasPhase: "landed", phase: "landed", wasGoals: [3, 3], goals: [3, 3], wasCurrent: nil, current: nil)
            || !moved(wasPhase: nil, phase: nil, wasGoals: nil, goals: nil, wasCurrent: 1, current: 2) { bad.append("moved") }
        if !claim("cardmotion.check") || claim("cardmotion.check") { bad.append("claim") }
        let pts = SparkGeometry.points([1, 2, 3], in: CGRect(x: 0, y: 0, width: 104, height: 36))
        if pts != [CGPoint(x: 2, y: 34), CGPoint(x: 52, y: 18), CGPoint(x: 102, y: 2)] { bad.append("spark \(pts)") }
        if SparkGeometry.points([5, 5], in: CGRect(x: 0, y: 0, width: 40, height: 36)).map(\.y) != [18, 18] { bad.append("flat") }
        return bad
    }
}

/// One card's springs: at 1 for a card drawn at rest, at 0 until `play` for one that just arrived.
/// Each piece reads only its own box, in its own small modifier (perf rule: a frame re-renders that piece).
@MainActor final class CardBeats {
    let times: CardTimes
    let face: SpringBox, line: SpringBox, visual: SpringBox, ask: SpringBox
    let pills: [SpringBox], items: [SpringBox], changes: [SpringBox], images: [SpringBox]

    init(_ t: CardTimes, rest: Bool) {
        let v: Double = rest ? 1 : 0
        times = t
        face = SpringBox(v, .pop)
        line = SpringBox(v, .smooth)
        visual = SpringBox(v, .smooth)
        ask = SpringBox(v, .gentle)
        pills = t.pills.map { _ in SpringBox(v, .pop) }
        items = t.items.map { _ in SpringBox(v, .gentle) }
        changes = t.changes.map { _ in SpringBox(v, .smooth) }
        images = t.images.map { _ in SpringBox(v, .chatIn) }
    }

    /// Every piece on its own spring and delay. `visual`: `.smooth` for a fill or a word, `.draw` for a sparkline.
    func play(visual preset: SpringPreset) {
        let t = times
        face.set(1, .pop, delay: t.face)
        line.set(1, .smooth, delay: t.line)
        visual.set(1, preset, delay: t.visual)
        for (b, d) in zip(pills, t.pills) { b.set(1, .pop, delay: d) }
        for (b, d) in zip(items, t.items) { b.set(1, .gentle, delay: d) }
        for (b, d) in zip(changes, t.changes) { b.set(1, .smooth, delay: d) }
        for (b, d) in zip(images, t.images) { b.set(1, .chatIn, delay: d) }
        ask.set(1, .gentle, delay: t.ask)
    }

    /// Straight to rest: a card that already played, Reduce Motion, a hidden window.
    func rest() { for b in [face, line, visual, ask] + pills + items + changes + images { b.snap(1) } }
}

/// The face, a timeline pill, a check disc: in from 0.8 (never toward 0: an OrbView rides inside, CONTRACT.md).
struct CardPop: ViewModifier {
    let box: SpringBox
    func body(content: Content) -> some View {
        let p = box.value
        content.scaleEffect(0.8 + 0.2 * p).opacity(clamp01(p))
    }
}

/// A line of words rising in: the "working on your notes" row's entrance (VisualView.swift:850-856).
struct CardRise: ViewModifier {
    let box: SpringBox
    func body(content: Content) -> some View {
        let p = box.value
        content.opacity(smoothstep(0.35, 1, p)).blur(radius: 6 * (1 - clamp01(p))).offset(y: 5 * (1 - p))
    }
}

/// RailArrival's mapping (the web page's railin, ChatView.swift:254-266) on a spring the card owns: items, the ask.
struct CardArrive: ViewModifier {
    let box: SpringBox
    func body(content: Content) -> some View {
        let p = box.value
        content.opacity(clamp01(p)).blur(radius: 12 * (1 - clamp01(p))).offset(y: 8 * (1 - p))
    }
}

/// An approval image with its buttons, arriving like a received bubble (BubbleEntrance on chatIn).
struct CardIn: ViewModifier {
    let box: SpringBox
    func body(content: Content) -> some View {
        content.modifier(BubbleEntrance(p: box.value, fly: false, mine: false, lift: nil, hidden: false))
    }
}

/// The progress visual and a master item's bar: ChatStepTrack's long bar (ChatProgress.swift:322-333) without the
/// knob, filling from `from` to `to` (0...1) as `box` goes 0 -> 1. Accent when it ends full.
struct CardBar: View {
    let box: SpringBox
    let from: Double
    let to: Double

    var body: some View {
        GeometryReader { g in
            let f = clamp01(lerp(from, to, box.value))
            ZStack(alignment: .leading) {
                Capsule().fill(Color.black.opacity(0.08))
                Capsule().fill(to >= 1 ? Color.tapBlue : Color.ink).frame(width: max(4, g.size.width * f))
            }
        }
        .frame(height: 4)
    }
}

/// Where a sparkline's points sit, the same on every client: inset by half the stroke, the highest value on top, a
/// flat series on the middle line.
enum SparkGeometry {
    static let height: CGFloat = 36
    static let maxWidth: CGFloat = 320
    static let pad: CGFloat = 2

    static func points(_ v: [Double], in r: CGRect) -> [CGPoint] {
        guard v.count >= 2, let lo = v.min(), let hi = v.max() else { return [] }
        let w = r.width - 2 * pad, h = r.height - 2 * pad
        return v.enumerated().map { i, x in
            CGPoint(x: r.minX + pad + w * CGFloat(i) / CGFloat(v.count - 1),
                    y: hi == lo ? r.midY : r.minY + pad + h * CGFloat((hi - x) / (hi - lo)))
        }
    }
}

struct SparkLine: Shape {
    let values: [Double]
    func path(in r: CGRect) -> Path {
        var p = Path()
        let pts = SparkGeometry.points(values, in: r)
        guard let first = pts.first else { return p }
        p.move(to: first)
        for q in pts.dropFirst() { p.addLine(to: q) }
        return p
    }
}

/// The graph visual: a hairline baseline, and the series drawing itself on its box (CheckVeil's trim, `.draw`).
struct CardSpark: View {
    let box: SpringBox
    let values: [Double]
    var body: some View {
        SparkLine(values: values)
            .trim(from: 0, to: clamp01(box.value))
            .stroke(Color.tapBlue, style: StrokeStyle(lineWidth: 2, lineCap: .round, lineJoin: .round))
            .background(alignment: .bottom) { Rectangle().fill(Color.black.opacity(0.08)).frame(height: 1) }
            .frame(maxWidth: SparkGeometry.maxWidth, alignment: .leading)
            .frame(height: SparkGeometry.height)
    }
}

/// An item's face through its change beat: the `was` look until its spring starts, then `now`, with ChatNod's kick.
struct CardItemFace<Face: View>: View {
    let box: SpringBox
    let still: Bool
    @ViewBuilder let face: (_ moved: Bool) -> Face
    var body: some View {
        let moved = box.value > 0.001
        face(moved).modifier(ChatNod(step: nil, pinged: moved ? 1 : 0, still: still))
    }
}

extension RingStyle {
    /// The same ring holding still: faces in a card never loop.
    var resting: RingStyle {
        switch self {
        case .chase(let c): .spin(c, length: 0.62, period: 0)
        case .spin(let c, let l, _): .spin(c, length: l, period: 0)
        case .dotted(let c, _): .dotted(c, period: 0)
        case .breathe(let c, _): .solid(c)
        case .solid: self
        }
    }
}
