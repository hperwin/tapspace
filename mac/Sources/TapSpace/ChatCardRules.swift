import Foundation

// Update cards in the Chat tab: the pure rules (no views). How answers fold into their card, what a face key
// names, the look table, and the small sums the card's parts draw from. The beat times are CardTimes
// (ChatCardMotion.swift).

/// A card row as the thread draws it: the card, its row id and NAME (answers name them), what Hayden answered.
struct CardRow: Equatable {
    let card: ChatCard
    let id: Int
    let agent: String
    var state = CardState()
}

/// What Hayden has answered on one card: the latest verdict per image, the latest ask answer, the card's action,
/// and which of them are still on their way (shown at once, rolled back on a refusal).
struct CardState: Equatable {
    struct Verdict: Equatable {
        var approve: Bool
        var note = ""
        var sending = false
    }
    var images: [Int: Verdict] = [:]
    var asked = false
    var checks: Set<String> = []
    var text = ""
    var askSending = false
    /// Accept (`approve`) or Not quite with its note, on a card whose action is a tap's Accept.
    var action: Verdict? = nil

    mutating func apply(_ a: ChatAnswer, sending: Bool) {
        if !a.action.isEmpty {
            action = Verdict(approve: a.action == "accept", note: a.note, sending: sending)
        } else if let i = a.image {
            images[i] = Verdict(approve: a.verdict == "approve", note: a.note, sending: sending)
        } else {
            asked = true
            checks = Set(a.checks)
            text = a.text
            askSending = sending
        }
    }

    /// Changes that move the card (an answer landing or rolling back), for the list's signature.
    var sig: String {
        images.keys.sorted().map { k in
            let v = images[k]!
            return "\(k)\(v.approve ? "a" : "c")\(v.sending ? "~" : "")"
        }.joined() + (asked ? "q" + (askSending ? "~" : "") : "")
            + (action.map { ($0.approve ? "A" : "D") + ($0.sending ? "~" : "") } ?? "")
    }
}

/// One answer still on its way (CardAnswers), matched to its stored row by nonce.
struct PendingAnswer: Equatable, Identifiable {
    let nonce: String
    let agent: String
    let answer: ChatAnswer
    var id: String { nonce }

    /// Its row is in the thread: the same nonce, or (an Accept, whose row the server writes without one) the
    /// same answer.
    func landed(in rows: [ChatMessage]) -> Bool {
        rows.contains { $0.role == "you" && ($0.nonce == nonce || $0.answer == answer) }
    }
}

/// A thread's rows with its cards' answers folded in (CONTRACT section 3). Rows are in id order, so a later
/// answer wins per image, per ask and per action; answers still on their way go on top unless their row has landed.
struct CardFold {
    let cards: Set<Int>
    private let states: [Int: CardState]

    init(rows: [ChatMessage], pending: [PendingAnswer]) {
        let cards = Set(rows.filter { $0.role == "master" && $0.card != nil }.map(\.id))
        var st: [Int: CardState] = [:]
        for r in rows where r.role == "you" {
            if let a = r.answer, cards.contains(a.card) { st[a.card, default: CardState()].apply(a, sending: false) }
        }
        for p in pending where cards.contains(p.answer.card) && !p.landed(in: rows) {
            st[p.answer.card, default: CardState()].apply(p.answer, sending: true)
        }
        self.cards = cards
        states = st
    }

    /// An answer row whose card is in the thread: drawn inside that card, never as a bubble.
    func hides(_ m: ChatMessage) -> Bool {
        m.role == "you" && m.answer.map { cards.contains($0.card) } == true
    }

    func state(_ card: Int) -> CardState { states[card] ?? CardState() }
}

extension ChatAnswer {
    /// "540:i0:approve:1a2b3c4d", "541:ask:1a2b3c4d" or "542:deny:1a2b3c4d" (CONTRACT section 2; well under the
    /// server's 64 characters).
    func makeNonce(_ salt: String = UUID().uuidString) -> String {
        let slot = action.isEmpty ? (image.map { "i\($0):\(verdict)" } ?? "ask") : action
        return "\(card):\(slot):" + salt.prefix(8).lowercased()
    }

    /// The same image of the same card, both the card's ask, or both its action: the later answer replaces the
    /// earlier one.
    func sameSlot(_ o: ChatAnswer) -> Bool { card == o.card && image == o.image && action.isEmpty == o.action.isEmpty }
}

/// A face key: "agent:NAME" a master (the lead too), "project:name" a project, anything else a tap
/// ("owner/repo#n"). Hidden: never shown.
enum FaceKey {
    enum Kind: Equatable { case master, tap, project }

    static func kind(_ key: String) -> Kind {
        if key.hasPrefix("agent:") { return .master }
        if key.hasPrefix("project:") { return .project }
        return .tap
    }

    /// A tap's or project's letter when nothing served one ("SnappStats/snapp-ios#1583" -> "S",
    /// "project:tapspace" -> "T").
    static func initial(_ key: String) -> String { letter(key.hasPrefix("project:") ? String(key.dropFirst(8)) : key) }

    /// The server's one rule (`_initial`): the first letter or digit after the last "/", upper case; "?" when
    /// there is none. ".github" -> "G", "owner/_9lives" -> "9".
    static func letter(_ name: String) -> String {
        let last = name.split(separator: "/", omittingEmptySubsequences: false).last.map(String.init) ?? ""
        return last.first { $0.isLetter || $0.isNumber }.map { String($0).uppercased() } ?? "?"
    }
}

/// A master's look (R7): its tone (0 accent, 1 ink, 2 tint) and its mark (0 plain, 1 glasses, 2 antenna,
/// 3 bowtie, 4 cheeks, 5 brows). P2's table (spec/faces-data.json "looks"), so every client draws look N alike.
struct FaceLook: Equatable {
    let tone: Int
    let mark: Int

    static let table: [(tone: Int, mark: Int)] = [(0, 0), (0, 1), (1, 2), (2, 3), (0, 2), (1, 3), (2, 4), (0, 3), (1, 4),
                                                  (2, 5), (0, 4), (1, 5), (2, 1), (0, 5), (1, 1), (2, 2), (1, 0), (2, 0)]
}

extension FaceLook {
    /// The server serves 0...17 (0 is the lead's); anything else draws as look 0.
    init(_ look: Int) {
        let t = Self.table.indices.contains(look) ? Self.table[look] : Self.table[0]
        self.init(tone: t.tone, mark: t.mark)
    }
}

/// A master's expression is its state (Glint's AgentAvatar.face, test-pinned there): here happy, thinking
/// focused, starting or switching wow, anything else sleepy.
enum FaceExpression: Equatable {
    case happy, focused, wow, sleepy

    static func of(_ state: String) -> FaceExpression {
        switch state {
        case "here": .happy
        case "thinking": .focused
        case "starting", "switching": .wow
        default: .sleepy
        }
    }
}

extension CardVisual {
    /// progress: where the fill starts and ends (0...1).
    var fill: (from: Double, to: Double) {
        guard total > 0 else { return (0, 0) }
        let t = Double(total)
        return (min(1, Double(max(0, from ?? 0)) / t), min(1, Double(max(0, done)) / t))
    }

    /// graph: the points scaled into 0...1 between their least and greatest; a flat series sits at 0.5.
    var unit: [Double] {
        guard let lo = points.min(), let hi = points.max() else { return [] }
        return hi > lo ? points.map { ($0 - lo) / (hi - lo) } : points.map { _ in 0.5 }
    }
}

extension CardItem {
    var nowPhase: TapPhase? { phase.flatMap(TapPhase.init(rawValue:)) }
    var wasPhase: TapPhase? { was?.phase.flatMap(TapPhase.init(rawValue:)) }

    /// It moved since the previous card: its change beat plays (was -> now). A master whose state alone moved
    /// counts (CONTRACT 12: its face pops to the new expression).
    var changed: Bool {
        guard let was else { return false }
        if let w = wasPhase, w != nowPhase { return true }
        if !was.goals.isEmpty, was.goals != goals { return true }
        if let c = was.current, c != progress?.current { return true }
        if let s = was.state, s != state { return true }
        return false
    }

    /// The StatusChip state for a tap phase (Tap.chipState's table, MuddiRules.swift:81-90; a snapshot has no
    /// `replying`, so en route is the still chip).
    static func chip(_ p: TapPhase) -> String {
        switch p {
        case .holding: "needs-you"
        case .enroute: "enroute"
        case .landed, .done: "landed"
        case .proposed: "boarding"
        case .closed: "closed"
        }
    }
}
