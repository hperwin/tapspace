import AppKit
import SwiftUI

// The Chat tab's third column: one card per live master with its run's goal,
// its steps and where it is now. Same rules as Glint's progress board (stable
// order by when each master joined, talking pairs side by side), so the two
// surfaces order and pair the cards alike. Ambient motion (the face, the
// current step, the link's chase) runs on Core Animation; springs move only
// when something changes.

/// Who gets a card, in what order, and which pairs are talking. Pure rules.
@MainActor
enum ChatProgressBoard {
    struct Row: Equatable, Identifiable {
        let agent: Agent
        /// Set on the upper card of a talking pair: the link drawn in the gap below it.
        var linkBelow: AgentLink?
        var id: String { agent.key }
    }

    /// A master keeps its card this long after it last listened or replied (the
    /// server calls a lapsed listener away after 30 s; this stops the flicker).
    static let liveHold: Double = 600
    /// A link is drawn while the server calls it active and its newest message
    /// is this fresh (re-checked here: time passes between polls).
    static let linkWindow: Double = 600
    static let working: Set<String> = ["thinking", "switching", "starting"]

    /// The master's published plan; an empty one (no goal, no steps) counts as none.
    static func progress(_ a: Agent) -> AgentProgress? {
        guard let p = a.progress, !(p.goal.isEmpty && p.steps.isEmpty) else { return nil }
        return p
    }

    /// Working or listening now, or heard from within `liveHold`.
    static func isLive(_ a: Agent, now: Double) -> Bool {
        working.contains(a.state) || a.state == "here" || now - max(a.seen, a.last_master ?? 0) < liveHold
    }

    /// Gets a card: live, or it published a goal.
    static func onBoard(_ a: Agent, now: Double) -> Bool { progress(a) != nil || isLive(a, now: now) }

    /// The column: cards in a stable order (when each master joined, then its
    /// name; never by state or recency), each talking pair pulled together
    /// under its earlier member, the link on the upper row. A pair needs the
    /// server's `active`, a newest message within `linkWindow`, two different
    /// masters that both hold a card, and at most one partner per master (the
    /// newest link wins), so every drawn pair is adjacent.
    static func rows(agents: [Agent], links: [AgentLink], now: Double) -> [Row] {
        let cards = agents.filter { onBoard($0, now: now) }
            .sorted { ($0.first ?? 0, $0.name) < ($1.first ?? 0, $1.name) }
        let names = Set(cards.map(\.name))
        let talking = links
            .filter { $0.active && now - $0.last < linkWindow && $0.a != $0.b && names.contains($0.a) && names.contains($0.b) }
            .sorted { ($0.last, $0.a, $0.b) > ($1.last, $1.a, $1.b) }
        var partner: [String: AgentLink] = [:]
        for l in talking where partner[l.a] == nil && partner[l.b] == nil {
            partner[l.a] = l
            partner[l.b] = l
        }
        var out: [Row] = []
        var placed = Set<String>()
        for a in cards where !placed.contains(a.name) {
            placed.insert(a.name)
            guard let l = partner[a.name], let b = cards.first(where: { $0.name == l.other(a.name) }),
                  !placed.contains(b.name) else {
                out.append(Row(agent: a))
                continue
            }
            placed.insert(b.name)
            out.append(Row(agent: a, linkBelow: l))
            out.append(Row(agent: b))
        }
        return out
    }

    /// The receiving card of each drawn pair and when its newest message
    /// landed: that card nods when the time grows.
    static func pings(_ rows: [Row]) -> [String: Double] {
        var out: [String: Double] = [:]
        for r in rows { if let l = r.linkBelow { out[l.to] = l.last } }
        return out
    }

    /// "3 of 5" and "Build"; "Done" and "5 of 5" once every step is done; nil
    /// without steps. Below 0 reads as the first step.
    static func stepLine(_ p: AgentProgress) -> (count: String, step: String)? {
        let n = p.steps.count
        guard n > 0 else { return nil }
        if p.current >= n { return ("Done", "\(n) of \(n)") }
        let i = max(0, p.current)
        return ("\(i + 1) of \(n)", oneLine(p.steps[i]))
    }

    /// One line of plain words (a goal, a step or a summary may carry newlines).
    static func oneLine(_ s: String) -> String {
        s.split(whereSeparator: \.isNewline)
            .map { $0.trimmingCharacters(in: .whitespaces) }
            .filter { !$0.isEmpty }
            .joined(separator: " ")
    }

    /// When the card last moved: its plan or the master's last reply (else when it was seen).
    static func updated(_ a: Agent, _ p: AgentProgress?) -> Double {
        let t = max(p?.updated ?? 0, a.last_master ?? 0)
        return t > 0 ? t : a.seen
    }

    /// The face's status dot: working breathes blue, here is green, anything else faint.
    static func dot(_ state: String) -> (color: NSColor, breathe: Bool) {
        if working.contains(state) { return (Palette.blue, true) }
        return (state == "here" ? Palette.green : Palette.faint, false)
    }

    static func stateWord(_ state: String) -> String {
        ["thinking": "Thinking", "here": "Here", "away": "Away", "switching": "Switching car",
         "starting": "Starting"][state] ?? state
    }

    /// VoiceOver for a card: "Glint app, Thinking, goal: Two tabs, step 3 of 5, Build, unread".
    static func spoken(_ a: Agent, _ p: AgentProgress?, unread: Bool) -> String {
        var parts = [a.title, stateWord(a.state)]
        let goal = oneLine(p?.goal ?? "")
        if !goal.isEmpty { parts.append("goal: " + goal) } else if p == nil { parts.append("no goal yet") }
        if let p, let s = stepLine(p) {
            parts.append(s.count == "Done" ? "all \(p.steps.count) steps done" : "step " + s.count + ", " + s.step)
        }
        if unread { parts.append("unread") }
        return parts.joined(separator: ", ")
    }
}

/// The third column on the Chat tab. ChatView hosts it as
/// `ChatProgressPanel()` in a 288 pt column; it fills that column top
/// aligned and scrolls itself.
struct ChatProgressPanel: View {
    var store: Store = .shared
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    /// --snapshot renders the column unscrolled: cacheDisplay cannot see
    /// inside a SwiftUI ScrollView (VisualView's grid does the same).
    static let unscrolled = CommandLine.arguments.contains("--snapshot")
    /// How often the column re-reads the clock (who is live, link expiry, "4m").
    static let tick: Double = 15

    var body: some View {
        TimelineView(.periodic(from: .now, by: Self.tick)) { ctx in
            let list = column(now: ctx.date.timeIntervalSince1970)
            if Self.unscrolled {
                list.frame(maxHeight: .infinity, alignment: .top).clipped()
            } else {
                ScrollView(.vertical) { list }
                    .scrollIndicators(.never)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
        .background {
            if HarnessLink.asked {
                Color.clear.onGeometryChange(for: CGRect.self, of: { $0.frame(in: .global) }) { HarnessLink.shared.panel = $0 }
            }
        }
    }

    private func column(now: Double) -> some View {
        let rows = ChatProgressBoard.rows(agents: store.agents, links: links, now: now)
        let titles = Dictionary(store.agents.map { ($0.name, $0.title) }, uniquingKeysWith: { first, _ in first })
        let pings = ChatProgressBoard.pings(rows)
        let pulse = linkMotion
        let still = reduceMotion
        return VStack(alignment: .leading, spacing: 0) {
            RailHead(title: "Progress")
                .padding(.bottom, 6)
            if rows.isEmpty {
                RailEmpty(text: store.agentsLoaded ? "No agents working right now." : "Looking for agents…")
            }
            ForEach(Array(rows.enumerated()), id: \.element.id) { i, r in
                let a = r.agent
                let plan = ChatProgressBoard.progress(a)
                VStack(spacing: 0) {
                    ChatProgressCard(agent: a, progress: plan, unread: store.isUnread(a.key), now: now, still: still) {
                        store.open(a.key)
                    }
                    .modifier(ChatNod(step: plan?.current, pinged: pings[a.name] ?? 0, still: still))
                    ChatProgressGap(link: r.linkBelow, upper: a.name, titles: titles, motion: pulse, now: now)
                }
                .modifier(RailArrival(index: i))
                .transition(.asymmetric(insertion: .identity, removal: .opacity))
            }
        }
        .padding(.horizontal, 12)
        .padding(.top, 2)
        .padding(.bottom, 20)
        // Cards coming or going and pairs forming or parting glide (the rail's spring).
        .animation(.spring(response: 0.5, dampingFraction: 0.82), value: rows.map(\.id))
    }

    /// Links hide while the server is unreachable (they could be stale).
    private var links: [AgentLink] {
        HarnessLink.shared.links ?? (store.connection == .live ? store.links : [])
    }

    /// The chase runs only while someone can see it: the Chat tab up, the
    /// window on screen, no lightbox over the chat. Reduce Motion: a still line.
    private var linkMotion: ChatLinkMotion {
        if reduceMotion { return .still }
        let seen = store.tab == .chat && store.windowVisible && ChatMedia.shared.lightbox.path == nil
        return seen ? .run : .off
    }
}

/// One master: face with its status dot, title, updated time and unread dot;
/// the north star (two lines) or "No goal yet"; the step track; "3 of 5 ·
/// Build". Clicking opens its thread (which also reads its card notes).
struct ChatProgressCard: View {
    let agent: Agent
    let progress: AgentProgress?
    let unread: Bool
    let now: Double
    let still: Bool
    let open: () -> Void
    @State private var hover = false

    var body: some View {
        let shape = RoundedRectangle(cornerRadius: 18, style: .continuous)
        let goal = ChatProgressBoard.oneLine(progress?.goal ?? "")
        Button(action: open) {
            HStack(alignment: .top, spacing: 10) {
                ChatProgressFace(state: agent.state, still: still)
                VStack(alignment: .leading, spacing: 0) {
                    HStack(spacing: 6) {
                        Text(agent.title)
                            .font(.system(size: 13, weight: .semibold))
                            .foregroundStyle(Color.ink)
                            .lineLimit(1)
                        Spacer(minLength: 6)
                        Text(when(ChatProgressBoard.updated(agent, progress), now: now))
                            .font(.system(size: 10.5))
                            .tracking(0.2)
                            .foregroundStyle(Color.faint)
                        UnreadDot(on: unread)
                    }
                    .frame(minHeight: 26)
                    if !goal.isEmpty {
                        Text(goal)
                            .font(.system(size: 12.5))
                            .foregroundStyle(Color.ink)
                            .lineSpacing(2)
                            .lineLimit(2)
                            .fixedSize(horizontal: false, vertical: true)
                            .padding(.top, 1)
                    } else if progress == nil {
                        Text("No goal yet")
                            .font(.system(size: 12))
                            .foregroundStyle(Color.faint)
                            .padding(.top, 1)
                    }
                    if let p = progress, !p.steps.isEmpty {
                        ChatStepTrack(count: p.steps.count, current: p.current,
                                      working: ChatProgressBoard.working.contains(agent.state), still: still)
                            .padding(.top, 10)
                        ChatStepLine(progress: p)
                            .padding(.top, 7)
                    }
                }
            }
            .padding(14)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(shape.fill(Color.white).modifier(ChatCardLift(k: hover ? 1.4 : 1)))
            .contentShape(shape)
        }
        .buttonStyle(ChatPress(scale: 0.98))
        .onHover { h in withAnimation(.easeOut(duration: 0.2)) { hover = h } }
        .help(help(goal))
        .accessibilityElement(children: .combine)
        .accessibilityLabel(ChatProgressBoard.spoken(agent, progress, unread: unread))
        .accessibilityHint("Opens the chat")
    }

    /// The goal in full (the card clips at two lines), then the step's note.
    private func help(_ goal: String) -> String {
        let note = ChatProgressBoard.oneLine(progress?.note ?? "")
        return [goal.isEmpty ? agent.title : goal, note].filter { !$0.isEmpty }.joined(separator: "\n\n")
    }
}

/// The 26 pt orb (its own state motion on Core Animation) with a status dot
/// at its lower right on a white ring.
struct ChatProgressFace: View {
    let state: String
    let still: Bool

    var body: some View {
        let dot = ChatProgressBoard.dot(state)
        OrbView(state: state)
            .frame(width: 26, height: 26)
            .overlay(alignment: .bottomTrailing) {
                BreathingDot(color: dot.color, period: 1.7, low: 0.45, animated: dot.breathe && !still)
                    .frame(width: 7, height: 7)
                    .padding(1.5)
                    .background(Circle().fill(Color.white))
                    .offset(x: 2, y: 2)
            }
    }
}

/// One capsule per step: done in ink, the current one accent and taller
/// (breathing on Core Animation while the master works), the rest faint; all
/// accent once every step is done. More than 16 steps: one bar and a knob.
struct ChatStepTrack: View {
    let count: Int
    let current: Int
    let working: Bool
    let still: Bool

    var body: some View {
        let done = current >= count
        let at = max(0, current)
        Group {
            if count > 16 {
                GeometryReader { g in
                    let x = g.size.width * CGFloat(min(at, count)) / CGFloat(max(count, 1))
                    ZStack(alignment: .leading) {
                        Capsule().fill(Color.black.opacity(0.08)).frame(height: 4)
                        Capsule().fill(done ? Color.tapBlue : Color.ink).frame(width: max(4, x), height: 4)
                        if !done {
                            Circle().fill(Color.tapBlue)
                                .frame(width: 8, height: 8)
                                .offset(x: min(max(0, x - 4), max(0, g.size.width - 8)))
                        }
                    }
                    .frame(maxHeight: .infinity)
                }
            } else {
                HStack(spacing: 3) {
                    ForEach(0..<count, id: \.self) { i in
                        segment(i, done: done, at: at)
                            .frame(maxWidth: .infinity)
                    }
                }
            }
        }
        .frame(height: 8)
        .animation(SpringPreset.smooth.animation, value: current)
    }

    @ViewBuilder private func segment(_ i: Int, done: Bool, at: Int) -> some View {
        if done {
            Capsule().fill(Color.tapBlue).frame(height: 4)
        } else if i < at {
            Capsule().fill(Color.ink).frame(height: 4)
        } else if i == at {
            BreathingDot(color: Palette.blue, period: 1.7, low: 0.45, animated: working && !still)
                .frame(height: 7)
        } else {
            Capsule().fill(Color.black.opacity(0.08)).frame(height: 4)
        }
    }
}

/// "3 of 5" in ink, " · Build" dim; "Done · 5 of 5" once every step is done.
struct ChatStepLine: View {
    let progress: AgentProgress

    var body: some View {
        if let s = ChatProgressBoard.stepLine(progress) {
            Text(Self.text(s.count, s.step))
                .font(.system(size: 11.5))
                .monospacedDigit()
                .lineLimit(1)
                .truncationMode(.tail)
                .contentTransition(.numericText(value: Double(progress.current)))
                .animation(SpringPreset.smooth.animation, value: progress.current)
        }
    }

    static func text(_ count: String, _ step: String) -> AttributedString {
        var lead = AttributedString(count)
        lead.foregroundColor = Color.ink
        var rest = AttributedString(" · " + step)
        rest.foregroundColor = Color.dim
        lead.append(rest)
        return lead
    }
}

/// The column's card lift: lighter than the pane's softShadow (a column of
/// pane shadows reads heavy, and its 32 pt spread would clip at the scroll
/// edge). Hover deepens it a little.
struct ChatCardLift: ViewModifier {
    var k: Double = 1

    func body(content: Content) -> some View {
        content
            .shadow(color: .black.opacity(0.05 * k), radius: 1, x: 0, y: 1)
            .shadow(color: .black.opacity(0.07 * k), radius: 9, x: 0, y: 6)
    }
}

/// A small nod when the card's step moves or a message reaches it on a drawn
/// link. Isolated so its per-frame spring re-renders only this wrapper; it
/// never scales toward 0 (an OrbView rides inside, CONTRACT.md).
struct ChatNod: ViewModifier {
    let step: Int?
    let pinged: Double
    let still: Bool
    @State private var bump = SpringBox(0, .pop)

    func body(content: Content) -> some View {
        content
            .scaleEffect(1 + 0.025 * bump.value)
            .onChange(of: step) { _, _ in nod() }
            .onChange(of: pinged) { old, new in if new > old { nod() } }
    }

    private func nod() {
        guard !still else { return }
        bump.snap(0)
        bump.kick(28)
    }
}
