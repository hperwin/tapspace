import AppKit
import Observation
import SwiftUI

struct RootView: View {
    let store: Store
    let visual: VisualModel

    var body: some View {
        let p = store.tabProgress.value
        let lb = visual.lightbox?.progress.value ?? 0
        ZStack(alignment: .top) {
            Color.white
            ZStack(alignment: .top) {
                VisualView(model: visual, store: store)
                    .opacity(clamp01(1 - p))
                    .blur(radius: 10 * clamp01(p))
                    .scaleEffect(1 - 0.012 * clamp01(p))
                    .allowsHitTesting(store.tab == .visual)
                ChatView(store: store)
                    .padding(.top, 70)
                    .padding(.bottom, 22)
                    .opacity(clamp01(p))
                    .blur(radius: 10 * (1 - clamp01(p)))
                    .scaleEffect(0.988 + 0.012 * clamp01(p))
                    .allowsHitTesting(store.tab == .chat)
            }
            .blur(radius: 14 * clamp01(lb))
            TopBar(store: store)
            if !store.ticketsLoaded {
                ConnectingView(down: store.connection == .down)
            }
            if let box = visual.lightbox {
                LightboxView(lb: box, model: visual)
            }
            ToastView(store: store)
        }
        .coordinateSpace(.named("root"))
        .ignoresSafeArea()
        .environment(\.colorScheme, .light)
    }
}

struct TopBar: View {
    let store: Store

    var body: some View {
        ZStack(alignment: .top) {
            LinearGradient(stops: [.init(color: .white, location: 0.62), .init(color: .white.opacity(0), location: 1)],
                           startPoint: .top, endPoint: .bottom)
                .frame(height: 74)
                .allowsHitTesting(false)
            HStack(alignment: .top) {
                Text("TAPSPACE")
                    .font(.system(size: 12, weight: .semibold))
                    .tracking(4)
                    .foregroundStyle(Color.faint)
                    .padding(.leading, 88)
                    .padding(.top, 21)
                Spacer()
                PresenceHUD(store: store)
                    .padding(.trailing, 26)
                    .padding(.top, 14)
            }
            TabSwitcher(store: store)
                .padding(.top, 12)
        }
        .frame(maxWidth: .infinity, alignment: .top)
    }
}

// MARK: - Visual | Chat

/// The selection is one liquid white body: it stretches toward the target
/// segment first, trails a metaball droplet, and settles with a wobble.
struct TabSwitcher: View {
    let store: Store
    @State private var track = LiquidTrack(axis: .horizontal, cornerRadius: 10, dropRadius: 9.5, anchorRadius: 15)
    @State private var frames: [Tab: CGRect] = [:]

    var body: some View {
        HStack(spacing: 2) {
            segment(.visual, "Visual", store.visualBadge)
            segment(.chat, "Chat", store.chatBadge)
        }
        .padding(3)
        .coordinateSpace(.named("tabs"))
        .background {
            ZStack(alignment: .topLeading) {
                RoundedRectangle(cornerRadius: 13, style: .continuous).fill(Color.black.opacity(0.045))
                LiquidCanvas(track: track)
            }
        }
        .onChange(of: frames) { place(animated: true) }
        .onChange(of: store.tab) { place(animated: true) }
    }

    private func place(animated: Bool) {
        guard let r = frames[store.tab], r.width > 0 else { return }
        track.move(to: r, animated: animated)
    }

    private func segment(_ tab: Tab, _ title: String, _ count: Int) -> some View {
        Button { store.tab = tab } label: {
            HStack(spacing: 0) {
                Text(title)
                    .font(.system(size: 12, weight: .semibold))
                    .tracking(0.25)
                    .foregroundStyle(store.tab == tab ? Color.ink : Color.dim)
                Badge(count: count)
            }
            .padding(.horizontal, 18)
            .frame(height: 30)
            .contentShape(Rectangle())
        }
        .buttonStyle(PressStyle(scale: 0.97))
        .animation(.easeOut(duration: 0.25), value: store.tab)
        .onGeometryChange(for: CGRect.self, of: { $0.frame(in: .named("tabs")) }) { frames[tab] = $0 }
    }
}

/// Blue capsule with a numeric transition; it pops in on a spring and its
/// width opens so the segment (and the liquid under it) grows to fit.
struct Badge: View {
    let count: Int
    @State private var s = SpringBox(0, .pop)
    @State private var shown = 0

    var body: some View {
        let k = max(0, s.value)
        Text("\(shown)")
            .font(.system(size: 10, weight: .semibold))
            .monospacedDigit()
            .foregroundStyle(.white)
            .contentTransition(.numericText(value: Double(shown)))
            .padding(.horizontal, 5)
            .frame(minWidth: 16, minHeight: 16)
            .background(Capsule().fill(Color.tapBlue))
            .fixedSize()
            .scaleEffect(k)
            .frame(width: (shown >= 10 ? 22 : 16) * CGFloat(min(k, 1.15)), alignment: .center)
            .padding(.leading, 6 * CGFloat(min(k, 1)))
            .onAppear {
                shown = count
                s.snap(count > 0 ? 1 : 0)
            }
            .onChange(of: count) { _, n in
                if n > 0 { withAnimation(.smooth(duration: 0.45)) { shown = n } }
                s.set(n > 0 ? 1 : 0, .pop)
            }
    }
}

// MARK: - Presence HUD

@MainActor @Observable
final class PresenceModel {
    struct Entry: Identifiable {
        let id: String
        var row: ActivityRow
        let box = SpringBox(0, .gentle)
        var leaving = false
    }
    private(set) var entries: [Entry] = []

    func sync(_ rows: [ActivityRow]) {
        let top = Array(rows.prefix(4))
        for r in top {
            if let i = entries.firstIndex(where: { $0.id == r.key }) {
                if entries[i].row != r { entries[i].row = r }
                if entries[i].leaving {
                    entries[i].leaving = false
                    entries[i].box.set(1)
                }
            } else {
                let e = Entry(id: r.key, row: r)
                entries.append(e)
                e.box.set(1)
            }
        }
        for i in entries.indices where !entries[i].leaving && !top.contains(where: { $0.key == entries[i].id }) {
            entries[i].leaving = true
            let id = entries[i].id
            entries[i].box.set(0, .snappy, then: { [weak self] in
                self?.entries.removeAll { $0.id == id && $0.leaving }
            })
        }
    }
}

struct PresenceHUD: View {
    let store: Store
    @State private var model = PresenceModel()

    var body: some View {
        let s = store.sessions
        let working = min(s.working, 8)
        let idle = min(max(s.recent - s.working, 0), 8 - working)
        VStack(alignment: .trailing, spacing: 7) {
            HStack(spacing: 8) {
                SessionDots(working: working, idle: idle)
                    .frame(width: CGFloat(max(working + idle, 1)) * 10 - 5, height: 8)
                Group {
                    if store.connection == .live {
                        HStack(spacing: 0) {
                            Text("\(s.working)").contentTransition(.numericText(value: Double(s.working)))
                            Text(" WORKING · ")
                            Text("\(s.recent)").contentTransition(.numericText(value: Double(s.recent)))
                            Text(" SESSIONS")
                        }
                    } else {
                        Text(store.ticketsLoaded ? "RECONNECTING" : "CONNECTING")
                    }
                }
                .font(.system(size: 10.5))
                .tracking(0.6)
                .monospacedDigit()
                .foregroundStyle(Color.faint)
                .animation(.smooth(duration: 0.5), value: s)
            }
            ForEach(model.entries) { e in
                ActivityRowView(row: e.row, p: e.box.value)
            }
        }
        .allowsHitTesting(false)
        .onChange(of: store.activity, initial: true) { _, rows in model.sync(rows) }
    }
}

struct ActivityRowView: View {
    let row: ActivityRow
    let p: Double

    var body: some View {
        let fading = row.state == "fading"
        let style: RingStyle = fading ? .solid(Palette.faint)
            : row.kind == "waiting" ? .breathe(Palette.blue, period: 3)
            : row.kind == "decision" ? .chase(Palette.green)
            : .chase(Palette.ink)
        HStack(spacing: 7) {
            RingView(style: style, lineWidth: 1.8)
                .frame(width: 14, height: 14)
            Text(row.agent.uppercased())
                .font(.system(size: 10.5, weight: .semibold))
                .tracking(0.85)
                .foregroundStyle(Color.dim)
            Text("· " + row.verb)
                .font(.system(size: 10.5))
                .tracking(0.5)
                .foregroundStyle(row.kind == "waiting" && !fading ? Color.tapBlue.opacity(0.85) : Color.faint)
        }
        .opacity((fading ? 0.38 : 1) * clamp01(p))
        .blur(radius: 8 * (1 - clamp01(p)))
        .offset(y: -6 * (1 - p))
    }
}

// MARK: - Toast, connecting, the mark

struct ToastView: View {
    let store: Store
    @State private var p = SpringBox(0, SpringPreset(stiffness: 300, damping: 24))
    @State private var text = ""

    var body: some View {
        VStack {
            Spacer()
            Text(text)
                .font(.system(size: 12, weight: .medium))
                .foregroundStyle(.white)
                .padding(.horizontal, 14)
                .padding(.vertical, 9)
                .background(RoundedRectangle(cornerRadius: 12, style: .continuous).fill(Color.ink))
                .softShadow(0.8)
                .opacity(clamp01(p.value))
                .scaleEffect(0.94 + 0.06 * p.value)
                .offset(y: 20 * (1 - p.value))
                .padding(.bottom, 34)
        }
        .allowsHitTesting(false)
        .onChange(of: store.toast) { _, t in
            guard let t else { return }
            text = t.text
            p.set(1)
            Task { @MainActor in
                try? await Task.sleep(for: .seconds(2.6))
                if store.toast == t { p.set(0, .snappy) }
            }
        }
    }
}

struct ConnectingView: View {
    let down: Bool
    var body: some View {
        ZStack {
            Color.white
            Breathing(low: 0.3, high: 1, period: 2.8, scale: 0.06) {
                VStack(spacing: 14) {
                    TapMark(size: 34)
                    Text(down ? "STARTING TAPSPACE" : "CONNECTING")
                        .font(.system(size: 11.5, weight: .medium))
                        .tracking(2.2)
                        .foregroundStyle(Color.dim)
                }
            }
            .frame(width: 280, height: 120)
        }
    }
}

/// The TapSpace mark: a tap and its ripple.
struct TapMark: View {
    var size: CGFloat = 15
    var color: Color = .ink

    var body: some View {
        ZStack {
            Circle()
                .stroke(color.opacity(0.35), lineWidth: size * 0.09)
                .frame(width: size, height: size)
            Circle()
                .stroke(color.opacity(0.75), lineWidth: size * 0.1)
                .frame(width: size * 0.66, height: size * 0.66)
            Circle()
                .fill(color)
                .frame(width: size * 0.34, height: size * 0.34)
        }
        .frame(width: size, height: size)
    }
}
