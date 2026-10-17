import AppKit
import Observation
import SwiftUI

private let agentLabel = ["thinking": "Thinking", "here": "Here", "away": "Away", "switching": "Switching car", "starting": "Starting"]

struct ChatView: View {
    let store: Store
    /// Room for the masters' progress column beside the thread.
    @State private var wide = true

    static let panelWidth: CGFloat = 288

    var body: some View {
        HStack(alignment: .top, spacing: 22) {
            RailView(store: store)
                .frame(width: 300)
            PaneView(store: store)
            if wide {
                ChatProgressPanel()
                    .frame(width: Self.panelWidth)
                    .frame(maxHeight: .infinity, alignment: .top)
            }
        }
        .padding(.horizontal, 40)
        .frame(maxWidth: wide ? 1600 : 1240)
        .frame(maxWidth: .infinity)
        // The window's width, not the capped one. The column shows from 1190 pt
        // and leaves below 1160 (no flicker on a live resize near the edge), and
        // never animates: a springing width would relayout the thread every frame.
        .onGeometryChange(for: CGFloat.self, of: { $0.size.width }) { w in
            let next = wide ? w >= 1160 : w >= 1190
            if next != wide { wide = next }
        }
        .overlay { ChatLightboxView(lb: ChatMedia.shared.lightbox) }
        .coordinateSpace(.named("chat"))
    }
}

// MARK: - Rail

struct RailView: View {
    let store: Store
    @State private var track = LiquidTrack(axis: .vertical, cornerRadius: 16, dropRadius: 12,
                                           anchorRadius: 22, squish: 0.06)
    @State private var frames: [String: CGRect] = [:]
    /// Everything under the lead shown (the disclosure open). Remembered on this Mac; closed by default.
    @AppStorage(RailView.openKey) private var fleetOpen = false
    static let openKey = "railFleetOpen"
    /// The strip under the lead: where the selection sits while the open thread is folded away.
    static let fleetKey = "rail:fleet"

    var body: some View {
        let details = Dictionary(store.agents.map { ($0.key, RailDetail.of($0, store: store)) },
                                 uniquingKeysWith: { first, _ in first })
        // The lead first, its children under it, then the rest (no lead: as served).
        let ordered = MuddiOrder.rows(store.agents)
        // With a lead, every other master and every tap folds under it behind one disclosure (CONTRACT 5, 10).
        let lead = MuddiOrder.lead(store.agents)
        let open = lead == nil || fleetOpen
        let rows = open ? ordered : ordered.filter { $0.agent.key == lead?.key }
        let folded = MuddiOrder.folded(store.agents, taps: store.taps.items, open: fleetOpen)
        let shown = Set(rows.map(\.agent.key) + (open ? store.taps.items.map(\.key) : []))
        ScrollView(.vertical) {
            VStack(alignment: .leading, spacing: 8) {
                RailTools(store: store)
                RailHead(title: "Agents")
                if store.agents.isEmpty {
                    RailEmpty(text: store.agentsLoaded
                              ? "No agents in the space yet. Any agent joins with tap.py listen --as NAME."
                              : "Looking for agents…")
                }
                ForEach(Array(rows.enumerated()), id: \.element.agent.key) { i, row in
                    let a = row.agent
                    // The lines under a master are siblings of its row, never inside it:
                    // the row's frame is what the liquid selection follows.
                    VStack(alignment: .leading, spacing: 0) {
                        RailRow(key: a.key, index: i, store: store, onFrame: { frames[a.key] = $0 }) {
                            OrbView(state: a.state, look: a.look).frame(width: 38, height: 38)
                            RowText(title: a.title, sub: agentSub(a))
                        }
                        .modifier(SpinDownRow(key: a.key, title: a.title, store: store))
                        RailAgentDetail(detail: details[a.key] ?? RailDetail()) { store.open(a.key) }
                        if a.key == lead?.key {
                            ChatFleetStrip(fleet: store.fleet, lead: a.title, open: fleetOpen,
                                           unread: folded.contains { store.isUnread($0) || store.hasNotes($0) },
                                           store: store) { fleetOpen.toggle() }
                                .onGeometryChange(for: CGRect.self, of: { $0.frame(in: .named("rail")) }) { frames[Self.fleetKey] = $0 }
                        }
                    }
                    // Under the lead, every other master moves in as one: row, Spin down, talk line, notes.
                    .padding(.leading, lead != nil && a.key != lead?.key ? MuddiOrder.indent : 0)
                    .transition(RailNoteRow.exit)
                }
                if open {
                    Group {
                        RailHead(title: "Your taps")
                        if store.taps.items.isEmpty {
                            RailEmpty(text: tapsEmptyText)
                        }
                        ForEach(Array(store.taps.items.enumerated()), id: \.element.key) { i, t in
                            RailRow(key: t.key, index: rows.count + i, store: store, onFrame: { frames[t.key] = $0 }) {
                                TapAvatar(status: t.status, working: t.replying == true, phase: t.tapPhase, initial: t.initial)
                                RowText(title: t.name, sub: tapSub(t), dim: t.tapPhase == .closed)
                            }
                            .transition(RailNoteRow.exit)
                        }
                    }
                    .padding(.leading, lead != nil ? MuddiOrder.indent : 0)
                }
            }
            .padding(.horizontal, 6)
            .padding(.top, 2)
            .padding(.bottom, 20)
            .coordinateSpace(.named("rail"))
            .background(alignment: .topLeading) { ChatRailLiquid(track: track) }
            // Agents re-sort by state (thinking first), move under the lead or out,
            // and gain or shed the lines under them; the group folds and unfolds:
            // rows glide, the body follows.
            .animation(.spring(response: 0.5, dampingFraction: 0.82),
                       value: rows.map { $0.agent.key + ($0.child ? ">" : "") + "|" + (details[$0.agent.key]?.sig ?? "") }
                           + ["open:\(open)"])
        }
        .scrollIndicators(.never)
        // A folded row leaves no frame behind for the selection to find.
        .onChange(of: shown) { _, keys in
            frames = frames.filter { keys.contains($0.key) || ($0.key == Self.fleetKey && lead != nil) }
        }
        .onChange(of: fleetOpen) { place(animated: true) }
        .onChange(of: frames) { place(animated: true) }
        // The pane builds the new thread in the update that changes it (layout
        // of every bubble, one long main-thread frame). The liquid starts on the
        // next display frame, so its first spring frame is not that frame.
        .onChange(of: store.thread) { ChatNextFrame.run { place(animated: true) } }
        .onAppear { place(animated: false) }
    }

    private var tapsEmptyText: String {
        if store.taps.fetched > 0 { return "No taps flying under your name." }
        return store.taps.error.isEmpty ? "Looking for your taps…" : "Couldn’t reach GitHub: " + store.taps.error
    }

    private func agentSub(_ a: Agent) -> String {
        agentLabel[a.state, default: a.state]
    }

    private func tapSub(_ t: Tap) -> String {
        var s = t.phaseLabel + " · " + t.repoName
        if let last = t.messages.last(where: { $0.who == "tap" }) { s += " · " + when(last.date) }
        return s
    }

    /// The selection sits on the open thread's row, or on the strip under the lead while that row is folded away.
    private func place(animated: Bool) {
        let t = store.thread
        let hidden = MuddiOrder.folded(store.agents, taps: store.taps.items, open: fleetOpen).contains(t)
        guard let r = frames[hidden ? Self.fleetKey : t], r.height > 0 else { track.hide(); return }
        track.move(to: r, animated: animated)
    }
}

/// New session: a master agent that starts in the home folder and picks its
/// project itself once Hayden says what to work on.
struct RailTools: View {
    let store: Store
    @State private var starting = false

    var body: some View {
        Button(action: start) {
            Label("New session", systemImage: "plus")
                .frame(maxWidth: .infinity)
                .modifier(RailButton(fg: .white, bg: .ink))
        }
        .buttonStyle(ChatPress(scale: 0.96))
        .disabled(starting)
        .padding(.horizontal, 4)
        .padding(.top, 4)
    }

    private func start() {
        starting = true
        Task {
            _ = await store.startSession()
            starting = false
        }
    }
}

struct RailButton: ViewModifier {
    let fg: Color
    let bg: Color
    func body(content: Content) -> some View {
        content
            .font(.system(size: 12.5, weight: .semibold))
            .foregroundStyle(fg)
            .lineLimit(1)
            .padding(.horizontal, 14)
            .padding(.vertical, 10)
            .background(RoundedRectangle(cornerRadius: 12, style: .continuous).fill(bg))
            .contentShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
    }
}

struct RailHead: View {
    let title: String
    var body: some View {
        Text(title.uppercased())
            .font(.system(size: 10.5, weight: .semibold))
            .tracking(1.7)
            .foregroundStyle(Color.faint)
            .padding(.horizontal, 10)
            .padding(.top, 12)
            .padding(.bottom, 2)
    }
}

struct RailEmpty: View {
    let text: String
    var body: some View {
        Text(text)
            .font(.system(size: 12))
            .foregroundStyle(Color.faint)
            .lineSpacing(3)
            .padding(.horizontal, 12)
            .padding(.vertical, 8)
            .fixedSize(horizontal: false, vertical: true)
            .transition(.opacity.combined(with: .offset(y: 4)))
    }
}

struct RowText: View {
    let title: String
    let sub: String
    /// A closed tap reads greyed.
    var dim = false
    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(title)
                .font(.system(size: 13, weight: .semibold))
                .foregroundStyle(Color.ink)
                .lineLimit(1)
            Text(sub)
                .font(.system(size: 11.5))
                .foregroundStyle(Color.dim)
                .lineLimit(1)
                .truncationMode(.middle)
                .contentTransition(.interpolate)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .opacity(dim ? 0.55 : 1)
        .animation(.smooth(duration: 0.35), value: sub)
    }
}

/// A rail row: staggered blur-in on arrival, a hover wash, a springy press,
/// and its frame reported so the one liquid selection body can spring to it.
struct RailRow<Content: View>: View {
    let key: String
    let index: Int
    let store: Store
    let onFrame: (CGRect) -> Void
    @ViewBuilder var content: Content
    @State private var hover = false

    var body: some View {
        let selected = store.thread == key
        Button { store.open(key) } label: {
            HStack(spacing: 12) {
                content
                // Blue dot: a reply he has not read, or card notes this master has not answered.
                UnreadDot(on: store.isUnread(key) || store.hasNotes(key))
            }
            .padding(12)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(RoundedRectangle(cornerRadius: 16, style: .continuous)
                .fill(Color.black.opacity(hover && !selected ? 0.03 : 0)))
            .contentShape(RoundedRectangle(cornerRadius: 16, style: .continuous))
        }
        .buttonStyle(ChatPress(scale: 0.98))
        .onGeometryChange(for: CGRect.self, of: { $0.frame(in: .named("rail")) }, action: onFrame)
        .onHover { h in withAnimation(.easeOut(duration: 0.2)) { hover = h } }
        .modifier(RailArrival(index: index))
    }
}

/// The web page's railin: blur 12 -> 0, rise 8 pt, staggered per row.
/// Isolated so its per-frame spring re-renders only this wrapper.
struct RailArrival: ViewModifier {
    let index: Int
    @State private var appear = SpringBox(0, .gentle)

    func body(content: Content) -> some View {
        let p = appear.value
        content
            .opacity(clamp01(p))
            .blur(radius: 12 * (1 - clamp01(p)))
            .offset(y: 8 * (1 - p))
            .onAppear { appear.set(1, delay: 0.06 * Double(min(index, 10))) }
    }
}

struct UnreadDot: View {
    let on: Bool
    @State private var s = SpringBox(0, .pop)

    var body: some View {
        Circle()
            .fill(Color.tapBlue)
            .frame(width: 8, height: 8)
            .scaleEffect(max(0, s.value))
            .onAppear { s.snap(0); if on { s.set(1, .pop, delay: 0.25) } }
            .onChange(of: on) { _, v in s.set(v ? 1 : 0, .pop) }
    }
}

/// Ring avatar per tap: spinning arc = flying, breathing blue = needs you,
/// dotted slow spin = boarding, green check = landed. With a phase from the
/// server the look comes from MuddiLook (landed: green ring, done: the check).
/// The repo's letter sits in the centre until the check takes its place.
struct TapAvatar: View {
    let status: String
    var working = false
    var size: CGFloat = 38
    /// nil (an older server, or the pill): the legacy look by `status`.
    var phase: TapPhase? = nil
    /// The repo's letter (the server's `initial`); "" draws the ring alone.
    var initial = ""
    /// The ring holds still (a face inside an update card never loops).
    var still = false

    var body: some View {
        let look = phase.map { MuddiLook($0, working: working) } ?? legacyLook
        ZStack {
            Circle().fill(look.fill)
            RingView(style: still ? look.ring.resting : look.ring, lineWidth: 2.2 * size / 38,
                     track: NSColor.black.withAlphaComponent(0.06))
            Group {
                if look.check {
                    Image(systemName: "checkmark")
                        .font(.system(size: 11 * size / 38, weight: .bold))
                        .transition(.symbolEffect(.drawOn))
                } else if !initial.isEmpty {
                    Text(verbatim: initial)
                        .font(.system(size: 0.4 * size, weight: .bold))
                        .transition(.opacity)
                }
            }
            .foregroundStyle(look.glyph)
        }
        .frame(width: size, height: size)
        .animation(.smooth(duration: 0.4), value: status + "|" + (phase?.rawValue ?? ""))
    }

    /// Today's look by the legacy status.
    private var legacyLook: MuddiLook {
        // the ring only moves while the pilot is actually working
        let style: RingStyle = switch status {
        case "flying": .spin(Palette.ink, length: 0.27, period: working ? 1.6 : 0)
        case "boarding": .dotted(Palette.faint, period: working ? 6 : 0)
        case "needs-you": .breathe(Palette.blue, period: 1.5)
        default: .solid(Palette.green)
        }
        let bg: Color = switch status {
        case "needs-you": Color.tapBlue.opacity(0.08)
        case "landed": Color.up.opacity(0.09)
        default: Color(white: 0.957)
        }
        let fg: Color = status == "needs-you" ? .tapBlue : (status == "landed" ? .up : .dim)
        return MuddiLook(ring: style, fill: bg, glyph: fg, check: status == "landed")
    }
}

// MARK: - Pane

struct DisplayMessage: Identifiable, Equatable {
    enum Receipt: Equatable { case sending, delivered, seen }
    let id: String
    let mine: Bool
    let body: String
    let ts: Double
    var receipt: Receipt?
    /// Served paths (/chat-media/...) or, before upload, "local:<id>".
    var images: [String] = []
    var fly = false
    /// An update card (agent threads): drawn by ChatCardView in place of the bubble.
    var card: CardRow? = nil
}

/// Where the composer field sits in the pane, so a sent bubble can lift out
/// of it. Only flying bubbles read it, so its per-frame updates stay local.
@MainActor @Observable
final class ChatGeometry {
    var composer: CGRect = .zero
    /// Sent bubbles still in the air: the list lets them draw past its
    /// bottom edge (over the composer) only while this is above zero.
    var flights = 0
}


/// Bubble entrances: a new message springs in (blur + offset + scale); a
/// message Hayden just sent flies up out of the composer. An update card plays
/// its beats when it springs in (CONTRACT 10), and so do the newest two Hayden
/// had not seen when the thread opened: they wait out of sight while the open
/// settles, then come in on their own (ChatCardView's `hold`).
@MainActor
final class BubbleAnimator {
    private var boxes: [String: SpringBox] = [:]
    private var known: Set<String> = []
    private var thread = ""
    private var loaded = false
    /// The open thread's read mark from just before it opened.
    private var openMark: Double = 0
    /// The unseen cards that play once the thread's open settles.
    private var opening: Set<String> = []
    /// Rows that had their entrance this session: a card never plays at an open after that.
    private var entered: Set<String> = []
    weak var geo: ChatGeometry?

    /// How long a thread's open settles before its unseen cards come in (CONTRACT 12: 700 ms).
    static let openHold = 0.7

    /// `loaded`: the thread's history has been fetched. The rows of that first
    /// fetch are history, not arrivals: they take their places without springing
    /// in, as on any later open (springing in, every laid-out row re-rendered on
    /// every frame). The exception is `cards` (key, time) newer than `mark()`,
    /// the read mark from before the open, read once per thread switch: the
    /// newest two of them play together after `openHold` when `plays`.
    func sync(thread: String, loaded: Bool, keys: [String], fly: Set<String>,
              cards: [(key: String, ts: Double)] = [], mark: () -> Double = { .infinity }, plays: Bool = false) {
        if thread != self.thread { openMark = mark() }
        if thread != self.thread || !self.loaded {
            self.thread = thread
            self.loaded = loaded
            known = Set(keys)
            boxes.removeAll()
            opening.removeAll()
            guard loaded, plays else { return }
            for k in Self.opening(cards, after: openMark) where !entered.contains(k) {
                opening.insert(k)
                entered.insert(k)
            }
            return
        }
        for k in keys where !known.contains(k) {
            known.insert(k)
            entered.insert(k)
            let flies = fly.contains(k)
            let b = SpringBox(0, flies ? .chatFly : .chatIn)
            boxes[k] = b
            guard flies, let geo else { b.set(1); continue }
            // Counted outside the view update that discovered the bubble.
            DispatchQueue.main.async {
                geo.flights += 1
                b.set(1) { geo.flights = max(0, geo.flights - 1) }
            }
        }
    }

    func box(_ k: String) -> SpringBox? { boxes[k] }

    /// A card that plays once its thread's open settles (it arrives on its own, after `openHold`).
    func opens(_ k: String) -> Bool { opening.contains(k) }

    /// The cards that play when a thread opens (CONTRACT 10, 12): of the cards newer than the read mark from
    /// before the open, the newest two (`cards` in thread order).
    nonisolated static func opening(_ cards: [(key: String, ts: Double)], after mark: Double) -> [String] {
        cards.filter { $0.ts > mark }.suffix(2).map(\.key)
    }
}

/// The thread swap: header, goals and messages rise into place out of a
/// white veil that clears. Neither a blur nor a group opacity: both render
/// the whole message list into a fresh offscreen surface on every frame of
/// the swap, the same frames the rail's liquid starts moving. The veil is
/// one flat layer over the pane's own white, and the rise is a transform.
struct SwapIn: ViewModifier {
    let box: SpringBox
    var rise: CGFloat = 6

    func body(content: Content) -> some View {
        let p = box.value
        content
            .offset(y: rise * (1 - p))
            .overlay {
                if p < 0.999 { Color.white.opacity(1 - clamp01(p)).allowsHitTesting(false) }
            }
    }
}

struct PaneView: View {
    let store: Store
    @State private var animator = BubbleAnimator()
    @State private var geo = ChatGeometry()
    @State private var swap = SpringBox(1, .gentle)
    @State private var focusToken = 0
    @State private var draft = ChatDraft()
    @State private var dropTarget = false

    var body: some View {
        let key = store.thread
        let agent = store.agent(key)
        let tap = store.tap(key)
        let shape = RoundedRectangle(cornerRadius: 22, style: .continuous)
        VStack(spacing: 0) {
            if agent != nil || tap != nil {
                // Opaque and above the list: while a sent bubble is in the air
                // the list stops clipping, and anything scrolled past its top
                // edge slides under the header instead of over it.
                VStack(spacing: 0) {
                    PaneHeader(agent: agent, tap: tap)
                        .modifier(SwapIn(box: swap, rise: 4))
                    Rectangle().fill(Color.hairline).frame(height: 1)
                    // The lead's thread: what it is doing right now, live from its session (CONTRACT 7).
                    if let agent, agent.isLead, let activity = agent.activity {
                        LeadActivityStrip(activity: activity)
                            .modifier(SwapIn(box: swap, rise: 4))
                        Rectangle().fill(Color.hairline).frame(height: 1)
                    }
                    if let tap, !tap.goals.isEmpty, ProcessInfo.processInfo.environment["CHATEXP_NOGOALS"] == nil {
                        GoalsStrip(goals: tap.goals)
                            .modifier(SwapIn(box: swap, rise: 4))
                        Rectangle().fill(Color.hairline).frame(height: 1)
                    }
                    // A holding, landed or proposed tap: its question, its proof, its flight.
                    if let tap, MuddiTapCard.shows(tap) {
                        MuddiTapCard(tap: tap, store: store)
                            .modifier(SwapIn(box: swap, rise: 4))
                        Rectangle().fill(Color.hairline).frame(height: 1)
                    }
                }
                .background(Color.white)
                .zIndex(2)
            }
            MessageList(store: store, agent: agent.map(ThreadAgent.init), tap: tap, animator: animator, geo: geo)
                .modifier(SwapIn(box: swap, rise: 10))
                .zIndex(1)
            Rectangle().fill(Color.hairline).frame(height: 1)
            ComposerBar(store: store, placeholder: placeholder(agent, tap),
                        enabled: agent != nil || tap != nil, canAttach: agent != nil,
                        focusToken: focusToken + draft.focusBump, geo: geo, draft: draft)
        }
        .coordinateSpace(.named("pane"))
        .overlay { DropOverlay(active: dropTarget && agent != nil) }
        .clipShape(shape)
        .background(shape.fill(.white).softShadow())
        // Card frames (HarnessCard) crop to the pane; reported only when they are asked for.
        .background {
            if CardPose.asked {
                Color.clear.onGeometryChange(for: CGRect.self, of: { $0.frame(in: .global) }) { CardPose.shared.pane = $0 }
            }
        }
        .onDrop(of: [.fileURL, .image], isTargeted: $dropTarget) { draft.drop($0, store: store) }
        .onAppear { ChatHarness.startIfAsked(store: store) }
        .onChange(of: store.thread) {
            // Veiled in the frame that builds the thread; it clears from the next one.
            swap.snap(0)
            ChatNextFrame.run { swap.set(1) }
            focusToken += 1
        }
    }

    private func placeholder(_ agent: Agent?, _ tap: Tap?) -> String {
        if let agent { return "Message \(agent.title)…" }
        return tap != nil ? "Message this tap…" : "Message…"
    }
}

struct PaneHeader: View {
    let agent: Agent?
    let tap: Tap?

    var body: some View {
        HStack(spacing: 12) {
            if let agent {
                // The lead while its row serves activity: one face and one status (CONTRACT 18, as the web does). The
                // face wears what it is doing and the activity strip under the header says it, so no chip here.
                let activity = agent.isLead ? agent.activity : nil
                OrbView(state: activity.map { LeadActivityStrip.face($0.state) } ?? agent.state, look: agent.look)
                    .frame(width: 38, height: 38)
                VStack(alignment: .leading, spacing: 2) {
                    HeaderTitle(text: agent.title)
                    Text(agentSubtitle(agent))
                        .font(.system(size: 11.5))
                        .foregroundStyle(Color.dim)
                        .lineLimit(1)
                        .truncationMode(.middle)
                }
                Spacer(minLength: 8)
                if activity == nil {
                    StatusChip(state: agent.state, label: agentLabel[agent.state, default: agent.state])
                }
            } else if let tap {
                TapAvatar(status: tap.status, working: tap.replying == true, phase: tap.tapPhase, initial: tap.initial)
                VStack(alignment: .leading, spacing: 2) {
                    HeaderTitle(text: tap.name)
                    HStack(spacing: 4) {
                        Text(tap.repoName + " ·")
                        if let url = URL(string: tap.url) { IssueLink(url: url) }
                    }
                    .font(.system(size: 11.5))
                    .foregroundStyle(Color.dim)
                }
                Spacer(minLength: 8)
                StatusChip(state: tap.chipState, label: tap.phaseLabel)
            }
        }
        .foregroundStyle(Color.ink)
        .padding(.horizontal, 20)
        .padding(.top, 16)
        .padding(.bottom, 12)
    }

    private func agentSubtitle(_ a: Agent) -> String {
        "Started " + when(a.first ?? a.seen)
    }
}

struct HeaderTitle: View {
    let text: String
    var body: some View {
        Text(text)
            .font(.system(size: 15, weight: .semibold))
            .tracking(-0.15)
            .lineLimit(1)
    }
}

struct IssueLink: View {
    let url: URL
    @State private var hover = false

    var body: some View {
        Link(destination: url) {
            HStack(spacing: 2) {
                Text("Open issue").underline(true, color: hover ? Color.dim : Color.faint)
                Image(systemName: "arrow.up.right")
                    .font(.system(size: 8.5, weight: .bold))
                    .offset(x: hover ? 1.5 : 0, y: hover ? -1.5 : 0)
            }
        }
        .foregroundStyle(hover ? Color.ink : Color.dim)
        .pointerStyle(.link)
        .onHover { h in withAnimation(.spring(response: 0.3, dampingFraction: 0.6)) { hover = h } }
    }
}

struct StatusChip: View {
    let state: String
    let label: String

    var body: some View {
        let (fg, bg, breathe): (NSColor, Color, Double) = switch state {
        case "thinking", "needs-you", "switching", "starting": (Palette.blue, Color.tapBlue.opacity(0.1), 1.0)
        case "flying": (Palette.ink, Color.black.opacity(0.045), 1.2)
        // a tap en route with nobody working this minute: the flying chip, still
        case "enroute": (Palette.ink, Color.black.opacity(0.045), 0)
        case "landed": (Palette.green, Color.up.opacity(0.1), 0)
        case "here": (Palette.green, Color.black.opacity(0.045), 2.4)
        default: (Palette.faint, Color.black.opacity(0.045), 0)
        }
        HStack(spacing: 6) {
            BreathingDot(color: fg, period: max(breathe, 1), animated: breathe > 0)
                .frame(width: 6, height: 6)
            Text(label.uppercased())
                .font(.system(size: 10.5, weight: .semibold))
                .tracking(0.6)
                .contentTransition(.interpolate)
        }
        .foregroundStyle(Color(nsColor: fg))
        .padding(.horizontal, 9)
        .padding(.vertical, 5)
        .background(Capsule().fill(bg))
        .animation(.spring(response: 0.4, dampingFraction: 0.7), value: label)
    }
}

struct GoalsStrip: View {
    let goals: [TapGoal]

    var body: some View {
        let done = goals.filter(\.done).count
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 8) {
                HStack(spacing: 0) {
                    Text(done, format: .number)
                        .contentTransition(.numericText(value: Double(done)))
                    Text("/")
                    Text(goals.count, format: .number)
                        .contentTransition(.numericText(value: Double(goals.count)))
                    Text(" GOALS")
                }
                .font(.system(size: 10.5, weight: .semibold))
                .tracking(0.8)
                .monospacedDigit()
                .foregroundStyle(Color.faint)
                .padding(.trailing, 4)
                .animation(.spring(response: 0.45, dampingFraction: 0.7), value: done)
                ForEach(Array(goals.enumerated()), id: \.offset) { _, g in
                    GoalPill(goal: g)
                }
            }
            .padding(.horizontal, 20)
            .padding(.vertical, 10)
        }
    }
}

struct GoalPill: View {
    let goal: TapGoal
    @State private var hover = false

    var body: some View {
        HStack(alignment: .top, spacing: 8) {
            GoalCheck(done: goal.done)
            Text(Markdown.inline(goal.text, dark: false))
                .font(.system(size: 11.5))
                .lineSpacing(1.5)
                .lineLimit(hover ? 6 : 2)
                .foregroundStyle(goal.done ? Color.ink : Color.dim)
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 8)
        .frame(width: 230, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 12, style: .continuous)
            .fill(goal.done ? Color.up.opacity(0.07) : Color.black.opacity(0.03)))
        .help(goal.text)
        .onHover { h in withAnimation(.spring(response: 0.38, dampingFraction: 0.8)) { hover = h } }
        .animation(.smooth, value: goal.done)
    }
}

struct GoalCheck: View {
    let done: Bool
    var body: some View {
        ZStack {
            Circle()
                .strokeBorder(done ? Color.up : Color.faint, lineWidth: 1.5)
                .background(Circle().fill(done ? Color.up : .clear))
            if done {
                Image(systemName: "checkmark")
                    .font(.system(size: 7.5, weight: .heavy))
                    .foregroundStyle(.white)
                    .transition(.symbolEffect(.drawOn))
            }
        }
        .frame(width: 15, height: 15)
        .padding(.top, 1)
    }
}

/// What the thread list shows of an agent. Not the whole Agent: its `seen`
/// moves on every heartbeat and would re-render the thread on every poll.
struct ThreadAgent: Equatable {
    let name: String
    let title: String
    let state: String

    init(_ a: Agent) { name = a.name; title = a.title; state = a.state }
}

struct MessageList: View {
    let store: Store
    let agent: ThreadAgent?
    let tap: Tap?
    let animator: BubbleAnimator
    let geo: ChatGeometry
    @State private var position = ScrollPosition(edge: .bottom)
    @State private var nearBottom = true
    @State private var lastThread = ""
    @State private var earlier = EarlierPages()
    /// The row that topped the list when "Show earlier messages" was pressed,
    /// until the one scroll back to it (onChange of the window) has run.
    @State private var keepTop: String?
    /// The row at the end of the list at the last change (an update card arriving there scrolls to its top).
    @State private var lastNewest = ""
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    /// Rows laid out: the newest 40, and 40 more per "Show earlier messages".
    static let page = 40

    private var messages: [DisplayMessage] {
        let key = store.thread
        var out: [DisplayMessage] = []
        if let agent {
            var present = Set<String>()
            // The server's rows (and, under card frames only, the posed ones), each answer folded into its card.
            let rows = (store.chats[agent.name] ?? []) + CardPose.shared.rows(for: agent.name)
            let fold = CardFold(rows: rows, pending: CardAnswers.shared.pending(for: agent.name))
            for m in rows where !fold.hides(m) {
                let id = "\(key)#\(m.id)"
                present.insert(id)
                let card = m.card.map { CardRow(card: $0, id: m.id, agent: m.agent, state: fold.state(m.id)) }
                out.append(DisplayMessage(id: id, mine: m.role == "you", body: m.body, ts: m.ts, images: m.images, card: card))
            }
            // Sends with images, until their server row is in the thread.
            let outbox = ChatOutbox.shared
            for o in outbox.items where o.key == key && !(outbox.serverKey(o.id).map(present.contains) ?? false) {
                out.append(DisplayMessage(id: o.id, mine: true, body: o.body, ts: o.ts,
                                          images: o.images.map(\.localPath), fly: true))
            }
        } else if let tap {
            for (i, m) in tap.messages.enumerated() {
                let mine = m.who == "you"
                let receipt: DisplayMessage.Receipt? = mine ? ((m.seen ?? false) ? .seen : .delivered) : nil
                out.append(DisplayMessage(id: "\(key)#\(i)", mine: mine, body: m.body, ts: m.date, receipt: receipt))
            }
        }
        for p in store.pending where p.key == key {
            out.append(DisplayMessage(id: p.id, mine: true, body: p.body, ts: p.ts,
                                      receipt: tap != nil ? .sending : nil, fly: true))
        }
        return out
    }

    var body: some View {
        let _ = (animator.geo = geo)
        let msgs = messages
        // An agent: thinking with a message of Hayden's unanswered; once the
        // reply is in the thread the dots go, without waiting for the next
        // agents poll. A tap: it has seen (👀) his latest comment and not
        // answered yet, the server's `replying`.
        // An answer folded into a card is still his newest word: the dots show until the agent replies.
        let typing = agent.map { a in a.state == "thinking" && (msgs.last?.mine == true || store.chats[a.name]?.last?.role == "you") }
            ?? (tap?.chatReplying ?? false)
        // A server row that confirmed an optimistic bubble keeps that bubble's
        // key (one row, one identity, its flight state intact) and still flies:
        // on a local server the row lands within the first frames of the flight.
        let outbox = ChatOutbox.shared.confirmed
        let keys = msgs.map { store.confirmed[$0.id] ?? outbox[$0.id] ?? $0.id }
        let rows = zip(msgs, keys).map { m, k in
            var m = m
            if k != m.id { m.fly = true }
            return (m, k)
        }
        let thread = store.thread
        // Loaded once its history is in: an agent's fetched rows, a tap's served comments. A thread whose row has
        // not arrived yet is not loaded, so the history that comes with it later takes its place at rest.
        let cards: [(key: String, ts: Double)] = rows.compactMap { m, k in m.card == nil ? nil : (key: k, ts: m.ts) }
        let _ = animator.sync(thread: thread, loaded: agent.map { store.chats[$0.name] != nil } ?? (tap != nil),
                              keys: keys, fly: Set(rows.filter(\.0.fly).map(\.1)), cards: cards,
                              mark: { ThreadOpenMark.take(thread, store: store) },
                              plays: !reduceMotion && store.isViewingCandidate)
        // An answer landing on a card (or rolling back) changes the list too.
        let answered: String = msgs.map { $0.card?.state.sig ?? "" }.joined(separator: ",")
        let sig = keys.joined(separator: ",") + "|\(typing)|" + msgs.map { $0.receipt.map { "\($0)" } ?? "" }.joined()
            + "|" + msgs.map { "\($0.images.count)" }.joined() + "|" + answered
        let limit = Self.page * (1 + earlier.count(in: store.thread))
        GeometryReader { box in
            let maxW = min(620, box.size.width * 0.82)
            ScrollViewReader { reader in
                ScrollView(.vertical) {
                    // Eager, over the newest rows only: the content height is exact, so
                    // the bottom anchor holds the end through every size change. A lazy
                    // stack estimated the rows it had not placed, and with one bubble
                    // taller than the pane that estimate never settled under the anchor:
                    // layout looped inside one run-loop pass and the app froze.
                    VStack(spacing: 12) {
                        if msgs.isEmpty {
                            Hello(agent: agent, tap: tap)
                                .frame(minHeight: box.size.height - 60)
                        }
                        if rows.count > limit {
                            EarlierButton {
                                keepTop = rows.suffix(limit).first?.1
                                earlier.open(in: store.thread)
                            }
                        }
                        ForEach(ProcessInfo.processInfo.environment["CHATEXP_NOLIST"] == nil ? Array(rows.suffix(limit)) : [], id: \.1) { m, k in
                            BubbleRow(m: m, box: animator.box(k), opens: animator.opens(k), maxWidth: maxW, geo: geo)
                                .id(k)
                        }
                        // The dots' spring is scoped to the dots: on the list it would also
                        // fade in the bubble Hayden just sent (the same update flips typing
                        // on), washing out its flight out of the composer.
                        // Session relay: the thread stays put while the next car takes over.
                        if let s = agent?.state, s == "switching" || s == "starting" {
                            SwitchingCar(label: agentLabel[s, default: s]).transition(.scale(scale: 0.8).combined(with: .opacity))
                        }
                        ZStack(alignment: .leading) {
                            if typing {
                                TypingBubble(caption: tap != nil ? "working on a reply" : nil)
                                    .transition(.asymmetric(
                                        insertion: .scale(scale: 0.4, anchor: .bottomLeading).combined(with: .opacity),
                                        removal: .scale(scale: 0.8, anchor: .bottomLeading).combined(with: .opacity)))
                            }
                        }
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .animation(.spring(response: 0.42, dampingFraction: 0.66), value: typing)
                    }
                    .padding(.horizontal, 22)
                    .padding(.top, 22)
                    .padding(.bottom, 12)
                }
                // Clipped by the scroll view itself (a plain clip, no offscreen
                // pass), except while a sent bubble lifts out of the composer below.
                // A mask here re-rendered the whole list offscreen every frame.
                .scrollClipDisabled(geo.flights > 0)
                .modifier(ExpPos(position: $position))
                // A fresh scroll view per thread opens at the bottom in the frame
                // that builds it; scrolling the old one there afterwards cost a
                // second full layout of the thread a frame later, mid-glide.
                .id(ProcessInfo.processInfo.environment["CHATEXP_NOID"] == nil ? store.thread : "one")
                // Near the end, a new message brings the list down to it. A size change
                // (markdown settling, goals appearing, the composer growing) scrolls
                // nothing here: the bottom anchor keeps the end in place on its own.
                .onScrollGeometryChange(for: Bool.self, of: { g in
                    g.contentSize.height - (g.contentOffset.y + g.containerSize.height) < 120
                }, action: { _, near in nearBottom = near })
                .onChange(of: sig, initial: true) {
                    let switched = lastThread != store.thread
                    lastThread = store.thread
                    // A card that just arrived at the end: its top comes into view (a tall one would show only its end).
                    let newest = rows.last?.1 ?? ""
                    let card = newest != lastNewest && rows.last?.0.card != nil && animator.box(newest) != nil
                    lastNewest = newest
                    // A thread opens on its newest page.
                    if switched { earlier = EarlierPages() }
                    if store.isViewingCandidate { store.markRead(store.thread) }
                    if agent != nil { ChatOutbox.shared.prune(Set(msgs.map(\.id))) }
                    // A switched thread is a fresh scroll view already at its bottom.
                    guard !switched, nearBottom else { return }
                    DispatchQueue.main.async {
                        withAnimation(.spring(response: 0.45, dampingFraction: 0.9)) {
                            if card { reader.scrollTo(newest, anchor: .top) } else { position.scrollTo(edge: .bottom) }
                        }
                    }
                }
                // "Show earlier messages" grew the window: the new page sits above, and
                // SwiftUI holds the top edge, so it would show the new page from its
                // oldest message. ONE scroll, once that layout is done, back to the
                // message that topped the list before the press. Only a press sets
                // keepTop: a size change never scrolls here.
                .onChange(of: limit) {
                    guard let k = keepTop else { return }
                    keepTop = nil
                    DispatchQueue.main.async { reader.scrollTo(k, anchor: .top) }
                }
            }
        }
    }
}

/// Earlier pages opened in the thread on screen. Keyed by thread, so the frame
/// that builds a newly opened thread already lays out only its newest page.
struct EarlierPages: Equatable {
    private var thread = ""
    private var pages = 0
    func count(in t: String) -> Int { t == thread ? pages : 0 }
    mutating func open(in t: String) { pages = count(in: t) + 1; thread = t }
}

/// The next page of earlier messages, in the meta line's voice. Its rows go in
/// above what is on screen, and MessageList scrolls once back to the message
/// that topped the list, so the reader stays where they were.
struct EarlierButton: View {
    let action: () -> Void
    @State private var hover = false

    var body: some View {
        Button(action: action) {
            Text("Show earlier messages")
                .font(.system(size: 10.5, weight: .medium))
                .tracking(0.2)
                .foregroundStyle(hover ? Color.dim : Color.faint)
                .padding(.vertical, 4)
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .pointerStyle(.link)
        .onHover { hover = $0 }
        .frame(maxWidth: .infinity)
    }
}

extension Store {
    /// The chat tab is up on this thread (window focus aside): reading counts.
    var isViewingCandidate: Bool { tab == .chat && windowKey }

    /// An explicit open (rail row, note row, progress card, fleet bead): its replies and its
    /// card notes are read. Notes are read only here and when one lands on screen.
    func open(_ key: String) {
        if key != thread { ThreadOpenMark.keep(key, readAt(key)) }
        thread = key
        markRead(key)
        readNotes(key)
    }
}

/// The read mark a thread had just before `Store.open` read it: its update cards newer than that are the ones
/// Hayden has not seen, and the newest two play when the thread opens (CONTRACT 10). A thread switched to directly
/// (a launch flag, the harness) is still unread when its list first draws, so the store's own mark serves there.
@MainActor
enum ThreadOpenMark {
    private static var marks: [String: Double] = [:]
    static func keep(_ key: String, _ mark: Double) { marks[key] = mark }
    static func take(_ key: String, store: Store) -> Double { marks.removeValue(forKey: key) ?? store.readAt(key) }
}

struct Hello: View {
    let agent: ThreadAgent?
    let tap: Tap?

    var body: some View {
        let (big, small): (String, String) = if let agent {
            ("Hey.", "A direct line to \(agent.title). Say anything.")
        } else if tap != nil {
            ("Boarding.", "This tap hasn’t said anything yet. It posts its plan here when it takes off.")
        } else {
            ("The space is open.", "Agents join with tap.py listen --as NAME. Your taps show up here on their own.")
        }
        VStack(spacing: 6) {
            Text(big)
                .font(.system(size: 22, weight: .semibold))
                .tracking(-0.4)
                .foregroundStyle(Color.ink)
            Text(small)
                .font(.system(size: 12.5))
                .foregroundStyle(Color.faint)
                .multilineTextAlignment(.center)
                .lineSpacing(3)
                .frame(maxWidth: 320)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}

private let bubbleR: CGFloat = 18
private let bubbleTail: CGFloat = 6

/// One message. Received bubbles spring in (blur + rise + scale). A bubble
/// Hayden just sent starts exactly where the composer field is, its text
/// still ink on the field's white, then lifts into place while it inks over
/// to black: the text leaves the composer and becomes the bubble.
struct BubbleRow: View {
    let m: DisplayMessage
    let box: SpringBox?
    /// An unseen card at its thread's open (BubbleAnimator): it waits for the open to settle, then plays.
    var opens = false
    let maxWidth: CGFloat
    var geo: ChatGeometry? = nil
    @State private var row: CGRect = .zero
    @State private var size: CGSize = .zero

    var body: some View {
        let p = box?.value ?? 1
        let _ = ProcessInfo.processInfo.environment["CHATEXP_COUNT"] != nil ? Harness.log("BUBBLE \(m.id)") : ()
        let flying = m.fly && box != nil && p != 1
        let measured = row != .zero && size != .zero && geo?.composer ?? .zero != .zero
        let lift = flying && measured ? flight() : nil
        let shape = UnevenRoundedRectangle(cornerRadii: .init(topLeading: bubbleR, bottomLeading: m.mine ? bubbleR : bubbleTail,
                                                              bottomTrailing: m.mine ? bubbleTail : bubbleR, topTrailing: bubbleR),
                                           style: .continuous)
        VStack(alignment: m.mine ? .trailing : .leading, spacing: 4) {
            VStack(alignment: m.mine ? .trailing : .leading, spacing: 6) {
                if let card = m.card {
                    // Its own surface and beats (once per session, the card's own claim). Arriving, the row's
                    // received-bubble entrance below is beat 0; at an open, it holds and then arrives on its own.
                    ChatCardView(row: card, images: m.images, play: box != nil || opens,
                                 width: min(maxWidth, ChatCardView.maxWidth), hold: opens ? BubbleAnimator.openHold : 0)
                        .equatable()
                } else {
                    if !m.images.isEmpty {
                        ChatImageGrid(paths: m.images, mine: m.mine, maxWidth: maxWidth)
                    }
                    if !m.body.isEmpty || m.images.isEmpty {
                        BubbleBody(m: m, ink: flying ? smoothstep(0.08, 0.6, p) : 1, shape: shape)
                    }
                }
            }
            .onGeometryChange(for: CGSize.self, of: { $0.size }) { s in if m.fly { size = s } }
            MetaLine(ts: m.ts, receipt: m.receipt)
                .padding(.horizontal, 6)
                .opacity(flying ? smoothstep(0.55, 1, p) : 1)
        }
        .frame(maxWidth: maxWidth, alignment: m.mine ? .trailing : .leading)
        .frame(maxWidth: .infinity, alignment: m.mine ? .trailing : .leading)
        .modifier(BubbleEntrance(p: p, fly: m.fly, mine: m.mine, lift: lift, hidden: flying && !measured))
        .background {
            if flying {
                Color.clear.onGeometryChange(for: CGRect.self, of: { $0.frame(in: .named("pane")) }) { row = $0 }
            }
        }
    }

    /// Offset from the bubble's resting place to the composer field: the
    /// text bubble's bottom starts on the field's bottom, so any images ride
    /// up from just above it, where the tray held them.
    /// Nil when the resting place is off screen (scrolled up): then it rises in plainly.
    private func flight() -> CGSize? {
        guard let c = geo?.composer else { return nil }
        let restX = m.mine ? row.maxX - size.width : row.minX
        let dy = c.maxY - (row.minY + size.height)
        guard dy > 0, dy < 640 + size.height else { return nil }
        return CGSize(width: c.minX - restX, height: dy)
    }
}

struct BubbleBody: View {
    let m: DisplayMessage
    /// 0 = still the composer's ink-on-white text, 1 = the settled bubble.
    let ink: Double
    let shape: UnevenRoundedRectangle

    var body: some View {
        let fill: Color = m.mine ? .ink : .bubbleGrey
        ZStack(alignment: .topLeading) {
            if ink < 1 {
                MarkdownText(m.body, dark: false).opacity(1 - ink)
            }
            MarkdownText(m.body, dark: m.mine).opacity(ink)
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 10)
        .background {
            ZStack {
                if ink < 1 { shape.fill(Color.white) }
                shape.fill(fill).opacity(ink)
            }
        }
    }
}

/// One modifier chain for every case (no branches, so a bubble keeps its
/// identity as it goes from waiting-for-geometry to flying to settled).
struct BubbleEntrance: ViewModifier {
    let p: Double
    let fly: Bool
    let mine: Bool
    /// A flight path out of the composer, when one is known.
    let lift: CGSize?
    /// A sent bubble waiting one layout pass for its flight path.
    let hidden: Bool

    func body(content: Content) -> some View {
        let k = 1 - p
        let anchor: UnitPoint = mine ? .bottomTrailing : .bottomLeading
        let scale: Double = if lift != nil { 1 - 0.03 * sin(.pi * clamp01(p)) }
            else if fly { 0.94 + 0.06 * p } else { 0.98 + 0.02 * p }
        let dx = (lift?.width ?? 0) * k
        let dy = lift.map { $0.height * k } ?? (fly ? 22 : 10) * k
        let blur = lift != nil ? 0 : (fly ? 8 : 10) * clamp01(k)
        let alpha = hidden ? 0 : (lift != nil ? 1 : clamp01(p * 1.3))
        content
            .scaleEffect(scale, anchor: anchor)
            .offset(x: dx, y: dy)
            .blur(radius: blur)
            .opacity(alpha)
    }
}

struct MetaLine: View {
    let ts: Double
    let receipt: DisplayMessage.Receipt?

    var body: some View {
        TimelineView(.periodic(from: .now, by: 30)) { ctx in
            HStack(spacing: 4) {
                Text(when(ts, now: ctx.date.timeIntervalSince1970))
                if let receipt {
                    Text("·")
                    ReceiptLabel(receipt: receipt)
                }
            }
            .font(.system(size: 10.5))
            .tracking(0.2)
            .foregroundStyle(Color.faint)
        }
    }
}

/// sending -> delivered -> seen 👀, each word blurring into the next; the
/// eyes pop in with a little spring when GitHub shows the reaction.
struct ReceiptLabel: View {
    let receipt: DisplayMessage.Receipt

    var body: some View {
        Group {
            switch receipt {
            case .sending:
                Text("sending")
            case .delivered:
                Text("delivered")
            case .seen:
                HStack(spacing: 3) {
                    Text("seen")
                    Text("👀").transition(.scale(scale: 0.2).combined(with: .opacity))
                }
                .foregroundStyle(Color.tapBlue)
            }
        }
        .transition(.blurReplace)
        .id(receipt)
        .animation(.spring(response: 0.42, dampingFraction: 0.62), value: receipt)
    }
}

struct TypingBubble: View {
    /// A tap thread says what the dots mean, in the meta line's voice.
    var caption: String? = nil

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            TypingDots()
                .frame(width: 26, height: 12)
                .padding(.horizontal, 14)
                .padding(.vertical, 12)
                .background(UnevenRoundedRectangle(cornerRadii: .init(topLeading: bubbleR, bottomLeading: bubbleTail,
                                                                      bottomTrailing: bubbleR, topTrailing: bubbleR),
                                                   style: .continuous).fill(Color.bubbleGrey))
            if let caption {
                Text(caption)
                    .font(.system(size: 10.5))
                    .tracking(0.2)
                    .foregroundStyle(Color.faint)
                    .padding(.horizontal, 6)
            }
        }
    }
}

// MARK: - Composer

struct ComposerBar: View {
    let store: Store
    let placeholder: String
    let enabled: Bool
    /// Agent threads take images; taps take text.
    var canAttach = false
    var focusToken = 0
    let geo: ChatGeometry
    let draft: ChatDraft
    @State private var text = ""
    @State private var height = SpringBox(44, .chatGrow)
    @State private var focused = false

    var body: some View {
        let hasText = !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        let canSend = enabled && (hasText || (canAttach && !draft.attachments.isEmpty))
        VStack(spacing: 0) {
            ChatTray(draft: draft, visible: canAttach)
            HStack(alignment: .bottom, spacing: 10) {
                AttachButton(shown: canAttach && enabled) { draft.pick(store: store) }
                GrowingTextView(text: $text, placeholder: placeholder, fontSize: 13.5, returnSends: true,
                                focusToken: focusToken,
                                onSend: send,
                                onHeight: { h in height.set(min(160, max(44, h + 20)), .chatGrow) },
                                onFocus: { f in withAnimation(.spring(response: 0.3, dampingFraction: 0.8)) { focused = f } },
                                onPaste: { pb in
                                    guard let sources = ChatImageSource.from(pb) else { return false }
                                    draft.add(sources, store: store)
                                    return true
                                })
                    .padding(.horizontal, 14)
                    .padding(.vertical, 10)
                    .modifier(ComposerField(height: height, focused: focused, geo: geo))
                SendButton(ready: canSend, action: send)
            }
            .padding(EdgeInsets(top: 12, leading: 14, bottom: 14, trailing: 14))
        }
        // The drive's hooks are refreshed whenever the thread or its state changes, so `enabled` and `canAttach` are current.
        .onChange(of: "\(store.thread)|\(enabled)|\(canAttach)", initial: true) {
            guard ChatHarness.enabled else { return }
            ChatHarness.draft = draft
            ChatHarness.compose = { text = $0 }
            ChatHarness.send = send
        }
    }

    private func send() {
        let body = text.trimmingCharacters(in: .whitespacesAndNewlines)
        let key = store.thread
        let withImages = canAttach && key.hasPrefix("agent:") && !draft.attachments.isEmpty
        if ChatHarness.enabled { Harness.log("CHATDRIVE send enabled=\(enabled) canAttach=\(canAttach) text=\(body.count) images=\(draft.attachments.count)") }
        guard enabled, !body.isEmpty || withImages else { return }
        text = ""
        guard withImages else {
            Task { @MainActor in
                if !(await store.send(body, to: key)) { text = body }
            }
            return
        }
        let images = draft.take()
        Task { @MainActor in
            if !(await ChatOutbox.shared.send(body, images: images, to: key, store: store)) {
                if text.isEmpty { text = body }
                draft.restore(images)
            }
        }
    }
}

/// The field's springing height lives here, so a growing composer re-renders
/// only its own frame (not the text view, not the bar).
struct ComposerField: ViewModifier {
    let height: SpringBox
    let focused: Bool
    let geo: ChatGeometry

    func body(content: Content) -> some View {
        let shape = RoundedRectangle(cornerRadius: 16, style: .continuous)
        content
            .frame(height: height.value)
            .background(shape.fill(focused ? Color.white : Color.black.opacity(0.025)))
            .overlay(shape.stroke(focused ? Color.black.opacity(0.18) : Color.hairline, lineWidth: 1))
            .onGeometryChange(for: CGRect.self, of: { $0.frame(in: .named("pane")) }) { geo.composer = $0 }
    }
}

struct SendButton: View {
    let ready: Bool
    let action: () -> Void
    @State private var s = SpringBox(0, .pop)

    var body: some View {
        let v = s.value
        Button(action: action) {
            Image(systemName: "arrow.up")
                .font(.system(size: 16, weight: .bold))
                .foregroundStyle(.white)
                .frame(width: 44, height: 44)
                .background(Circle().fill(Color.ink))
        }
        .buttonStyle(ChatPress(scale: 0.9))
        .disabled(!ready)
        .scaleEffect(0.86 + 0.14 * v)
        .opacity(0.25 + 0.75 * clamp01(v))
        .onAppear { s.snap(ready ? 1 : 0) }
        .onChange(of: ready) { _, r in s.set(r ? 1 : 0, r ? .pop : .snappy) }
    }
}

struct ExpPos: ViewModifier {
    @Binding var position: ScrollPosition
    func body(content: Content) -> some View {
        let e = ProcessInfo.processInfo.environment
        if e["CHATEXP_NOPOS"] != nil { content.defaultScrollAnchor(.bottom) }
        else if e["CHATEXP_INIT"] != nil { content.scrollPosition($position).defaultScrollAnchor(.bottom, for: .initialOffset).defaultScrollAnchor(.bottom) }
        else { content.scrollPosition($position).defaultScrollAnchor(.bottom) }
    }
}


struct SwitchingCar: View {
    var label = "Switching car"
    @State private var spin = false
    var body: some View {
        HStack(spacing: 8) {
            Circle().trim(from: 0, to: 0.72)
                .stroke(Color.tapBlue, style: StrokeStyle(lineWidth: 2, lineCap: .round))
                .frame(width: 12, height: 12)
                .rotationEffect(.degrees(spin ? 360 : 0))
                .animation(.linear(duration: 0.8).repeatForever(autoreverses: false), value: spin)
            Text(label.uppercased())
                .font(.system(size: 11.5, weight: .semibold)).tracking(0.7)
                .foregroundStyle(Color.tapBlue)
        }
        .padding(.horizontal, 14).padding(.vertical, 8)
        .background(Capsule().fill(Color.tapBlue.opacity(0.07)))
        .frame(maxWidth: .infinity)
        .onAppear { spin = true }
    }
}
