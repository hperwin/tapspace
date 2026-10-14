import AppKit
import Observation
import SwiftUI

struct RootView: View {
    let store: Store
    let visual: VisualModel
    /// 0 while the first state is in flight, springs to 1 when it lands:
    /// the space blurs up into focus as the connecting mark dissolves.
    @State private var ready = SpringBox(0, SpringPreset(stiffness: 150, damping: 22))

    var body: some View {
        let p = clamp01(store.tabProgress.value)
        let moving = p > 0.001 && p < 0.999
        let lb = visual.lightbox?.progress.value ?? 0
        let r = clamp01(ready.value)
        ZStack(alignment: .top) {
            Color.white
            ZStack(alignment: .top) {
                // The two tabs trade places sideways, in the control's order:
                // Visual leaves left as Chat arrives from the right.
                VisualView(model: visual, store: store)
                    .opacity(1 - p)
                    .blur(radius: moving ? 10 * p : 0)
                    .scaleEffect(1 - 0.012 * p)
                    .offset(x: pixel(-28 * p))
                    .allowsHitTesting(store.tab == .visual)
                ChatView(store: store)
                    .padding(.top, 70)
                    .padding(.bottom, 22)
                    .opacity(p)
                    .blur(radius: moving ? 10 * (1 - p) : 0)
                    .scaleEffect(0.988 + 0.012 * p)
                    .offset(x: pixel(28 * (1 - p)))
                    .allowsHitTesting(store.tab == .chat)
            }
            .blur(radius: 14 * clamp01(lb))
            .opacity(r)
            .blur(radius: r < 0.999 ? 16 * (1 - r) : 0)
            .scaleEffect(0.985 + 0.015 * r)
            TopBar(store: store)
            if r < 0.999 {
                ConnectingView(down: store.connection == .down)
                    .opacity(1 - r)
                    .blur(radius: 10 * r)
                    .scaleEffect(1 + 0.05 * r)
                    .allowsHitTesting(false)
            }
            if let box = visual.lightbox {
                LightboxView(lb: box, model: visual)
            }
            ToastView(store: store)
        }
        .coordinateSpace(.named("root"))
        .ignoresSafeArea()
        .environment(\.colorScheme, .light)
        .onChange(of: store.ticketsLoaded, initial: true) { was, loaded in
            guard loaded else { return }
            // Already there when the window appeared: no entrance to play.
            if was == loaded { ready.snap(1) } else { ready.set(1) }
        }
    }
}

struct TopBar: View {
    let store: Store
    var chrome = ShellChrome.shared

    var body: some View {
        let mid = chrome.lightsMidY
        ZStack(alignment: .top) {
            LinearGradient(stops: [.init(color: .white, location: 0.62), .init(color: .white.opacity(0), location: 1)],
                           startPoint: .top, endPoint: .bottom)
                .frame(height: mid + 46)
                .allowsHitTesting(false)
            ShellDragArea()
                .frame(height: mid * 2)
            HStack(alignment: .top) {
                Text("TAPSPACE")
                    .font(.system(size: 12, weight: .semibold))
                    .tracking(4)
                    .foregroundStyle(Color.faint)
                    .frame(height: 16)
                    .padding(.leading, chrome.lightsMaxX + 22)
                    .padding(.top, pixel(mid - 8))
                    .allowsHitTesting(false)
                Spacer()
                PresenceHUD(store: store, fold: clamp01(store.tabProgress.value))
                    .padding(.trailing, 26)
                    .padding(.top, pixel(mid - PresenceHUD.lineHeight / 2))
            }
            TabSwitcher(store: store)
                .padding(.top, pixel(mid - TabSwitcher.height / 2))
        }
        .frame(maxWidth: .infinity, alignment: .top)
    }
}

// MARK: - Visual | Chat

/// The selection is one liquid white body: it stretches toward the target
/// segment first, trails a metaball droplet, and settles with a wobble. The
/// labels read the body itself, so ink pours into a segment as the water
/// arrives instead of on a timer.
struct TabSwitcher: View {
    static let height: CGFloat = 36
    let store: Store
    @State private var track = LiquidTrack(axis: .horizontal, cornerRadius: 10, dropRadius: 9.5, anchorRadius: 15)
    @State private var frames: [Tab: CGRect] = [:]

    var body: some View {
        let body = track.bodyRect
        HStack(spacing: 2) {
            segment(.visual, "Visual", store.visualBadge, cover(.visual, body))
            segment(.chat, "Chat", store.chatBadge, cover(.chat, body))
        }
        .padding(3)
        .coordinateSpace(.named("tabs"))
        .background {
            ZStack(alignment: .topLeading) {
                RoundedRectangle(cornerRadius: 13, style: .continuous).fill(Color.black.opacity(0.045))
                LiquidCanvas(track: track)
            }
        }
        .frame(height: Self.height)
        .onChange(of: frames) { place(animated: true) }
        .onChange(of: store.tab) { place(animated: true) }
    }

    /// How much of a segment the liquid body covers right now (0...1).
    private func cover(_ tab: Tab, _ body: CGRect) -> Double {
        guard let f = frames[tab], f.width > 0, body.width > 1 else { return store.tab == tab ? 1 : 0 }
        let overlap = min(body.maxX, f.maxX) - max(body.minX, f.minX)
        return smoothstep(0.15, 0.85, Double(max(0, overlap) / f.width))
    }

    private func place(animated: Bool) {
        guard let r = frames[store.tab], r.width > 0 else { return }
        track.move(to: r, animated: animated)
    }

    private func segment(_ tab: Tab, _ title: String, _ count: Int, _ k: Double) -> some View {
        Button { store.tab = tab } label: {
            HStack(spacing: 0) {
                Text(title)
                    .font(.system(size: 12, weight: .semibold))
                    .tracking(0.25)
                    .foregroundStyle(Color.dim.mix(with: .ink, by: k))
                Badge(count: count)
            }
            .padding(.horizontal, 18)
            .frame(height: 30)
            .contentShape(Rectangle())
        }
        .buttonStyle(ShellPressStyle(scale: 0.95))
        .onGeometryChange(for: CGRect.self, of: { $0.frame(in: .named("tabs")) }) { frames[tab] = $0 }
    }
}

/// Blue capsule with a numeric transition. It pops in on an underdamped
/// spring and its width opens with it, so the segment (and the liquid
/// under it) grows to fit; a new count gives it a little kick.
struct Badge: View {
    let count: Int
    @State private var s = SpringBox(0, .pop)
    @State private var shown = 0
    @State private var width: CGFloat = 16

    var body: some View {
        let k = max(0, s.value)
        let open = CGFloat(min(k, 1))
        Text("\(shown)")
            .font(.system(size: 10, weight: .semibold))
            .monospacedDigit()
            .foregroundStyle(.white)
            .contentTransition(.numericText(value: Double(shown)))
            .padding(.horizontal, 5)
            .frame(minWidth: 16, minHeight: 16)
            .background(Capsule().fill(Color.tapBlue))
            .fixedSize()
            .onGeometryChange(for: CGFloat.self, of: { $0.size.width }) { width = $0 }
            .scaleEffect(k)
            .opacity(clamp01(k * 2.5))
            .frame(width: pixel(width * open), height: 16)
            .padding(.leading, pixel(6 * open))
            .onAppear {
                shown = count
                s.snap(count > 0 ? 1 : 0)
            }
            .onChange(of: count) { old, n in
                if n > 0 { withAnimation(.smooth(duration: 0.45)) { shown = n } }
                if n > 0, old > 0, s.target == 1 {
                    s.kick(n > old ? 5 : -3)
                } else {
                    s.set(n > 0 ? 1 : 0, n > 0 ? .pop : .snappy)
                }
            }
    }
}

// MARK: - Presence HUD

@MainActor @Observable
final class PresenceModel {
    struct Entry: Identifiable {
        let id: String
        var row: ActivityRow
        let box: SpringBox
        var leaving = false
        /// The ring's own pop (underdamped), kicked when the row's kind changes.
        var ring: SpringBox? = nil
    }
    private(set) var entries: [Entry] = []

    func sync(_ rows: [ActivityRow]) {
        let top = Array(rows.prefix(4))
        for (n, r) in top.enumerated() {
            if let i = entries.firstIndex(where: { $0.id == r.key }) {
                if entries[i].row != r {
                    if entries[i].row.kind != r.kind || entries[i].row.state != r.state { entries[i].ring?.kick(-6) }
                    entries[i].row = r
                }
                if entries[i].leaving {
                    entries[i].leaving = false
                    entries[i].box.set(1, .gentle)
                }
            } else {
                let e = Entry(id: r.key, row: r, box: SpringBox(0, .gentle), ring: SpringBox(0, .pop))
                entries.append(e)
                let stagger = 0.06 * Double(n)
                e.box.set(1, delay: stagger)
                e.ring?.set(1, delay: stagger + 0.08)
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
    static let lineHeight: CGFloat = 14
    static let rowHeight: CGFloat = 22
    let store: Store
    /// 0 on Visual; 1 folds the activity rows away (the chat pane's header
    /// lives under them), leaving the session line.
    var fold: Double = 0
    @State private var model = PresenceModel()
    var chrome = ShellChrome.shared

    var body: some View {
        let s = store.sessions
        let working = min(s.working, 8)
        let idle = min(max(s.recent - s.working, 0), 8 - working)
        let open = 1 - clamp01(fold)
        VStack(alignment: .trailing, spacing: 0) {
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
                .animation(.smooth(duration: 0.4), value: store.connection == .live)
            }
            .frame(height: Self.lineHeight)
            .padding(.bottom, 4)
            ForEach(Array(model.entries.enumerated()), id: \.element.id) { i, e in
                // Each row closes its own height as it leaves, and the
                // chat tab folds them bottom-up.
                let fold = smoothstep(0.1 * Double(model.entries.count - 1 - i), 0.1 * Double(model.entries.count - 1 - i) + 0.7, open)
                let h = clamp01(e.box.value) * fold
                ActivityRowView(row: e.row, p: e.box.value * fold, ring: e.ring?.value ?? 1)
                    .frame(height: Self.rowHeight * CGFloat(h), alignment: .center)
                    .clipped(antialiased: false)
            }
        }
        .allowsHitTesting(false)
        .onGeometryChange(for: CGFloat.self, of: { $0.size.width }) { w in
            if chrome.hudWidth != w { chrome.hudWidth = w }
        }
        .onChange(of: openBottom, initial: true) { _, b in chrome.hudBottom = b }
        .onChange(of: store.activity, initial: true) { _, rows in model.sync(rows) }
    }

    /// Bottom of the HUD with every row open (ignores the Chat fold, so the
    /// board does not shift while the tabs trade places).
    private var openBottom: CGFloat {
        let rows = model.entries.reduce(0) { $0 + clamp01($1.box.value) }
        return pixel(chrome.lightsMidY - Self.lineHeight / 2) + Self.lineHeight + 4 + Self.rowHeight * CGFloat(rows)
    }
}

struct ActivityRowView: View {
    let row: ActivityRow
    let p: Double
    var ring: Double = 1

    var body: some View {
        let fading = row.state == "fading"
        let style: RingStyle = fading ? .solid(Palette.faint)
            : row.kind == "waiting" ? .breathe(Palette.blue, period: 3)
            : row.kind == "decision" ? .chase(Palette.green)
            : .chase(Palette.ink)
        HStack(spacing: 7) {
            RingView(style: style, lineWidth: 1.8)
                .frame(width: 14, height: 14)
                // Never 0: a singular transform on an AppKit-backed view asserts.
                .scaleEffect(max(0.05, ring))
                .opacity(clamp01(ring * 4))
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
        .blur(radius: p < 0.999 ? 8 * (1 - clamp01(p)) : 0)
        .offset(y: pixel(-6 * (1 - p)))
        .animation(.smooth(duration: 0.6), value: fading)
    }
}

// MARK: - Toast, connecting, the mark

struct ToastView: View {
    let store: Store
    @State private var p = SpringBox(0, SpringPreset(stiffness: 300, damping: 22))
    @State private var text = ""

    var body: some View {
        let v = p.value
        VStack {
            Spacer()
            Text(text)
                .font(.system(size: 12, weight: .medium))
                .foregroundStyle(.white)
                .opacity(smoothstep(0.45, 0.95, v))
                .padding(.horizontal, 14)
                .padding(.vertical, 9)
                .background(RoundedRectangle(cornerRadius: 12, style: .continuous).fill(Color.ink))
                .softShadow(0.8)
                // Like the pill spreading: width leads, the drop rounds out.
                .scaleEffect(x: 0.6 + 0.4 * v, y: 0.82 + 0.18 * v)
                .opacity(clamp01(v * 1.6))
                .blur(radius: v < 0.999 ? 6 * (1 - clamp01(v)) : 0)
                .offset(y: pixel(22 * (1 - v)))
                // Above the chat composer on Chat; near the bottom on Visual.
                .padding(.bottom, pixel(34 + 72 * clamp01(store.tabProgress.value)))
        }
        .allowsHitTesting(false)
        .onChange(of: store.toast) { _, t in
            guard let t else { return }
            text = t.text
            p.set(1, SpringPreset(stiffness: 300, damping: 22))
            Task { @MainActor in
                try? await Task.sleep(for: .seconds(2.6))
                if store.toast == t { p.set(0, .snappy) }
            }
        }
    }
}

/// The calm first-launch state: the mark breathing while the first state
/// loads. If it takes a moment (the app is starting the server with
/// `tap.py up`), the label says so.
struct ConnectingView: View {
    let down: Bool
    @State private var slow = false

    var body: some View {
        Breathing(low: 0.3, high: 1, period: 2.8, scale: 0.06) {
            VStack(spacing: 14) {
                TapMark(size: 34)
                Text(down || slow ? "STARTING TAPSPACE" : "CONNECTING")
                    .font(.system(size: 11.5, weight: .medium))
                    .tracking(2.2)
                    .foregroundStyle(Color.dim)
                    .contentTransition(.opacity)
            }
        }
        .frame(width: 280, height: 120)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .task {
            try? await Task.sleep(for: .seconds(2.5))
            withAnimation(.smooth(duration: 0.6)) { slow = true }
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
