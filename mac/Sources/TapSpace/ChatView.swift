import AppKit
import Observation
import SwiftUI

private let statusLabel = ["needs-you": "Needs you", "flying": "Flying", "boarding": "Boarding", "landed": "Landed"]
private let agentLabel = ["thinking": "Thinking", "here": "Here", "away": "Away"]

struct ChatView: View {
    let store: Store

    var body: some View {
        HStack(alignment: .top, spacing: 22) {
            RailView(store: store)
                .frame(width: 300)
            PaneView(store: store)
        }
        .padding(.horizontal, 40)
        .frame(maxWidth: 1240)
        .frame(maxWidth: .infinity)
    }
}

// MARK: - Rail

struct RailView: View {
    let store: Store
    @State private var track = LiquidTrack(axis: .vertical, cornerRadius: 16, dropRadius: 12,
                                           anchorRadius: 22, squish: 0.06)
    @State private var frames: [String: CGRect] = [:]

    var body: some View {
        ScrollView(.vertical) {
            VStack(alignment: .leading, spacing: 8) {
                RailHead(title: "Agents")
                if store.agents.isEmpty {
                    RailEmpty(text: store.agentsLoaded
                              ? "No agents in the space yet. Any agent joins with tap.py listen --as NAME."
                              : "Looking for agents…")
                }
                ForEach(Array(store.agents.enumerated()), id: \.element.key) { i, a in
                    RailRow(key: a.key, index: i, store: store, onFrame: { frames[a.key] = $0 }) {
                        OrbView(state: a.state).frame(width: 38, height: 38)
                        RowText(title: a.name, sub: agentSub(a))
                    }
                }
                RailHead(title: "Your taps")
                if store.taps.items.isEmpty {
                    RailEmpty(text: tapsEmptyText)
                }
                ForEach(Array(store.taps.items.enumerated()), id: \.element.key) { i, t in
                    RailRow(key: t.key, index: store.agents.count + i, store: store, onFrame: { frames[t.key] = $0 }) {
                        TapAvatar(status: t.status, number: t.number)
                        RowText(title: t.name, sub: tapSub(t))
                    }
                }
            }
            .padding(.horizontal, 6)
            .padding(.top, 2)
            .padding(.bottom, 20)
            .coordinateSpace(.named("rail"))
            .background(alignment: .topLeading) { LiquidCanvas(track: track) }
            // Agents re-sort by state (thinking first): rows glide, the body follows.
            .animation(.spring(response: 0.5, dampingFraction: 0.82), value: store.agents.map(\.key))
        }
        .scrollIndicators(.never)
        .onChange(of: frames) { place(animated: true) }
        .onChange(of: store.thread) { place(animated: true) }
        .onAppear { place(animated: false) }
    }

    private var tapsEmptyText: String {
        if store.taps.fetched > 0 { return "No taps flying under your name." }
        return store.taps.error.isEmpty ? "Looking for your taps…" : "Couldn’t reach GitHub: " + store.taps.error
    }

    private func agentSub(_ a: Agent) -> String {
        let state = agentLabel[a.state, default: a.state]
        guard let w = a.where, !w.isEmpty else { return state }
        return state + " · " + w
    }

    private func tapSub(_ t: Tap) -> String {
        var s = statusLabel[t.status, default: t.status] + " · " + t.repoName
        if let last = t.messages.last(where: { $0.who == "tap" }) { s += " · " + when(last.date) }
        return s
    }

    private func place(animated: Bool) {
        guard let r = frames[store.thread], r.height > 0 else { track.hide(); return }
        track.move(to: r, animated: animated)
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
                UnreadDot(on: store.isUnread(key))
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
/// dotted slow spin = boarding, green check = landed.
struct TapAvatar: View {
    let status: String
    let number: Int
    var size: CGFloat = 38

    var body: some View {
        let style: RingStyle = switch status {
        case "flying": .spin(Palette.ink, length: 0.27, period: 1.6)
        case "boarding": .dotted(Palette.faint, period: 6)
        case "needs-you": .breathe(Palette.blue, period: 1.5)
        default: .solid(Palette.green)
        }
        let bg: Color = switch status {
        case "needs-you": Color.tapBlue.opacity(0.08)
        case "landed": Color.up.opacity(0.09)
        default: Color(white: 0.957)
        }
        let fg: Color = status == "needs-you" ? .tapBlue : (status == "landed" ? .up : .dim)
        ZStack {
            Circle().fill(bg)
            RingView(style: style, lineWidth: 2.2 * size / 38, track: NSColor.black.withAlphaComponent(0.06))
            Group {
                if status == "landed" {
                    Image(systemName: "checkmark")
                        .font(.system(size: 11 * size / 38, weight: .bold))
                        .transition(.symbolEffect(.drawOn))
                } else {
                    Text("#" + String(String(number).suffix(4)))
                        .font(.system(size: 9.5 * size / 38, weight: .bold))
                        .monospacedDigit()
                        .tracking(-0.2)
                        .lineLimit(1)
                        .minimumScaleFactor(0.6)
                        .frame(width: size * 0.7)
                        .transition(.blurReplace)
                }
            }
            .foregroundStyle(fg)
        }
        .frame(width: size, height: size)
        .animation(.smooth(duration: 0.4), value: status)
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
    var fly = false
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

/// The list normally clips at its own frame; while a sent bubble is lifting
/// out of the composer it may draw below that edge too.
struct FlightMask: ViewModifier {
    let geo: ChatGeometry

    func body(content: Content) -> some View {
        content.mask(alignment: .top) {
            Rectangle().padding(.bottom, geo.flights > 0 ? -420 : 0)
        }
    }
}

/// Bubble entrances: a new message springs in (blur + offset + scale); a
/// message Hayden just sent flies up out of the composer.
@MainActor
final class BubbleAnimator {
    private var boxes: [String: SpringBox] = [:]
    private var known: Set<String> = []
    private var thread = ""
    weak var geo: ChatGeometry?

    func sync(thread: String, keys: [String], fly: Set<String>) {
        if thread != self.thread {
            self.thread = thread
            known = Set(keys)
            boxes.removeAll()
            return
        }
        for k in keys where !known.contains(k) {
            known.insert(k)
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
}

/// The thread swap: header, goals and messages blur up into place.
struct SwapIn: ViewModifier {
    let box: SpringBox
    var rise: CGFloat = 6

    func body(content: Content) -> some View {
        let p = box.value
        content
            .opacity(clamp01(p))
            .blur(radius: 8 * (1 - clamp01(p)))
            .offset(y: rise * (1 - p))
    }
}

struct PaneView: View {
    let store: Store
    @State private var animator = BubbleAnimator()
    @State private var geo = ChatGeometry()
    @State private var swap = SpringBox(1, .gentle)
    @State private var focusToken = 0

    var body: some View {
        let key = store.thread
        let agent = store.agent(key)
        let tap = store.tap(key)
        let shape = RoundedRectangle(cornerRadius: 22, style: .continuous)
        VStack(spacing: 0) {
            if agent != nil || tap != nil {
                PaneHeader(agent: agent, tap: tap)
                    .modifier(SwapIn(box: swap, rise: 4))
                Rectangle().fill(Color.hairline).frame(height: 1)
                if let tap, !tap.goals.isEmpty {
                    GoalsStrip(goals: tap.goals)
                        .modifier(SwapIn(box: swap, rise: 4))
                    Rectangle().fill(Color.hairline).frame(height: 1)
                }
            }
            MessageList(store: store, agent: agent, tap: tap, animator: animator, geo: geo)
                .modifier(SwapIn(box: swap, rise: 10))
                .zIndex(1)
            Rectangle().fill(Color.hairline).frame(height: 1)
            ComposerBar(store: store, placeholder: placeholder(agent, tap),
                        enabled: agent != nil || tap != nil, focusToken: focusToken, geo: geo)
        }
        .coordinateSpace(.named("pane"))
        .clipShape(shape)
        .background(shape.fill(.white).softShadow())
        .onChange(of: store.thread) {
            swap.snap(0)
            swap.set(1)
            focusToken += 1
        }
    }

    private func placeholder(_ agent: Agent?, _ tap: Tap?) -> String {
        if let agent { return "Message \(agent.name)…" }
        return tap != nil ? "Message this tap…" : "Message…"
    }
}

struct PaneHeader: View {
    let agent: Agent?
    let tap: Tap?

    var body: some View {
        HStack(spacing: 12) {
            if let agent {
                OrbView(state: agent.state).frame(width: 38, height: 38)
                VStack(alignment: .leading, spacing: 2) {
                    HeaderTitle(text: agent.name)
                    Text(agentSubtitle(agent))
                        .font(.system(size: 11.5))
                        .foregroundStyle(Color.dim)
                        .lineLimit(1)
                        .truncationMode(.middle)
                }
                Spacer(minLength: 8)
                StatusChip(state: agent.state, label: agentLabel[agent.state, default: agent.state])
            } else if let tap {
                TapAvatar(status: tap.status, number: tap.number)
                VStack(alignment: .leading, spacing: 2) {
                    HeaderTitle(text: tap.name)
                    HStack(spacing: 4) {
                        Text(tap.key + " ·")
                        if let url = URL(string: tap.url) { IssueLink(url: url) }
                    }
                    .font(.system(size: 11.5))
                    .foregroundStyle(Color.dim)
                }
                Spacer(minLength: 8)
                StatusChip(state: tap.status, label: statusLabel[tap.status, default: tap.status])
            }
        }
        .foregroundStyle(Color.ink)
        .padding(.horizontal, 20)
        .padding(.top, 16)
        .padding(.bottom, 12)
    }

    private func agentSubtitle(_ a: Agent) -> String {
        ["Agent", a.where ?? "", "joined " + when(a.first ?? a.seen)].filter { !$0.isEmpty }.joined(separator: " · ")
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
        case "thinking", "needs-you": (Palette.blue, Color.tapBlue.opacity(0.1), 1.0)
        case "flying": (Palette.ink, Color.black.opacity(0.045), 1.2)
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

struct MessageList: View {
    let store: Store
    let agent: Agent?
    let tap: Tap?
    let animator: BubbleAnimator
    let geo: ChatGeometry
    @State private var position = ScrollPosition(edge: .bottom)
    @State private var nearBottom = true
    @State private var lastThread = ""
    @State private var pinUntil = Date.distantPast

    private var messages: [DisplayMessage] {
        let key = store.thread
        var out: [DisplayMessage] = []
        if let agent {
            for m in store.chats[agent.name] ?? [] {
                out.append(DisplayMessage(id: "\(key)#\(m.id)", mine: m.role == "you", body: m.body, ts: m.ts))
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
        // Thinking means a message of Hayden's is unanswered; once the reply is
        // in the thread the dots go, without waiting for the next agents poll.
        let typing = agent?.state == "thinking" && msgs.last?.mine == true
        let keys = msgs.map { store.confirmed[$0.id] ?? $0.id }
        let _ = animator.sync(thread: store.thread, keys: keys, fly: Set(msgs.filter(\.fly).map(\.id)))
        let sig = keys.joined(separator: ",") + "|\(typing)|" + msgs.map { $0.receipt.map { "\($0)" } ?? "" }.joined()
        GeometryReader { box in
            let maxW = min(620, box.size.width * 0.82)
            ScrollView(.vertical) {
                VStack(spacing: 12) {
                    if msgs.isEmpty {
                        Hello(agent: agent, tap: tap)
                            .frame(minHeight: box.size.height - 60)
                    }
                    ForEach(Array(zip(msgs, keys)), id: \.0.id) { m, k in
                        BubbleRow(m: m, box: animator.box(k), maxWidth: maxW, geo: geo)
                    }
                    if typing {
                        TypingBubble()
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .transition(.asymmetric(
                                insertion: .scale(scale: 0.4, anchor: .bottomLeading).combined(with: .opacity),
                                removal: .scale(scale: 0.8, anchor: .bottomLeading).combined(with: .opacity)))
                    }
                }
                .padding(.horizontal, 22)
                .padding(.top, 22)
                .padding(.bottom, 12)
                .animation(.spring(response: 0.42, dampingFraction: 0.66), value: typing)
            }
            .scrollClipDisabled()
            .scrollPosition($position)
            .defaultScrollAnchor(.bottom)
            // Stay pinned to the end while content or the viewport changes size
            // (markdown settling, goals appearing, the composer growing), when we
            // were at the end before the change or a thread just opened.
            .onScrollGeometryChange(for: ListGeometry.self, of: { g in
                ListGeometry(content: g.contentSize.height, container: g.containerSize.height, offset: g.contentOffset.y)
            }, action: { old, new in
                nearBottom = new.gap < 120
                let resized = old.content != new.content || old.container != new.container
                if resized, old.gap < 120 || Date() < pinUntil { position.scrollTo(edge: .bottom) }
            })
            .onChange(of: sig, initial: true) {
                let switched = lastThread != store.thread
                lastThread = store.thread
                if switched { pinUntil = Date().addingTimeInterval(0.8) }
                if store.isViewingCandidate { store.markRead(store.thread) }
                guard switched || nearBottom else { return }
                DispatchQueue.main.async {
                    if switched { position.scrollTo(edge: .bottom) }
                    else { withAnimation(.spring(response: 0.45, dampingFraction: 0.9)) { position.scrollTo(edge: .bottom) } }
                }
            }
        }
        .modifier(FlightMask(geo: geo))
    }
}

struct ListGeometry: Equatable {
    let content: CGFloat
    let container: CGFloat
    let offset: CGFloat
    var gap: CGFloat { content - (offset + container) }
}

extension Store {
    /// The chat tab is up on this thread (window focus aside): reading counts.
    var isViewingCandidate: Bool { tab == .chat && windowKey }

    func open(_ key: String) {
        thread = key
        markRead(key)
    }
}

struct Hello: View {
    let agent: Agent?
    let tap: Tap?

    var body: some View {
        let (big, small): (String, String) = if let agent {
            ("Hey.", "A direct line to \(agent.name). Say anything.")
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
    let maxWidth: CGFloat
    var geo: ChatGeometry? = nil
    @State private var row: CGRect = .zero
    @State private var size: CGSize = .zero

    var body: some View {
        let p = box?.value ?? 1
        let flying = m.fly && box != nil && p != 1
        let measured = row != .zero && size != .zero && geo?.composer ?? .zero != .zero
        let lift = flying && measured ? flight() : nil
        let shape = UnevenRoundedRectangle(cornerRadii: .init(topLeading: bubbleR, bottomLeading: m.mine ? bubbleR : bubbleTail,
                                                              bottomTrailing: m.mine ? bubbleTail : bubbleR, topTrailing: bubbleR),
                                           style: .continuous)
        VStack(alignment: m.mine ? .trailing : .leading, spacing: 4) {
            BubbleBody(m: m, ink: flying ? smoothstep(0.08, 0.6, p) : 1, shape: shape)
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

    /// Offset from the bubble's resting place to the composer field.
    /// Nil when the resting place is off screen (scrolled up): then it rises in plainly.
    private func flight() -> CGSize? {
        guard let c = geo?.composer else { return nil }
        let restX = m.mine ? row.maxX - size.width : row.minX
        let dy = c.minY - row.minY
        guard dy > 0, dy < 640 else { return nil }
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
    var body: some View {
        TypingDots()
            .frame(width: 26, height: 12)
            .padding(.horizontal, 14)
            .padding(.vertical, 12)
            .background(UnevenRoundedRectangle(cornerRadii: .init(topLeading: bubbleR, bottomLeading: bubbleTail,
                                                                  bottomTrailing: bubbleR, topTrailing: bubbleR),
                                               style: .continuous).fill(Color.bubbleGrey))
    }
}

// MARK: - Composer

struct ComposerBar: View {
    let store: Store
    let placeholder: String
    let enabled: Bool
    var focusToken = 0
    let geo: ChatGeometry
    @State private var text = ""
    @State private var height = SpringBox(44, .chatGrow)
    @State private var focused = false

    var body: some View {
        let canSend = enabled && !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        HStack(alignment: .bottom, spacing: 10) {
            GrowingTextView(text: $text, placeholder: placeholder, fontSize: 13.5, returnSends: true,
                            focusToken: focusToken,
                            onSend: send,
                            onHeight: { h in height.set(min(160, max(44, h + 20)), .chatGrow) },
                            onFocus: { f in withAnimation(.spring(response: 0.3, dampingFraction: 0.8)) { focused = f } })
                .padding(.horizontal, 14)
                .padding(.vertical, 10)
                .modifier(ComposerField(height: height, focused: focused, geo: geo))
            SendButton(ready: canSend, action: send)
        }
        .padding(EdgeInsets(top: 12, leading: 14, bottom: 14, trailing: 14))
    }

    private func send() {
        let body = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard enabled, !body.isEmpty else { return }
        let key = store.thread
        text = ""
        Task { @MainActor in
            if !(await store.send(body, to: key)) { text = body }
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
