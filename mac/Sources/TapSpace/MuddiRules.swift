import Foundation

// Muddi in the Chat tab: the pure rules, Foundation only (no views), so a check
// binary can compile them with API.swift alone. The lead (Muddi) heads the
// rail and its children sit under it; a tap's phase picks its word, its chip
// state and the card at the top of its thread. With no lead, no parent and no
// phase in the payloads every rule gives today's answer.

/// The rail's master order: the lead first, then its children, then the rest.
enum MuddiOrder {
    struct Row: Equatable {
        let agent: Agent
        /// A master that reports to the lead: drawn indented under it.
        let child: Bool
    }

    /// How far a child's row, its talk line and its note rows move in.
    static let indent: CGFloat = 20

    /// The first row marked lead, in the server's order (the server keeps one).
    static func lead(_ agents: [Agent]) -> Agent? { agents.first(where: \.isLead) }

    /// No lead: every row as served. A lead: the lead, then the rows whose parent
    /// is the lead, then everything else, each block in the server's order
    /// (working first, then newest). The lead is never a child, and a parent
    /// naming anyone but the lead leaves the row with the rest.
    static func rows(_ agents: [Agent]) -> [Row] {
        guard let lead = lead(agents) else { return agents.map { Row(agent: $0, child: false) } }
        var children: [Row] = []
        var rest: [Row] = []
        for a in agents where a.key != lead.key {
            if a.parent == lead.name { children.append(Row(agent: a, child: true)) } else { rest.append(Row(agent: a, child: false)) }
        }
        return [Row(agent: lead, child: false)] + children + rest
    }

    /// With a lead, every other master and every tap folds under it behind one
    /// disclosure (closed by default): their thread keys are off the rail while
    /// it is closed. No lead, or open: none.
    static func folded(_ agents: [Agent], taps: [Tap], open: Bool) -> [String] {
        guard let lead = lead(agents), !open else { return [] }
        return agents.filter { $0.key != lead.key }.map(\.key) + taps.map(\.key)
    }
}

/// Hayden's five tap states plus closed: a done tap aborted or closed without a sign-off (and an older server's
/// closed), shown greyed.
enum TapPhase: String {
    case proposed, enroute, holding, landed, done, closed

    /// The word every client shows when the server sends no phase_word.
    var word: String {
        switch self {
        case .proposed: "Proposed"
        case .enroute: "Flying"
        case .holding: "Needs you"
        case .landed: "Landed"
        case .done: "Done"
        case .closed: "Closed"
        }
    }

    /// A finished tap's look by how it ended: shipped is done; aborted or closed is closed; anything else nil.
    init?(outcome: String?) {
        switch outcome {
        case "shipped": self = .done
        case "aborted", "closed": self = .closed
        default: return nil
        }
    }
}

/// The card a tap's thread shows under its goals: an answer to give, proof to
/// accept, or a flight to clear.
enum MuddiCardKind: Equatable { case holding, landed, proposed }

/// One usable proof image: a TapSpace proxy path, ready for the chat image loader.
struct MuddiProofItem: Equatable, Identifiable {
    let path: String
    let alt: String
    var id: String { path }
}

/// The words an older server's `status` maps to.
let muddiLegacyWords = ["needs-you": "Needs you", "flying": "Flying", "boarding": "Boarding", "landed": "Landed"]

extension Tap {
    /// nil from an older server or an unknown spelling: the legacy look stays. A done tap that was aborted or
    /// closed reads closed.
    var tapPhase: TapPhase? {
        let p = phase.flatMap(TapPhase.init(rawValue:))
        return p == .done ? TapPhase(outcome: outcome) ?? p : p
    }

    /// It waits on Hayden: the board holds on a question, or a pilot asked in a comment (status needs-you, which an
    /// older server sends exactly while holding). Its ring and chip still follow the board's phase.
    var waits: Bool { tapPhase == .holding || status == "needs-you" }

    /// The server's word, else the phase's word, else the legacy word, else the raw status.
    var phaseLabel: String {
        if let w = phaseWord, !w.isEmpty { return w }
        if let p = tapPhase { return p.word }
        return muddiLegacyWords[status] ?? status
    }

    /// The state string StatusChip colors by. No phase: today's status.
    var chipState: String {
        guard let p = tapPhase else { return status }
        switch p {
        case .holding: return "needs-you"
        case .enroute: return replying == true ? "flying" : "enroute"
        case .landed, .done: return "landed"
        case .proposed: return "boarding"
        case .closed: return "closed"
        }
    }

    /// Which card the thread shows, if any: the question whenever it waits on Hayden, else by phase (enroute, done,
    /// closed and no phase: none).
    var cardKind: MuddiCardKind? {
        guard let p = tapPhase else { return nil }
        if waits { return .holding }
        switch p {
        case .landed: return .landed
        case .proposed: return .proposed
        default: return nil
        }
    }

    /// Proof images that go through TapSpace's proxy, raw "#" made "%23" (a raw
    /// "#" would cut the key off as a fragment), duplicates dropped. Anything
    /// else, like a GitHub attachment URL that needs a token, is never requested.
    var proofItems: [MuddiProofItem] {
        var seen = Set<String>()
        var out: [MuddiProofItem] = []
        for p in proof {
            let path = p.url.replacingOccurrences(of: "#", with: "%23")
            guard path.hasPrefix("/api/taps/proof?"), seen.insert(path).inserted else { continue }
            out.append(MuddiProofItem(path: path, alt: p.alt))
        }
        return out
    }

    /// Hayden has replied to the question it waits on: one of his comments, or a
    /// bubble still on its way (`pending`: their timestamps), is newer than the
    /// ask. With no usable time on the ask: his is the newest message, or one
    /// is on its way. False without an ask.
    func askAnswered(pending: [Double]) -> Bool {
        guard let ask else { return false }
        let at = ask.date
        if at > 0 {
            return messages.contains { $0.who == "you" && $0.date > at } || pending.contains { $0 > at }
        }
        return messages.last?.who == "you" || !pending.isEmpty
    }
}

extension TapsResponse {
    /// A tap's phase for its face and its card: the listed tap's while it is listed, else its recorded ending's;
    /// nil when neither knows the key.
    func tapPhase(_ key: String) -> TapPhase? {
        if let t = items.first(where: { $0.key == key }) { return t.tapPhase }
        return TapPhase(outcome: outcomes[key])
    }
}
