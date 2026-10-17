import SwiftUI

// An update card in the thread (CONTRACT sections 4, 9, 13, 14): one subject on top with the line, one visual, the
// items as text rows, the pictures to approve or the card's one Accept, and the question. Every beat is one of
// CardBeats' springs (ChatCardMotion.swift); a card that does not play builds at rest, as a played one ends.

/// An update card in the thread (CONTRACT section 4).
struct ChatCardView: View, Equatable {
    static let maxWidth: CGFloat = 460
    let row: CardRow
    /// The row's served image paths, aligned with card.images.
    let images: [String]
    /// The thread asks for its beats: it arrived live, or it was unread when the thread opened (CONTRACT section 10).
    /// It plays once per session, and only on screen in the focused Chat tab.
    let play: Bool
    let width: CGFloat
    var store: Store = .shared
    /// Seconds it stays out of sight before it arrives the way a received bubble does (a thread that just opened
    /// settles first, CONTRACT section 12). 0: its row's own entrance carries it.
    var hold: Double = 0

    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var beats: CardBeats
    @State private var shell: SpringBox

    init(row: CardRow, images: [String], play: Bool, width: CGFloat, store: Store? = nil, hold: Double = 0) {
        self.row = row
        self.images = images
        self.play = play
        self.width = width
        self.store = store ?? .shared
        self.hold = hold
        _beats = State(initialValue: CardBeats(CardLayout(row.card, images: images.count).times, rest: !play))
        _shell = State(initialValue: SpringBox(play && hold > 0 ? 0 : 1, .chatIn))
    }

    static func == (a: Self, b: Self) -> Bool {
        a.row == b.row && a.images == b.images && a.play == b.play && a.width == b.width && a.hold == b.hold
    }

    var body: some View {
        let c = row.card
        let layout = CardLayout(c, images: images.count)
        let inner = width - 28
        let still = reduceMotion
        VStack(alignment: .leading, spacing: 14) {
            CardHeader(row: row, layout: layout, beats: beats, still: still, store: store)
            if let v = c.visual, layout.visual { CardVisualView(visual: v, beats: beats) }
            if !layout.rows.isEmpty { CardItemRows(card: c, layout: layout, beats: beats, still: still, store: store) }
            if layout.images > 0 {
                CardImages(row: row, paths: Array(images.prefix(layout.images)), width: inner, hero: layout.hero,
                           decide: layout.action == nil, beats: beats, still: still)
            }
            if let a = layout.action {
                // The one Accept / Not quite pair, under the pictures, arriving right after the last of them.
                CardActionView(row: row, tap: a.tap, width: inner, still: still, store: store)
                    .modifier(CardIn(box: beats.images[layout.images]))
            }
            if let ask = c.ask {
                CardAskView(row: row, ask: ask, still: still)
                    .modifier(CardArrive(box: beats.ask))
            }
        }
        .padding(14)
        .frame(width: width, alignment: .leading)
        // The progress column's card look: white, corner 18, the light lift.
        .background(RoundedRectangle(cornerRadius: 18, style: .continuous).fill(Color.white).modifier(ChatCardLift()))
        .modifier(CardShell(box: shell, on: hold > 0))
        .accessibilityElement(children: .contain)
        .onAppear(perform: begin)
    }

    /// The first view (P7 2.2.1, CONTRACT sections 10 and 12): a card the thread asked to play, on screen in the
    /// focused Chat tab, plays once per session. Reduce Motion, another tab or a hidden window: it builds at rest.
    private func begin() {
        guard play else { return }
        let seen = store.tab == .chat && store.windowVisible && store.windowKey
        guard !reduceMotion, seen, CardMotion.claim("\(row.agent)#\(row.id)") else {
            beats.rest()
            shell.snap(1)
            return
        }
        let preset: SpringPreset = row.card.visual?.kind == "graph" ? .draw : .smooth
        guard hold > 0 else { beats.play(visual: preset); return }
        let beats = beats, hold = hold
        shell.set(1, .chatIn, delay: hold)
        Task { @MainActor in
            try? await Task.sleep(for: .seconds(hold))
            beats.play(visual: preset)
        }
    }
}

/// A held card's own arrival (BubbleEntrance received, on CardIn); a card its row carries in has none.
private struct CardShell: ViewModifier {
    let box: SpringBox
    let on: Bool

    func body(content: Content) -> some View {
        if on { content.modifier(CardIn(box: box)) } else { content }
    }
}

// MARK: - What a card draws

/// What a card draws, read once from its data (CONTRACT sections 9, 12, 13): its one subject, whether the pictures
/// are the whole card, which items get a text row, which moved since the thread's last card, and its beat table.
struct CardLayout {
    enum Head: Equatable {
        /// One subject: its face, its name, the line under it.
        case one(String)
        /// Several items: their faces stacked (three, then +N), the line beside them.
        case stack([String])
        /// No subject: the line leads.
        case none
    }

    let head: Head
    /// The item the one subject is (its change beat plays on the head's face); never drawn again as a row.
    let subjectItem: Int?
    /// A decision card (A1): pictures, no visual, at most one item. The pictures are the card, the line its only words.
    let hero: Bool
    /// The visual is one this build draws and has something to show.
    let visual: Bool
    /// Item indexes drawn as text rows, in order.
    let rows: [Int]
    /// Moved items (by index) to their change spring: their rank among the moved, in item order.
    let changes: [Int: Int]
    /// Pictures drawn: the card's labels and the row's paths, aligned.
    let images: Int
    /// The card's one action, when it is one this build knows.
    let action: CardAction?
    let times: CardTimes

    /// The server keeps a card to 8 items.
    static let maxItems = 8

    init(_ c: ChatCard, images paths: Int) {
        let items = Array(c.items.prefix(Self.maxItems))
        let subject: String? = !c.face.isEmpty ? c.face : (items.count == 1 ? items[0].ref : nil)
        let own = subject.flatMap { s in items.firstIndex { $0.ref == s } }
        let pictures = min(c.images.count, paths)
        let decision = pictures > 0 && c.visual == nil && items.count <= 1
        let shown = decision ? [] : items.indices.filter { $0 != own }
        var moved: [Int: Int] = [:]
        for i in items.indices where (shown.contains(i) || i == own) && Self.moved(items[i]) { moved[i] = moved.count }
        let act = c.action.flatMap { $0.kind == "tap_accept" && !$0.tap.isEmpty ? $0 : nil }
        let draws = Self.draws(c.visual)
        let pills = draws && c.visual?.kind == "timeline" ? min(c.visual?.steps.count ?? 0, 7) : 0
        head = subject.map(Head.one) ?? (items.count > 1 ? .stack(items.map(\.ref)) : .none)
        subjectItem = own
        hero = decision
        visual = draws
        rows = shown
        changes = moved
        images = pictures
        action = act
        times = CardTimes(items: shown.count, changed: moved.count, images: pictures + (act == nil ? 0 : 1), pills: pills)
    }

    /// A visual this build draws, with something in it (anything else draws nothing).
    static func draws(_ v: CardVisual?) -> Bool {
        guard let v else { return false }
        switch v.kind {
        case "progress": return v.total >= 1
        case "timeline": return !v.steps.isEmpty
        case "graph": return v.points.count >= 2
        case "word": return !v.text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        default: return false
        }
    }

    /// An item moved since the thread's previous card (P7's rule, plus a master's state alone: CONTRACT section 12).
    /// Projects never move.
    static func moved(_ it: CardItem) -> Bool {
        guard let w = it.was, it.kind != "project" else { return false }
        if CardMotion.moved(wasPhase: w.phase, phase: it.phase, wasGoals: w.goals.isEmpty ? nil : w.goals, goals: it.goals,
                            wasCurrent: w.current, current: it.progress?.current) { return true }
        if let s = w.state, s != it.state { return true }
        return false
    }

    /// A progress visual's fill, where it starts (the previous card's) and ends, 0...1.
    static func fill(_ v: CardVisual) -> (from: Double, to: Double) {
        guard v.total > 0 else { return (0, 0) }
        let t = Double(v.total)
        return (clamp01(Double(v.from ?? 0) / t), clamp01(Double(v.done) / t))
    }

    /// A title as Hayden reads it: "[tap] Fix the thing" -> "Fix the thing" (Tap.name's rule).
    static func plain(_ s: String) -> String {
        s.replacingOccurrences(of: #"^\[[^\]]*\]\s*"#, with: "", options: .regularExpression)
            .trimmingCharacters(in: .whitespacesAndNewlines)
    }
}

// MARK: - The head

/// The top of a card (CONTRACT section 13): its one subject (the face, its name, the line under it), the items'
/// faces stacked with the line, or the line alone. On a decision card the line is the only words.
struct CardHeader: View {
    let row: CardRow
    let layout: CardLayout
    let beats: CardBeats
    let still: Bool
    let store: Store

    var body: some View {
        let c = row.card
        switch layout.head {
        case .one(let key):
            let name = layout.hero ? "" : CardFace.title(key, info: c.faces[key], item: layout.subjectItem.map { c.items[$0] },
                                                          store: store)
            HStack(alignment: layout.hero || name.isEmpty ? .center : .top, spacing: 10) {
                subject(key, c).modifier(CardPop(box: beats.face))
                VStack(alignment: .leading, spacing: 2) {
                    if !name.isEmpty {
                        Text(verbatim: name)
                            .font(.system(size: 15, weight: .semibold))
                            .tracking(-0.15)
                            .foregroundStyle(Color.ink)
                            .lineLimit(1)
                    }
                    CardLine(text: c.line, lead: name.isEmpty)
                }
                .modifier(CardRise(box: beats.line))
            }
        case .stack(let keys):
            HStack(alignment: .center, spacing: 10) {
                CardFaceStack(keys: keys, faces: c.faces, store: store).modifier(CardPop(box: beats.face))
                CardLine(text: c.line, lead: true).modifier(CardRise(box: beats.line))
            }
        case .none:
            CardLine(text: c.line, lead: true).modifier(CardRise(box: beats.line))
        }
    }

    /// The subject's face. When its item moved, it shows what it was until its change beat, then pops to now.
    @ViewBuilder private func subject(_ key: String, _ c: ChatCard) -> some View {
        if let i = layout.subjectItem, let k = layout.changes[i] {
            let item = c.items[i]
            CardItemFace(box: beats.changes[k], still: still) { now in
                CardFace(key: key, size: 32, info: c.faces[key],
                         state: item.state.map { now ? $0 : (item.was?.state ?? $0) },
                         phase: (now ? item.phase : (item.was?.phase ?? item.phase)).flatMap(TapPhase.init(rawValue:)),
                         store: store)
            }
        } else {
            CardFace(key: key, size: 32, info: c.faces[key], store: store)
        }
    }
}

/// The card's line: the lead words in the header's voice, or under a subject's name in the secondary one.
struct CardLine: View {
    let text: String
    let lead: Bool

    var body: some View {
        Text(verbatim: text)
            .font(.system(size: lead ? 15 : 13, weight: lead ? .semibold : .regular))
            .tracking(lead ? -0.15 : 0)
            .foregroundStyle(lead ? Color.ink : Color.dim)
            .lineSpacing(2)
            .fixedSize(horizontal: false, vertical: true)
            .textSelection(.enabled)
    }
}

/// A face inside a card: the chat's one resolver (FaceView). `state` and `phase` pin it to the card's snapshot (a
/// change beat); nil shows the store's live face, or the snapshot's for a key the store no longer holds.
struct CardFace: View {
    let key: String
    let size: CGFloat
    let info: CardFaceInfo?
    var state: String? = nil
    var phase: TapPhase? = nil
    var store: Store = .shared

    var body: some View {
        FaceView(key: key, size: size, info: info,
                 state: state ?? (store.agent(key) == nil ? info?.state : nil),
                 phase: phase ?? (store.tap(key) == nil ? info?.phase.flatMap(TapPhase.init(rawValue:)) : nil),
                 store: store)
    }

    /// What Hayden calls a face key: the store's live title, else the card's snapshot, else a project's name; ""
    /// when nothing plain is known (a key is never shown).
    static func title(_ key: String, info: CardFaceInfo?, item: CardItem? = nil, store: Store) -> String {
        if let a = store.agent(key) { return a.title }
        if let t = store.tap(key) { return t.name }
        for s in [item?.title ?? "", info?.title ?? ""] where !CardLayout.plain(s).isEmpty { return CardLayout.plain(s) }
        if key.hasPrefix("project:") { return String(key.dropFirst(8).split(separator: "/").last ?? "") }
        return ""
    }
}

/// Several subjects: their faces overlapped, the first in front, at most three, then "+N" in the count voice.
struct CardFaceStack: View {
    let keys: [String]
    let faces: [String: CardFaceInfo]
    let store: Store
    static let shown = 3
    static let size: CGFloat = 26

    var body: some View {
        HStack(spacing: 6) {
            HStack(spacing: -8) {
                ForEach(Array(keys.prefix(Self.shown).enumerated()), id: \.offset) { i, k in
                    // A white rim parts each face from the one behind it; a project keeps its squircle.
                    let rim = k.hasPrefix("project:")
                        ? AnyShape(RoundedRectangle(cornerRadius: (Self.size + 4) * 0.32, style: .continuous))
                        : AnyShape(Circle())
                    CardFace(key: k, size: Self.size, info: faces[k], store: store)
                        .padding(2)
                        .background(rim.fill(Color.white))
                        .zIndex(Double(Self.shown - i))
                }
            }
            if keys.count > Self.shown {
                Text(verbatim: "+\(keys.count - Self.shown)")
                    .font(.system(size: 11, weight: .semibold))
                    .monospacedDigit()
                    .foregroundStyle(Color.dim)
            }
        }
        .accessibilityHidden(true)
    }
}

// MARK: - Answers

/// The one way a card's buttons reach TapSpace: an answer row (POST /api/chat, shown at once and rolled back on a
/// refusal) or the card's Accept (the tap's guarded accept route with this card's id).
@MainActor
enum CardSend {
    @discardableResult
    static func answer(_ a: ChatAnswer, to agent: String, store: Store? = nil) async -> Bool {
        await CardAnswers.shared.send(a, to: agent, store: store ?? .shared)
    }

    static func accept(tap key: String, card: Int, store: Store) async -> Bool {
        await store.accept(tap: key, card: card)
    }
}
