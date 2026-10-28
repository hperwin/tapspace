import AppKit
import QuartzCore
import SwiftUI

// The Chat tab's third column (C4): the lead's face on top, and under it a pool of faces floating in a
// honeycomb, one per member of the server's `fleet` (its masters, then its taps). When the lead and a member
// talk, a line draws between their faces and a pulse runs along it to the one who heard; hovering a face opens
// a small card saying what that master or tap is about. The server builds the fleet and the talks; this file
// only lays them out and draws them. Every loop (the float, the line's draw and chase, the ping) runs on Core
// Animation, so Motion.main sleeps unless a hover or an arrival is springing.

/// Where the faces go, which talks draw, how old lines fade, and the plain words the card shows. Pure rules
/// (the harness checks them: POOLCHECK).
@MainActor
enum ChatPoolBoard {
    /// A pool face, the gap beside it, the row pitch and the column's side margin.
    static let face: CGFloat = 40, gap: CGFloat = 14, pitch: CGFloat = 48, side: CGFloat = 16
    /// The lead's face and the room above it.
    static let lead: CGFloat = 64, leadTop: CGFloat = 14
    /// The lead block: face, 10 pt, the title line (17), 2 pt, the activity line (15).
    static let leadBlock: CGFloat = leadTop + lead + 10 + 17 + 2 + 15
    /// The pool starts this far under the lead block.
    static let poolGap: CGFloat = 28
    /// A talk line bows this far sideways from its midpoint.
    static let bend: CGFloat = 18
    /// A talk reads fresh this long, then fades until links.LIVE (600 s), then its line leaves.
    static let fresh: Double = 60, live: Double = 600

    /// Faces per full row: n = floor((W - 2*16 + 14) / (40 + 14)), at least 2 so the short rows hold one.
    static func perRow(_ width: CGFloat) -> Int {
        max(2, Int(((width - 2 * side + gap) / (face + gap)).rounded(.down)))
    }

    /// Where the pool starts: under the lead block, or at the top with no lead.
    static func top(lead: Bool) -> CGFloat { lead ? leadBlock + poolGap : leadTop }

    /// The lead face's center.
    static func leadCenter(_ width: CGFloat) -> CGPoint { CGPoint(x: width / 2, y: leadTop + lead / 2) }

    /// Face centers, honeycomb: even rows hold n, odd rows n - 1 (so they sit half a cell over), every row
    /// centered, one row every 48 pt.
    static func slots(count: Int, width: CGFloat, top: CGFloat) -> [CGPoint] {
        let n = perRow(width)
        var out: [CGPoint] = []
        var row = 0
        while out.count < count {
            let k = min(row % 2 == 0 ? n : n - 1, count - out.count)
            let span = CGFloat(k) * face + CGFloat(k - 1) * gap
            let x0 = (width - span) / 2 + face / 2
            let y = top + CGFloat(row) * pitch + face / 2
            for i in 0..<k { out.append(CGPoint(x: x0 + CGFloat(i) * (face + gap), y: y)) }
            row += 1
        }
        return out
    }

    /// The pool's members: masters in the server's order, then taps in the server's order (layout only; the
    /// server decides who is in it, R8).
    static func members(_ fleet: [FleetEntry]) -> [FleetEntry] {
        fleet.filter { $0.kind != "tap" } + fleet.filter { $0.kind == "tap" }
    }

    /// One line: the newest talk between the lead and this member, either way.
    struct Line: Equatable {
        let member: String
        let talk: AgentTalk
        /// The lead said it (the line draws from the lead's end).
        let fromLead: Bool
    }

    /// One line per member that talked with the lead in the last `live` seconds, its newest talk. Tap keys match
    /// case-insensitively (Muddi writes both casings); talks between two members, or with a key not in the pool,
    /// draw nothing.
    static func lines(talks: [AgentTalk], lead: String, members: [String], now: Double) -> [Line] {
        var byLower: [String: String] = [:]
        for m in members where m.lowercased() != lead.lowercased() { byLower[m.lowercased()] = m }
        let leadKey = lead.lowercased()
        var out: [Line] = []
        var taken = Set<String>()
        for t in talks.sorted(by: { ($0.ts, $0.id) > ($1.ts, $1.id) }) where now - t.ts < live {
            let from = t.from.lowercased(), to = t.to.lowercased()
            let member: String?
            if from == leadKey { member = byLower[to] } else if to == leadKey { member = byLower[from] } else { member = nil }
            guard let m = member, taken.insert(m).inserted else { continue }
            out.append(Line(member: m, talk: t, fromLead: from == leadKey))
        }
        return out
    }

    /// 0.9 under a minute old, then linearly down to 0.15 at 600 s.
    static func opacity(age: Double) -> Double {
        if age <= fresh { return 0.9 }
        return 0.9 - 0.75 * min(1, (age - fresh) / (live - fresh))
    }

    /// The curve's control point: 18 pt sideways from the midpoint, bowing away from the column's middle, so a
    /// line keeps its side whichever end spoke.
    static func control(lead a: CGPoint, member b: CGPoint, width: CGFloat) -> CGPoint {
        let dx = b.x - a.x, dy = b.y - a.y
        let len = max(1, (dx * dx + dy * dy).squareRoot())
        var n = CGPoint(x: -dy / len, y: dx / len)
        let away: CGFloat = b.x >= width / 2 ? 1 : -1
        if (n.x >= 0 ? 1 : -1) != away { n = CGPoint(x: -n.x, y: -n.y) }
        return CGPoint(x: (a.x + b.x) / 2 + n.x * bend, y: (a.y + b.y) / 2 + n.y * bend)
    }

    /// "ready for sign-off" -> "Ready for sign-off".
    static func capitalized(_ s: String) -> String { s.prefix(1).uppercased() + s.dropFirst() }

    /// One line of plain words for the card from a tap's goal, ask or message (the server's clipping rules, on
    /// text it does not clip): the first line, no markdown, links, paths, issue numbers or hex ids, NAMEs made
    /// titles, never the word "pilot", no em dashes, at most `limit` characters.
    static func plain(_ s: String, titles: [String: String] = [:], limit: Int = 140) -> String {
        var t = s.split(whereSeparator: \.isNewline).map { $0.trimmingCharacters(in: .whitespaces) }.first { !$0.isEmpty } ?? ""
        for (name, title) in titles where !name.isEmpty { t = t.replacingOccurrences(of: name, with: title) }
        let rules: [(String, String)] = [
            (#"^(#+|>|[-*+]|\d+[.)])\s+"#, ""),            // a heading, quote or list marker
            (#"\*\*|__|`"#, ""),                            // bold and code marks
            (#"https?://\S+"#, ""),                         // links
            (#"\s*\((?:the |a )?pilots?\)"#, ""),        // "(pilot)": who wrote it, said already
            (#"\b(?:PR|pull request)\s*#\d+"#, "the PR"),  // a PR by number
            (#"(?:\b(?:issue|Issue)\s*)?#\d+"#, ""),       // issue numbers
            (#"\S*/\S*"#, ""),                              // paths and owner/repo keys
            (#"\b[0-9a-f]{7,}\b"#, ""),                     // hex ids
            (#"\b([Pp])ilots\b"#, "taps"), (#"\b([Pp])ilot\b"#, "tap"),
            (#"\s*\x{2014}\s*"#, ", "),                     // em dashes
            (#"\(\s*\)"#, ""), (#"\s+([,.;:!?])"#, "$1"), (#"\s{2,}"#, " "),
        ]
        for (pattern, with) in rules { t = t.replacingOccurrences(of: pattern, with: with, options: .regularExpression) }
        t = t.trimmingCharacters(in: CharacterSet(charactersIn: " ,;:-"))
        // A sentence the rules left starting low ("the PR was merged") starts with a capital again.
        var out = ""
        var up = true
        for ch in t {
            out.append(up && ch.isLetter ? Character(ch.uppercased()) : ch)
            if ch.isLetter || ch.isNumber { up = false } else if ".!?".contains(ch) { up = true }
        }
        t = out
        return t.count > limit ? String(t.prefix(limit - 1)).trimmingCharacters(in: .whitespaces) + "\u{2026}" : t
    }

    /// The card's goals line for a tap: "3 of 4 goals done", and the first goal not done (the last one when all
    /// are). nil without goals.
    static func goals(_ goals: [TapGoal]) -> (count: String, goal: String)? {
        guard !goals.isEmpty else { return nil }
        let done = goals.filter(\.done).count
        let next = goals.first { !$0.done } ?? goals[goals.count - 1]
        return ("\(done) of \(goals.count) goals done", plain(next.text))
    }

    /// The rules on synthetic rows: one line per failed expectation, empty when every one holds.
    static func checks() -> [String] {
        var failed: [String] = []
        func expect(_ ok: Bool, _ what: String) { if !ok { failed.append("pool check: " + what) } }
        expect(perRow(288) == 5, "a 288 pt column holds 5 faces a row")
        let s = slots(count: 12, width: 288, top: 0)
        expect(s.count == 12, "one slot per face")
        expect(Set(s.prefix(5).map(\.y)).count == 1 && Set(s.dropFirst(5).prefix(4).map(\.y)).count == 1,
               "rows of 5 then 4")
        expect(abs((s[5].x - s[0].x) - (face + gap) / 2) < 0.01, "the short row sits half a cell over")
        expect(abs(s[5].y - s[0].y - pitch) < 0.01, "rows 48 pt apart")
        var apart = true
        for i in s.indices { for j in s.indices where j > i { if hypot(s[i].x - s[j].x, s[i].y - s[j].y) < face { apart = false } } }
        expect(apart, "no two faces overlap")
        expect(s.allSatisfy { $0.x - face / 2 >= side - 0.01 && $0.x + face / 2 <= 288 - side + 0.01 }, "inside the margins")
        let m = [FleetEntry(key: "a/b#1", kind: "tap"), FleetEntry(key: "agent:x"), FleetEntry(key: "agent:y")]
        expect(members(m).map(\.key) == ["agent:x", "agent:y", "a/b#1"], "masters first, then taps, each in order")
        let now = 1_800_000_000.0
        func talk(_ id: String, _ f: String, _ t: String, _ age: Double) -> AgentTalk {
            AgentTalk(id: id, from: f, to: t, ts: now - age, text: "", kind: "message")
        }
        let ls = lines(talks: [talk("1", "agent:L", "agent:x", 200), talk("2", "agent:x", "agent:L", 20),
                               talk("3", "agent:x", "agent:y", 5), talk("4", "agent:L", "Owner/Repo#7", 30),
                               talk("5", "agent:L", "agent:gone", 10), talk("6", "agent:L", "agent:y", 700)],
                       lead: "agent:L", members: ["agent:x", "agent:y", "owner/repo#7"], now: now)
        expect(ls.map(\.member) == ["agent:x", "owner/repo#7"], "newest talk per member, tap keys in either case")
        expect(ls.first?.talk.id == "2" && ls.first?.fromLead == false, "the newest talk sets the direction")
        expect(!ls.contains { $0.talk.id == "3" || $0.talk.id == "5" || $0.talk.id == "6" },
               "talks between members, to keys not in the pool, or older than 600 s draw nothing")
        expect(opacity(age: 30) == 0.9 && abs(opacity(age: 600) - 0.15) < 1e-9 && abs(opacity(age: 330) - 0.525) < 1e-9,
               "line opacity by age")
        let c = control(lead: CGPoint(x: 144, y: 46), member: CGPoint(x: 230, y: 200), width: 288)
        expect(abs(hypot(c.x - 187, c.y - 123) - bend) < 0.01 && c.x > 187, "the curve bows 18 pt, away from the middle")
        expect(capitalized("ready for sign-off") == "Ready for sign-off", "the phase word is capitalized")
        let words = plain("**Merged** and live in prod (pilot) \u{2014} see PR #1620 at ~/x/y.md and a1b2c3d4e5\nmore")
        expect(words.hasPrefix("Merged and live in prod, see the PR at") && !words.contains("pilot") && !words.contains("#")
               && !words.contains("/") && !words.contains("a1b2c3d") && !words.contains("\u{2014}") && !words.contains("more"),
               "plain words: \(words)")
        expect(plain("Ask Claude \u{00B7} home \u{00B7} c3a7 now", titles: ["Claude \u{00B7} home \u{00B7} c3a7": "Store pages"])
               == "Ask Store pages now", "NAMEs become titles")
        let g = goals([TapGoal(text: "One", done: true), TapGoal(text: "Two", done: false)])
        expect(g?.count == "1 of 2 goals done" && g?.goal == "Two", "the first goal not done")
        expect(goals([TapGoal(text: "One", done: true)])?.goal == "One", "all done: the last goal")
        let b0 = ChatPoolBeat.at(0), b1 = ChatPoolBeat.at(ChatPoolBeat.draw), bp = ChatPoolBeat.at(ChatPoolBeat.pingAt + 0.01)
        expect(abs(b0.draw) < 1e-6 && b0.chase == nil && b0.ping == nil, "a new line starts undrawn")
        expect(abs(b1.draw - 1) < 1e-6 && b1.chase != nil, "drawn at 0.6 s, then the chase")
        expect(bp.ping != nil && abs((bp.chase?.end ?? 0) - 1) < 1e-6, "the ping starts as the chase reaches the receiver")
        expect(ChatPoolBeat.at(ChatPoolBeat.length + 0.1).ping == nil && ChatPoolBeat.at(ChatPoolBeat.length + 0.1).chase == nil,
               "then the line rests")
        return failed
    }
}

/// A new talk's beat on its line, as model values at `t` seconds (the live line plays the same thing as Core
/// Animation; offline frames pin `t`): the line draws on over 0.6 s from the sender's end (cubic-bezier .4, 0,
/// .2, 1), one chase runs sender to receiver (`chaseDraw`, inOutSine on each half, 1.9 s), and as its head lands
/// the receiver's face pings (the thinking orb's ripple, once: 1.6 s ease-out, scale 0.85 to 1.35, opacity 0.9
/// to 0).
enum ChatPoolBeat {
    static let draw = 0.6, chase = 1.9, ping = 1.6
    static var pingAt: Double { draw + chase / 2 }
    static var length: Double { max(draw + chase, pingAt + ping) }

    struct Pose: Equatable {
        var draw: Double
        var chase: (start: Double, end: Double)?
        /// The ping's progress (0..1), nil outside it.
        var ping: Double?

        static func == (a: Pose, b: Pose) -> Bool {
            a.draw == b.draw && a.ping == b.ping && a.chase?.start == b.chase?.start && a.chase?.end == b.chase?.end
        }
    }

    static func at(_ t: Double) -> Pose {
        let drawn = t >= draw ? 1 : bezier(0.4, 0, 0.2, 1, max(0, t) / draw)
        let c = t >= draw && t < draw + chase ? chaseStroke(at: (t - draw) / chase) : nil
        let p = t >= pingAt && t < pingAt + ping ? (t - pingAt) / ping : nil
        return Pose(draw: drawn, chase: c, ping: p)
    }

    /// The ping ring's scale and opacity at progress `p` (Core Animation's ease-out, 0, 0, .58, 1).
    static func ring(_ p: Double) -> (scale: Double, opacity: Double) {
        let e = bezier(0, 0, 0.58, 1, p)
        return (0.85 + 0.5 * e, 0.9 * (1 - e))
    }

    /// A CSS/Core Animation cubic bezier's y at x (bisection: offline frames only).
    static func bezier(_ x1: Double, _ y1: Double, _ x2: Double, _ y2: Double, _ x: Double) -> Double {
        let x = clamp01(x)
        func at(_ a: Double, _ b: Double, _ t: Double) -> Double { 3 * a * t * (1 - t) * (1 - t) + 3 * b * t * t * (1 - t) + t * t * t }
        var lo = 0.0, hi = 1.0
        for _ in 0..<40 {
            let mid = (lo + hi) / 2
            if at(x1, x2, mid) < x { lo = mid } else { hi = mid }
        }
        return at(y1, y2, (lo + hi) / 2)
    }

    /// The draw-on curve, as Core Animation plays it.
    static var drawCurve: CAMediaTimingFunction { CAMediaTimingFunction(controlPoints: 0.4, 0, 0.2, 1) }
}

/// The third column on the Chat tab. ChatView hosts it as `ChatPoolPanel()` in its `panelWidth` column; it
/// fills that column top aligned and scrolls itself when the pool runs past the bottom (lead, lines and faces
/// scroll together, so a line never leaves its faces).
struct ChatPoolPanel: View {
    var store: Store = .shared
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    /// The face under the pointer.
    @State private var hovered: String?
    /// The face whose card is on screen (kept while the card closes).
    @State private var shown: String?
    @State private var reveal = SpringBox(0, .smooth)
    @State private var dim = SpringBox(0, .snappy)
    @State private var cardHeight: CGFloat = 0

    /// How often the column re-reads the clock (line ages, "2m").
    static let tick: Double = 5

    var body: some View {
        GeometryReader { g in
            TimelineView(.periodic(from: .now, by: Self.tick)) { ctx in
                let now = HarnessPool.shared.clock ?? ctx.date.timeIntervalSince1970
                let pool = column(width: g.size.width, height: g.size.height, now: now)
                if ChatProgressPanel.unscrolled {
                    pool.frame(width: g.size.width, height: g.size.height, alignment: .top).clipped()
                } else {
                    ScrollView(.vertical) { pool }
                        .scrollIndicators(.never)
                }
            }
        }
        .background {
            if HarnessLink.asked || HarnessPool.asked {
                Color.clear.onGeometryChange(for: CGRect.self, of: { $0.frame(in: .global) }) { HarnessLink.shared.panel = $0 }
            }
        }
        .onChange(of: active, initial: true) { _, key in hover(key) }
    }

    /// The pinned face (the harness), else the pointer's.
    private var active: String? { HarnessPool.shared.hover ?? hovered }

    private func hover(_ key: String?) {
        if key == nil, shown == nil, reveal.atRest, reveal.value == 0 { return }
        if let key {
            if shown != key {
                shown = key
                reveal.snap(0)
            }
            reveal.set(1, .smooth)
            dim.set(1, .snappy)
        } else {
            dim.set(0, .snappy)
            reveal.set(0, .snappy) { if active == nil { shown = nil } }
        }
    }

    private func column(width w: CGFloat, height h: CGFloat, now: Double) -> some View {
        let lead = MuddiOrder.lead(store.agents)
        let members = ChatPoolBoard.members(fleet)
        let top = ChatPoolBoard.top(lead: lead != nil)
        let slots = ChatPoolBoard.slots(count: members.count, width: w, top: top)
        let center = ChatPoolBoard.leadCenter(w)
        let lines = lead.map { ChatPoolBoard.lines(talks: talks, lead: $0.key, members: members.map(\.key), now: now) } ?? []
        let at = Dictionary(zip(members.map(\.key), slots), uniquingKeysWith: { first, _ in first })
        let strands = lines.compactMap { l -> ChatPoolStrand? in
            guard let p = at[l.member] else { return nil }
            return ChatPoolStrand(member: l.member, talk: l.talk.id, said: l.talk.ts, lead: center, face: p,
                                  fromLead: l.fromLead, bow: ChatPoolBoard.control(lead: center, member: p, width: w),
                                  opacity: ChatPoolBoard.opacity(age: now - l.talk.ts))
        }
        let bottom = (slots.map(\.y).max() ?? top) + ChatPoolBoard.face / 2
        let tall = max(h, bottom + 28)
        let still = reduceMotion
        let d = dim.value
        return ZStack(alignment: .topLeading) {
            ChatPoolLines(strands: strands, motion: lineMotion, pin: HarnessPool.shared.pin)
                .frame(width: w, height: tall)
            if let lead {
                ChatPoolLead(agent: lead, still: still) { store.open(lead.key) }
                    .frame(width: w)
                    .padding(.top, ChatPoolBoard.leadTop)
            }
            if !store.agentsLoaded {
                ChatPoolEmpty(text: "Looking for agents…").frame(width: w).offset(y: top + 8)
            } else if members.isEmpty {
                ChatPoolEmpty(text: lead.map { "Nothing under \($0.title) yet" } ?? "No lead yet").frame(width: w).offset(y: top + 8)
            }
            ForEach(Array(members.enumerated()), id: \.element.key) { i, e in
                let lifted = shown == e.key && active != nil
                ChatPoolFace(entry: e, index: i, lifted: lifted, still: still,
                             onHover: { h in
                                 if h { hovered = e.key } else if hovered == e.key { hovered = nil }
                             },
                             open: { store.open(e.key) })
                    .opacity(shown != nil && shown != e.key ? lerp(1, 0.5, d) : 1)
                    .position(slots[i])
                    .animation(SpringPreset.smooth.animation, value: slots[i])
                    .transition(.asymmetric(insertion: .identity,
                                            removal: .opacity.combined(with: .scale(scale: 0.9)).animation(.easeOut(duration: 0.18))))
            }
            if let key = shown, let i = members.firstIndex(where: { $0.key == key }) {
                let c = slots[i]
                let below = c.y + 26 + cardHeight <= tall - 8
                let p = reveal.value
                ChatPoolCard(entry: members[i], lead: lead, line: lines.first { $0.member == key }, now: now, store: store)
                    .id(key)
                    .frame(width: w - 24)
                    .onGeometryChange(for: CGFloat.self, of: { $0.size.height }) { cardHeight = $0 }
                    .opacity(clamp01(p))
                    .scaleEffect(0.96 + 0.04 * p, anchor: below ? .top : .bottom)
                    .offset(y: 6 * (1 - p))
                    .blur(radius: 6 * (1 - clamp01(p)))
                    .position(x: w / 2, y: below ? c.y + 26 + cardHeight / 2 : c.y - 26 - cardHeight / 2)
                    .allowsHitTesting(false)
                    .zIndex(1)
            }
        }
        .frame(width: w, height: tall, alignment: .topLeading)
    }

    private var fleet: [FleetEntry] { Self.fleet(store) }

    /// The server's fleet plus taps the harness lists (a scratch server reads no board).
    static func fleet(_ store: Store) -> [FleetEntry] {
        let served = store.fleet
        guard let extra = HarnessPool.shared.taps, !extra.isEmpty else { return served }
        let have = Set(served.map { $0.key.lowercased() })
        return served + extra.filter { !have.contains($0.key.lowercased()) }.map(HarnessPool.entry)
    }

    /// Talks hide while the server is unreachable (they could be stale).
    private var talks: [AgentTalk] {
        HarnessPool.shared.talks ?? (store.connection == .live ? store.talks : [])
    }

    /// The beat plays only while someone can see it (the progress column's rule). Reduce Motion: a still line.
    private var lineMotion: ChatLinkMotion {
        if reduceMotion { return .still }
        let seen = store.tab == .chat && store.windowVisible && ChatMedia.shared.lightbox.path == nil
        return seen ? .run : .off
    }
}

/// "Nothing under Muddi yet": one faint line where the pool would be.
private struct ChatPoolEmpty: View {
    let text: String
    var body: some View {
        Text(text)
            .font(.system(size: 12))
            .foregroundStyle(Color.faint)
            .multilineTextAlignment(.center)
            .padding(.horizontal, 16)
            .frame(maxWidth: .infinity)
            .transition(.opacity)
    }
}

/// The lead on top: its 64 pt face wearing what it is doing now (the pane header's rule), its title and its
/// activity word. A click opens its thread.
private struct ChatPoolLead: View {
    let agent: Agent
    let still: Bool
    let open: () -> Void

    var body: some View {
        let activity = agent.activity
        let word = activity.map(\.word).flatMap { $0.isEmpty ? nil : $0 } ?? agentLabel[agent.state, default: agent.state]
        Button(action: open) {
            VStack(spacing: 0) {
                ChatPoolFloat(period: 4.2, lift: 2, phase: 0, on: !still) {
                    OrbView(state: activity.map { LeadActivityStrip.face($0.state) } ?? agent.state, look: agent.look)
                }
                .frame(width: ChatPoolBoard.lead, height: ChatPoolBoard.lead)
                // The words sit on the column's white, so a talk line passes under them as it does under faces.
                VStack(spacing: 2) {
                    Text(agent.title)
                        .font(.system(size: 13, weight: .semibold))
                        .foregroundStyle(Color.ink)
                        .lineLimit(1)
                        .frame(height: 17)
                    Text(word)
                        .font(.system(size: 11.5))
                        .foregroundStyle(Color.dim)
                        .lineLimit(1)
                        .truncationMode(.tail)
                        .contentTransition(.interpolate)
                        .frame(height: 15)
                        .animation(.smooth(duration: 0.35), value: word)
                }
                .padding(.horizontal, 6)
                .background(RoundedRectangle(cornerRadius: 6, style: .continuous).fill(Color.white))
                .padding(.top, 10)
            }
            .padding(.horizontal, 16)
            .contentShape(Rectangle())
        }
        .buttonStyle(ChatPress(scale: 0.96))
        .accessibilityElement(children: .combine)
        .accessibilityLabel(agent.title + ", " + word)
        .accessibilityHint("Opens the chat")
    }
}

/// One pool face: a master's orb or a tap's ring, floating on its own period and phase. It springs in at its
/// slot (scale 0.6 to 1, opacity, blur 6 to 0, staggered 40 ms by index up to 6) and grows to 1.12 while its
/// card is open. It never scales toward 0 (an OrbView rides inside, CONTRACT.md).
private struct ChatPoolFace: View {
    let entry: FleetEntry
    let index: Int
    let lifted: Bool
    let still: Bool
    let onHover: (Bool) -> Void
    let open: () -> Void
    @State private var appear = SpringBox(0, .pop)
    @State private var grow = SpringBox(0, .pop)

    var body: some View {
        let e = entry
        let a = appear.value
        let size = ChatPoolBoard.face
        Button(action: open) {
            ChatPoolFloat(period: 3.6 + Double(index % 5) * 0.35, lift: 3, phase: Double(index) * 0.6, on: !still) {
                FaceView(key: e.key, size: size, look: e.look, initial: e.initial,
                         phase: e.kind == "tap" ? TapPhase(rawValue: e.phase) : nil)
            }
            .frame(width: size, height: size)
            .contentShape(Circle())
        }
        .buttonStyle(ChatPress(scale: 0.92))
        .scaleEffect(max(0.001, (0.6 + 0.4 * a) * (1 + 0.12 * grow.value)))
        .opacity(clamp01(a))
        .blur(radius: 6 * (1 - clamp01(a)))
        .onHover(perform: onHover)
        .onAppear { appear.set(1, .pop, delay: 0.04 * Double(min(index, 6))) }
        .onChange(of: lifted, initial: true) { _, up in grow.set(up ? 1 : 0, .pop) }
        .accessibilityLabel(e.word.isEmpty ? e.title : e.title + ", " + e.word)
        .accessibilityHint("Opens the chat")
    }
}

/// The card a hovered face opens: what that master or tap is about, in titles and plain words only. Its lines
/// arrive 40 ms apart.
private struct ChatPoolCard: View {
    let entry: FleetEntry
    let lead: Agent?
    let line: ChatPoolBoard.Line?
    let now: Double
    let store: Store

    var body: some View {
        let shape = RoundedRectangle(cornerRadius: 14, style: .continuous)
        VStack(alignment: .leading, spacing: 0) {
            if entry.kind == "tap" { tap } else { master }
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(shape.fill(Color.white).modifier(ChatCardLift(k: 1.4)))
    }

    /// Title, state chip, goal (else the order's first line), the step it is on, the newest talk with the lead.
    @ViewBuilder private var master: some View {
        let a = store.agent(entry.key)
        let state = a?.state ?? entry.state
        let plan = a.flatMap { ChatProgressBoard.progress($0) }
        let about = ChatProgressBoard.oneLine(plan?.goal ?? "").isEmpty
            ? (a?.brief ?? "") : ChatProgressBoard.oneLine(plan?.goal ?? "")
        title(a?.title ?? entry.title).modifier(ChatPoolLineIn(index: 0))
        StatusChip(state: state, label: agentLabel[state, default: state])
            .padding(.top, 8)
            .modifier(ChatPoolLineIn(index: 1))
        if !about.isEmpty {
            Text(about)
                .font(.system(size: 12.5))
                .foregroundStyle(Color.ink)
                .lineSpacing(2)
                .lineLimit(3)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.top, 10)
                .modifier(ChatPoolLineIn(index: 2))
        }
        if let p = plan, let s = ChatProgressBoard.stepLine(p) {
            VStack(alignment: .leading, spacing: 7) {
                ChatStepTrack(count: p.steps.count, current: p.current,
                              working: ChatProgressBoard.working.contains(state), still: true)
                Text(ChatStepLine.text(s.count == "Done" ? s.count : "Step " + s.count, s.step))
                    .font(.system(size: 11.5))
                    .monospacedDigit()
                    .lineLimit(1)
                    .truncationMode(.tail)
            }
            .padding(.top, 10)
            .modifier(ChatPoolLineIn(index: 3))
        }
        talk.modifier(ChatPoolLineIn(index: 4))
    }

    /// Title, phase word, goals done and the next one, the question when it waits on Hayden, the newest message.
    @ViewBuilder private var tap: some View {
        let t = HarnessPool.tap(entry.key, store: store)
        let titles = Dictionary(store.agents.map { ($0.name, $0.title) }, uniquingKeysWith: { first, _ in first })
        let word = ChatPoolBoard.capitalized(t?.phaseLabel ?? entry.word)
        let goals = t.flatMap { ChatPoolBoard.goals($0.goals) }
            ?? (entry.total > 0 ? ("\(entry.done) of \(entry.total) goals done", "") : nil)
        let ask = t.flatMap { $0.waits ? $0.ask.map { ChatPoolBoard.plain($0.text, titles: titles, limit: 200) } : nil } ?? ""
        let said = t?.messages.last.map { ChatPoolBoard.plain($0.body, titles: titles) } ?? ""
        title(t?.name ?? entry.title).modifier(ChatPoolLineIn(index: 0))
        Text(word)
            .font(.system(size: 12, weight: .semibold))
            .foregroundStyle(Self.color(t?.chipState ?? entry.phase))
            .padding(.top, 6)
            .modifier(ChatPoolLineIn(index: 1))
        if let goals {
            VStack(alignment: .leading, spacing: 3) {
                Text(goals.count)
                    .font(.system(size: 11.5))
                    .foregroundStyle(Color.ink)
                    .monospacedDigit()
                if !goals.goal.isEmpty {
                    Text(goals.goal)
                        .font(.system(size: 12.5))
                        .foregroundStyle(Color.ink)
                        .lineSpacing(2)
                        .lineLimit(3)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            .padding(.top, 10)
            .modifier(ChatPoolLineIn(index: 2))
        }
        if !ask.isEmpty {
            Text(ask)
                .font(.system(size: 12.5))
                .foregroundStyle(Color.tapBlue)
                .lineSpacing(2)
                .lineLimit(3)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.top, 10)
                .modifier(ChatPoolLineIn(index: 3))
        }
        if !said.isEmpty {
            Text(said)
                .font(.system(size: 11.5))
                .foregroundStyle(Color.dim)
                .lineLimit(2)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.top, 10)
                .modifier(ChatPoolLineIn(index: 4))
        }
        talk.modifier(ChatPoolLineIn(index: 5))
    }

    /// "Muddi: check the build · 2m", dim; nothing without a talk with the lead.
    @ViewBuilder private var talk: some View {
        if let l = line, let lead {
            let name = l.fromLead ? lead.title : (store.agent(entry.key)?.title ?? HarnessPool.tap(entry.key, store: store)?.name ?? entry.title)
            let text = ChatProgressBoard.oneLine(l.talk.text)
            Text(name + (text.isEmpty ? "" : ": " + text) + " · " + when(l.talk.ts, now: now))
                .font(.system(size: 11.5))
                .foregroundStyle(Color.dim)
                .lineLimit(2)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.top, 10)
        }
    }

    private func title(_ s: String) -> some View {
        Text(s)
            .font(.system(size: 14, weight: .semibold))
            .foregroundStyle(Color.ink)
            .lineLimit(2)
            .fixedSize(horizontal: false, vertical: true)
    }

    /// The phase word in its chip's color: blue while it waits on Hayden, green once landed, dim otherwise.
    private static func color(_ chip: String) -> Color {
        switch chip {
        case "needs-you", "holding": .tapBlue
        case "landed": .up
        default: .dim
        }
    }
}

/// One line of the card arriving: opacity and a 4 pt rise on the smooth spring, 40 ms after the line above.
private struct ChatPoolLineIn: ViewModifier {
    let index: Int
    @State private var appear = SpringBox(0, .smooth)

    func body(content: Content) -> some View {
        let p = appear.value
        content
            .opacity(clamp01(p))
            .offset(y: 4 * (1 - p))
            .onAppear { appear.set(1, .smooth, delay: 0.04 * Double(index)) }
    }
}

/// Floats any face on Core Animation: translateY 0 to -`lift` and back, ease in-out sine, one cycle per
/// `period`, `phase` seconds into it, so faces drift and never move in lockstep. Zero main-thread cost: the
/// display link stays asleep. The face is hosted inside; clicks and hover belong to the SwiftUI view around it.
private struct ChatPoolFloat<Content: View>: NSViewRepresentable {
    let period: Double
    let lift: CGFloat
    let phase: Double
    let on: Bool
    @ViewBuilder var content: Content

    final class Host: NSView {
        let hosting: NSHostingView<Content>
        var sig = ""
        var armed = ""

        init(_ content: Content) {
            hosting = NSHostingView(rootView: content)
            super.init(frame: .zero)
            wantsLayer = true
            layer?.masksToBounds = false
            hosting.sizingOptions = []
            addSubview(hosting)
        }

        required init?(coder: NSCoder) { fatalError() }

        override func layout() {
            super.layout()
            hosting.frame = bounds
            arm()
        }

        /// (Re)adds the float once the hosted face has a layer and whenever its settings change.
        func arm() {
            guard let l = hosting.layer, armed != sig else { return }
            armed = sig
            l.removeAnimation(forKey: "float")
            let parts = sig.split(separator: "|").compactMap { Double($0) }
            guard parts.count == 4, parts[3] > 0 else { return }
            let a = CABasicAnimation(keyPath: "transform.translation.y")
            a.fromValue = 0
            a.toValue = -parts[1]
            a.duration = parts[0] / 2
            a.autoreverses = true
            a.repeatCount = .infinity
            a.timingFunction = .inOutSine
            a.timeOffset = parts[2].truncatingRemainder(dividingBy: parts[0])
            a.isRemovedOnCompletion = false
            l.add(a, forKey: "float")
        }

        override func hitTest(_ point: NSPoint) -> NSView? { nil }
    }

    func makeNSView(context: Context) -> Host { Host(content) }

    func updateNSView(_ v: Host, context: Context) {
        v.hosting.rootView = content
        v.sig = "\(period)|\(lift)|\(phase)|\(on ? 1 : 0)"
        v.arm()
    }
}

/// One drawn line: the lead and a member, the newest talk between them, and where their faces sit.
struct ChatPoolStrand: Equatable {
    let member: String
    let talk: String
    /// When it was said (epoch): only a talk said after the column appeared plays its beat.
    let said: Double
    let lead: CGPoint
    let face: CGPoint
    let fromLead: Bool
    let bow: CGPoint
    let opacity: Double
}

/// The talk lines on Core Animation, under the faces: a 1.5 pt accent curve per strand at its age's opacity,
/// and on each new talk its beat (ChatPoolBeat): the draw-on from the sender, one 3 pt chase over the line, the
/// receiver's ping. `pin` (the harness) holds one talk's beat at a time for offline frames, which draw models.
private struct ChatPoolLines: NSViewRepresentable {
    let strands: [ChatPoolStrand]
    let motion: ChatLinkMotion
    let pin: (talk: String, t: Double)?

    @MainActor final class Strand {
        let base = CAShapeLayer()
        let chase = CAShapeLayer()
        let ping = CAShapeLayer()
        var talk = ""
        var fromLead = true
        var lead = CGPoint.zero
        var face = CGPoint.zero
        var pinned = false

        init(into host: CALayer) {
            for l in [ping, base, chase] {
                l.fillColor = nil
                l.lineCap = .round
                l.strokeColor = Palette.blue.cgColor
                host.addSublayer(l)
            }
            base.lineWidth = 1.5
            chase.lineWidth = 3
            chase.strokeEnd = 0
            ping.lineWidth = 1.5
            ping.strokeColor = Palette.blue.withAlphaComponent(0.35).cgColor
            ping.opacity = 0
        }

        var layers: [CALayer] { [ping, base, chase] }

        /// The curve from the sender's face to the receiver's.
        func path(bow: CGPoint) -> CGPath {
            let p = CGMutablePath()
            p.move(to: fromLead ? lead : face)
            p.addQuadCurve(to: fromLead ? face : lead, control: bow)
            return p
        }

        /// The receiver's ring: around its face, 4 pt out (the orb's ripple).
        func placePing() {
            let to = fromLead ? face : lead
            let r = (fromLead ? ChatPoolBoard.face : ChatPoolBoard.lead) / 2 + 4
            ping.bounds = CGRect(x: 0, y: 0, width: 2 * r, height: 2 * r)
            ping.position = to
            ping.path = CGPath(ellipseIn: ping.bounds, transform: nil)
        }

        /// Model values at a pinned moment of the beat.
        func pose(_ b: ChatPoolBeat.Pose) {
            base.strokeEnd = CGFloat(b.draw)
            chase.strokeStart = CGFloat(b.chase?.start ?? 0)
            chase.strokeEnd = CGFloat(b.chase?.end ?? 0)
            if let p = b.ping {
                let r = ChatPoolBeat.ring(p)
                ping.opacity = Float(r.opacity)
                ping.transform = CATransform3DMakeScale(CGFloat(r.scale), CGFloat(r.scale), 1)
            } else {
                ping.opacity = 0
                ping.transform = CATransform3DIdentity
            }
        }

        /// The beat, live: draw-on, then the chase once, then the ping as the chase lands.
        func play() {
            for l in layers { l.removeAllAnimations() }
            pose(ChatPoolBeat.at(ChatPoolBeat.length))
            let now = base.convertTime(CACurrentMediaTime(), from: nil)
            let draw = CABasicAnimation(keyPath: "strokeEnd")
            draw.fromValue = 0
            draw.toValue = 1
            draw.duration = ChatPoolBeat.draw
            draw.timingFunction = ChatPoolBeat.drawCurve
            base.add(draw, forKey: "draw")
            let run = chaseDraw(duration: ChatPoolBeat.chase, ease: .eachHalf(.inOutSine))
            run.repeatCount = 1
            run.beginTime = now + ChatPoolBeat.draw
            chase.add(run, forKey: "chase")
            let s = CABasicAnimation(keyPath: "transform.scale"); s.fromValue = 0.85; s.toValue = 1.35
            let o = CABasicAnimation(keyPath: "opacity"); o.fromValue = 0.9; o.toValue = 0
            let g = CAAnimationGroup()
            g.animations = [s, o]
            g.duration = ChatPoolBeat.ping
            g.timingFunction = CAMediaTimingFunction(name: .easeOut)
            g.beginTime = now + ChatPoolBeat.pingAt
            ping.add(g, forKey: "ping")
        }
    }

    @MainActor final class Coordinator {
        var strands: [String: Strand] = [:]
        var seen = Set<String>()
        /// When this column appeared: talks said before it draw at rest, without a beat.
        let born = Date().timeIntervalSince1970
    }

    func makeCoordinator() -> Coordinator { Coordinator() }

    func makeNSView(context: Context) -> LayerHost { LayerHost() }

    func updateNSView(_ v: LayerHost, context: Context) {
        guard let host = v.layer else { return }
        let c = context.coordinator
        let keep = Set(strands.map(\.member))
        // Lines whose talk aged out or whose face left: fade, then go.
        for (k, s) in c.strands where !keep.contains(k) {
            c.strands[k] = nil
            CATransaction.begin()
            CATransaction.setAnimationDuration(0.3)
            CATransaction.setCompletionBlock { s.layers.forEach { $0.removeFromSuperlayer() } }
            s.layers.forEach { $0.opacity = 0 }
            CATransaction.commit()
        }
        for d in strands {
            let fresh = c.strands[d.member] == nil
            let s = c.strands[d.member] ?? Strand(into: host)
            c.strands[d.member] = s
            let moved = s.lead != d.lead || s.face != d.face || s.fromLead != d.fromLead
            let newTalk = s.talk != d.talk
            CATransaction.begin()
            CATransaction.setDisableActions(true)
            if moved || fresh {
                let old = s.base.path
                let turned = s.fromLead != d.fromLead
                s.lead = d.lead
                s.face = d.face
                s.fromLead = d.fromLead
                let path = s.path(bow: d.bow)
                // A glide (the faces re-slotting) carries the line on the faces' smooth spring.
                if let old, !fresh, !turned, motion == .run {
                    let a = CASpringAnimation(keyPath: "path")
                    a.fromValue = old
                    a.toValue = path
                    a.stiffness = SpringPreset.smooth.stiffness
                    a.damping = SpringPreset.smooth.damping
                    a.duration = a.settlingDuration
                    s.base.add(a, forKey: "glide")
                    s.chase.add(a, forKey: "glide")
                }
                s.base.path = path
                s.chase.path = path
                s.placePing()
            }
            if newTalk {
                s.talk = d.talk
                let beat = !c.seen.contains(d.talk) && d.said >= c.born - 1 && motion == .run && pin == nil
                c.seen.insert(d.talk)
                if beat { s.play() } else { s.pose(ChatPoolBeat.at(ChatPoolBeat.length)) }
            }
            if let pin, pin.talk == d.talk {
                s.pinned = true
                s.pose(ChatPoolBeat.at(pin.t))
            } else if s.pinned {
                s.pinned = false
                s.pose(ChatPoolBeat.at(ChatPoolBeat.length))
            }
            if fresh { s.base.opacity = Float(d.opacity) }
            CATransaction.commit()
            // An older talk fades on Core Animation's own implicit fade.
            if !fresh { s.base.opacity = Float(d.opacity) }
        }
    }
}
