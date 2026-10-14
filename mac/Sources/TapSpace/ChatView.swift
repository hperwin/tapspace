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
                    RailEmpty(text: "No agents in the space yet. Any agent joins with tap.py listen --as NAME.")
                }
                ForEach(Array(store.agents.enumerated()), id: \.element.key) { i, a in
                    RailRow(key: a.key, index: i, store: store, onFrame: { frames[a.key] = $0 }) {
                        OrbView(state: a.state).frame(width: 38, height: 38)
                        RowText(title: a.name, sub: agentLabel[a.state, default: a.state] + ((a.where ?? "").isEmpty ? "" : " · " + (a.where ?? "")))
                    }
                }
                RailHead(title: "Your taps")
                if store.taps.items.isEmpty {
                    RailEmpty(text: store.taps.fetched > 0 ? "No taps flying under your name."
                              : (store.taps.error.isEmpty ? "Looking for your taps…" : "Couldn’t reach GitHub: " + store.taps.error))
                }
                ForEach(Array(store.taps.items.enumerated()), id: \.element.key) { i, t in
                    RailRow(key: t.key, index: store.agents.count + i, store: store, onFrame: { frames[t.key] = $0 }) {
                        TapAvatar(status: t.status, number: t.number)
                        RowText(title: t.name, sub: statusLabel[t.status, default: t.status] + " · " + t.repoName
                                + (t.messages.last { $0.who == "tap" }.map { " · " + when($0.date) } ?? ""))
                    }
                }
            }
            .padding(.horizontal, 6)
            .padding(.top, 2)
            .padding(.bottom, 20)
            .coordinateSpace(.named("rail"))
            .background(alignment: .topLeading) { LiquidCanvas(track: track) }
        }
        .scrollIndicators(.never)
        .onChange(of: frames) { place(animated: true) }
        .onChange(of: store.thread) { place(animated: true) }
        .onAppear { place(animated: false) }
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
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

/// A rail row: staggered blur-in on arrival, a hover wash, and its frame
/// reported so the one liquid selection body can spring to it.
struct RailRow<Content: View>: View {
    let key: String
    let index: Int
    let store: Store
    let onFrame: (CGRect) -> Void
    @ViewBuilder var content: Content
    @State private var appear = SpringBox(0, .gentle)
    @State private var hover = false

    var body: some View {
        let p = appear.value
        let selected = store.thread == key
        HStack(spacing: 12) {
            content
            UnreadDot(on: store.isUnread(key))
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 16, style: .continuous)
            .fill(Color.black.opacity(hover && !selected ? 0.03 : 0)))
        .contentShape(RoundedRectangle(cornerRadius: 16, style: .continuous))
        .onGeometryChange(for: CGRect.self, of: { $0.frame(in: .named("rail")) }, action: onFrame)
        .onTapGesture { store.open(key) }
        .onHover { h in withAnimation(.easeOut(duration: 0.2)) { hover = h } }
        .opacity(clamp01(p))
        .blur(radius: 12 * (1 - clamp01(p)))
        .offset(y: 8 * (1 - p))
        .onAppear { appear.set(1, delay: 0.07 * Double(min(index, 10))) }
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
                } else {
                    Text("#" + String(String(number).suffix(4)))
                        .font(.system(size: 9.5 * size / 38, weight: .bold))
                        .monospacedDigit()
                        .tracking(0.2)
                }
            }
            .foregroundStyle(fg)
            .contentTransition(.symbolEffect(.replace))
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

/// Bubble entrances: a new message springs in (blur + offset + scale); a
/// message Hayden just sent flies up from the composer.
@MainActor
final class BubbleAnimator {
    private var boxes: [String: SpringBox] = [:]
    private var known: Set<String> = []
    private var thread = ""

    func sync(thread: String, keys: [String], fly: Set<String>) {
        if thread != self.thread {
            self.thread = thread
            known = Set(keys)
            boxes.removeAll()
            return
        }
        for k in keys where !known.contains(k) {
            known.insert(k)
            let b = SpringBox(0, fly.contains(k) ? SpringPreset(stiffness: 230, damping: 23) : .gentle)
            b.set(1)
            boxes[k] = b
        }
    }

    func box(_ k: String) -> SpringBox? { boxes[k] }
}

struct PaneView: View {
    let store: Store
    @State private var animator = BubbleAnimator()
    @State private var swap = SpringBox(1, .gentle)

    var body: some View {
        let key = store.thread
        let agent = store.agent(key)
        let tap = store.tap(key)
        let shape = RoundedRectangle(cornerRadius: 22, style: .continuous)
        VStack(spacing: 0) {
            if agent != nil || tap != nil {
                PaneHeader(agent: agent, tap: tap)
                    .opacity(clamp01(swap.value))
                    .blur(radius: 8 * (1 - clamp01(swap.value)))
                    .offset(y: 4 * (1 - swap.value))
                Rectangle().fill(Color.hairline).frame(height: 1)
                if let tap, !tap.goals.isEmpty {
                    GoalsStrip(goals: tap.goals)
                    Rectangle().fill(Color.hairline).frame(height: 1)
                }
            }
            MessageList(store: store, agent: agent, tap: tap, animator: animator)
            Rectangle().fill(Color.hairline).frame(height: 1)
            ComposerBar(store: store, placeholder: agent.map { "Message \($0.name)…" } ?? (tap != nil ? "Message this tap…" : "Message…"),
                        enabled: agent != nil || tap != nil)
        }
        .clipShape(shape)
        .background(shape.fill(.white).softShadow())
        .onChange(of: store.thread) { swap.snap(0); swap.set(1) }
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
                    Text(agent.name)
                        .font(.system(size: 15, weight: .semibold))
                        .tracking(-0.15)
                        .lineLimit(1)
                    Text(["Agent", agent.where ?? "", "joined " + when(agent.first ?? agent.seen)].filter { !$0.isEmpty }.joined(separator: " · "))
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
                    Text(tap.name)
                        .font(.system(size: 15, weight: .semibold))
                        .tracking(-0.15)
                        .lineLimit(1)
                    HStack(spacing: 4) {
                        Text(tap.key + " ·")
                        if let url = URL(string: tap.url) {
                            Link(destination: url) {
                                Text("Open issue").underline(true, color: Color.faint)
                            }
                            .foregroundStyle(Color.dim)
                            .pointerStyle(.link)
                        }
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
        .animation(.smooth(duration: 0.35), value: label)
    }
}

struct GoalsStrip: View {
    let goals: [TapGoal]

    var body: some View {
        let done = goals.filter(\.done).count
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 8) {
                HStack(spacing: 0) {
                    Text("\(done)")
                        .contentTransition(.numericText(value: Double(done)))
                    Text("/\(goals.count) GOALS")
                }
                .font(.system(size: 10.5, weight: .semibold))
                .tracking(0.8)
                .monospacedDigit()
                .foregroundStyle(Color.faint)
                .padding(.trailing, 4)
                .animation(.smooth, value: done)
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

    var body: some View {
        HStack(alignment: .top, spacing: 8) {
            ZStack {
                Circle()
                    .strokeBorder(goal.done ? Color.up : Color.faint, lineWidth: 1.5)
                    .background(Circle().fill(goal.done ? Color.up : .clear))
                if goal.done {
                    Image(systemName: "checkmark")
                        .font(.system(size: 7.5, weight: .heavy))
                        .foregroundStyle(.white)
                        .transition(.symbolEffect(.drawOn))
                }
            }
            .frame(width: 15, height: 15)
            .padding(.top, 1)
            Text(Markdown.inline(goal.text, dark: false))
                .font(.system(size: 11.5))
                .lineSpacing(1.5)
                .lineLimit(2)
                .foregroundStyle(goal.done ? Color.ink : Color.dim)
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 8)
        .frame(width: 230, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 12, style: .continuous)
            .fill(goal.done ? Color.up.opacity(0.07) : Color.black.opacity(0.03)))
        .help(goal.text)
        .animation(.smooth, value: goal.done)
    }
}

struct MessageList: View {
    let store: Store
    let agent: Agent?
    let tap: Tap?
    let animator: BubbleAnimator
    @State private var position = ScrollPosition(edge: .bottom)
    @State private var nearBottom = true
    @State private var lastThread = ""

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
                out.append(DisplayMessage(id: "\(key)#\(i)", mine: mine, body: m.body, ts: m.date,
                                          receipt: mine ? ((m.seen ?? false) ? .seen : .delivered) : nil))
            }
        }
        for p in store.pending where p.key == key {
            out.append(DisplayMessage(id: p.id, mine: true, body: p.body, ts: p.ts,
                                      receipt: tap != nil ? .sending : nil, fly: true))
        }
        return out
    }

    var body: some View {
        let msgs = messages
        let typing = agent?.state == "thinking"
        let keys = msgs.map { store.confirmed[$0.id] ?? $0.id }
        let _ = animator.sync(thread: store.thread, keys: keys, fly: Set(msgs.filter(\.fly).map(\.id)))
        let sig = keys.joined(separator: ",") + "|\(typing)|" + msgs.map { "\($0.receipt.map { "\($0)" } ?? "")" }.joined()
        GeometryReader { geo in
            let maxW = min(620, geo.size.width * 0.82)
            ScrollView(.vertical) {
                VStack(spacing: 12) {
                    if msgs.isEmpty {
                        Hello(agent: agent, tap: tap)
                            .frame(minHeight: geo.size.height - 60)
                    }
                    ForEach(Array(zip(msgs, keys)), id: \.0.id) { m, k in
                        BubbleRow(m: m, box: animator.box(k), maxWidth: maxW)
                    }
                    if typing {
                        TypingBubble()
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .transition(.opacity.combined(with: .offset(y: 8)))
                    }
                }
                .padding(.horizontal, 22)
                .padding(.top, 22)
                .padding(.bottom, 12)
                .animation(.smooth(duration: 0.35), value: typing)
            }
            .scrollPosition($position)
            .defaultScrollAnchor(.bottom)
            .onScrollGeometryChange(for: Bool.self, of: { g in
                g.contentSize.height - (g.contentOffset.y + g.containerSize.height) < 120
            }, action: { _, near in nearBottom = near })
            .onChange(of: sig, initial: true) {
                let switched = lastThread != store.thread
                lastThread = store.thread
                guard switched || nearBottom else { return }
                DispatchQueue.main.async {
                    if switched { position.scrollTo(edge: .bottom) }
                    else { withAnimation(.smooth(duration: 0.4)) { position.scrollTo(edge: .bottom) } }
                }
                if store.isViewingCandidate { store.markRead(store.thread) }
            }
        }
    }
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

struct BubbleRow: View {
    let m: DisplayMessage
    let box: SpringBox?
    let maxWidth: CGFloat

    var body: some View {
        let p = box?.value ?? 1
        let fly = m.fly
        let shape = UnevenRoundedRectangle(cornerRadii: .init(topLeading: 18, bottomLeading: m.mine ? 18 : 6,
                                                              bottomTrailing: m.mine ? 6 : 18, topTrailing: 18),
                                           style: .continuous)
        VStack(alignment: m.mine ? .trailing : .leading, spacing: 4) {
            MarkdownText(m.body, dark: m.mine)
                .padding(.horizontal, 14)
                .padding(.vertical, 10)
                .background(shape.fill(m.mine ? Color.ink : Color.bubbleGrey))
            MetaLine(ts: m.ts, receipt: m.receipt)
                .padding(.horizontal, 6)
        }
        .frame(maxWidth: maxWidth, alignment: m.mine ? .trailing : .leading)
        .frame(maxWidth: .infinity, alignment: m.mine ? .trailing : .leading)
        .scaleEffect((fly ? 0.94 : 0.98) + (fly ? 0.06 : 0.02) * p, anchor: m.mine ? .bottomTrailing : .bottomLeading)
        .offset(y: (fly ? 64 : 10) * (1 - p))
        .blur(radius: (fly ? 8 : 10) * (1 - clamp01(p)))
        .opacity(clamp01(p * 1.3))
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
                    Group {
                        switch receipt {
                        case .sending:
                            Text("sending")
                        case .delivered:
                            Text("delivered")
                        case .seen:
                            Text("seen 👀").foregroundStyle(Color.tapBlue)
                        }
                    }
                    .transition(.blurReplace)
                    .id(receipt)
                }
            }
            .font(.system(size: 10.5))
            .tracking(0.2)
            .foregroundStyle(Color.faint)
            .animation(.smooth(duration: 0.45), value: receipt)
        }
    }
}

struct TypingBubble: View {
    var body: some View {
        TypingDots()
            .frame(width: 26, height: 12)
            .padding(.horizontal, 14)
            .padding(.vertical, 12)
            .background(UnevenRoundedRectangle(cornerRadii: .init(topLeading: 18, bottomLeading: 6, bottomTrailing: 18, topTrailing: 18),
                                               style: .continuous).fill(Color.bubbleGrey))
    }
}

// MARK: - Composer

struct ComposerBar: View {
    let store: Store
    let placeholder: String
    let enabled: Bool
    @State private var text = ""
    @State private var height = SpringBox(44, SpringPreset(stiffness: 320, damping: 24))
    @State private var focused = false

    var body: some View {
        let canSend = enabled && !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        HStack(alignment: .bottom, spacing: 10) {
            GrowingTextView(text: $text, placeholder: placeholder, fontSize: 13.5, returnSends: true,
                            onSend: send,
                            onHeight: { h in height.set(min(160, max(44, h + 20)), SpringPreset(stiffness: 320, damping: 24)) },
                            onFocus: { f in withAnimation(.easeOut(duration: 0.2)) { focused = f } })
                .padding(.horizontal, 14)
                .padding(.vertical, 10)
                .frame(height: height.value)
                .background(RoundedRectangle(cornerRadius: 16, style: .continuous)
                    .fill(focused ? Color.white : Color.black.opacity(0.025)))
                .overlay(RoundedRectangle(cornerRadius: 16, style: .continuous)
                    .stroke(focused ? Color.black.opacity(0.18) : Color.hairline, lineWidth: 1))
            Button(action: send) {
                Image(systemName: "arrow.up")
                    .font(.system(size: 16, weight: .bold))
                    .foregroundStyle(.white)
                    .frame(width: 44, height: 44)
                    .background(Circle().fill(Color.ink))
            }
            .buttonStyle(PressStyle(scale: 0.9))
            .disabled(!canSend)
            .opacity(canSend ? 1 : 0.25)
            .animation(.easeOut(duration: 0.2), value: canSend)
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
