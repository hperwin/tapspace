import AppKit
import QuartzCore
import SwiftUI

/// One thing waiting on Hayden, as a pill row.
struct PillItem: Identifiable, Equatable {
    enum Kind: Equatable { case card, tap(String), agent(String) }
    let id: String
    let kind: Kind
    let who: String
    let text: String
    let status: String
    let number: Int
}

final class PillPanel: NSPanel {
    override var canBecomeKey: Bool { false }
    override var canBecomeMain: Bool { false }
    // Keep the frame flush with the screen top, over the menu bar.
    override func constrainFrameRect(_ frameRect: NSRect, to screen: NSScreen?) -> NSRect { frameRect }
}

final class FirstMouseHostingView<Content: View>: NSHostingView<Content> {
    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }
}

/// The menu-bar liquid pill: SnappWAU's panel, display-link discipline and
/// global mouse monitor, mirrored to sit just right of the notch.
@MainActor
final class PillController: MotionDriver {
    let liquid = PillLiquid()
    let store: Store
    let motion = Motion.pill
    private(set) var panel: PillPanel!
    private var lastMouse: CGPoint?
    /// Verification: a scripted or pinned cursor instead of the real one.
    var simMouse: (() -> CGPoint?)?
    var onOpen: ((PillItem?) -> Void)?

    init(store: Store) { self.store = store }

    func install() {
        let panel = PillPanel(contentRect: NSRect(origin: .zero, size: PillLiquid.windowSize),
                              styleMask: [.borderless, .nonactivatingPanel], backing: .buffered, defer: false)
        panel.isOpaque = false
        panel.backgroundColor = .clear
        panel.hasShadow = false
        panel.hidesOnDeactivate = false
        panel.isMovable = false
        panel.ignoresMouseEvents = true
        panel.collectionBehavior = [.canJoinAllSpaces, .stationary, .fullScreenAuxiliary, .ignoresCycle]
        panel.level = .statusBar
        let hosting = FirstMouseHostingView(rootView: PillRoot(store: store, liquid: liquid, open: { [weak self] in self?.onOpen?($0) }))
        hosting.sizingOptions = []
        hosting.frame = NSRect(origin: .zero, size: PillLiquid.windowSize)
        panel.contentView = hosting
        self.panel = panel
        place()
        NotificationCenter.default.addObserver(forName: NSApplication.didChangeScreenParametersNotification,
                                               object: nil, queue: .main) { [weak self] _ in
            MainActor.assumeIsolated { self?.place() }
        }
        panel.orderFrontRegardless()

        motion.attach(to: hosting)
        motion.keepAwake = { [weak self] in self?.liquid.isTiming ?? false }

        let moves: NSEvent.EventTypeMask = [.mouseMoved, .leftMouseDragged, .rightMouseDragged]
        NSEvent.addGlobalMonitorForEvents(matching: moves) { [weak self] _ in
            MainActor.assumeIsolated { self?.wake(onlyIfNear: true) }
        }
        NSEvent.addLocalMonitorForEvents(matching: moves) { [weak self] event in
            MainActor.assumeIsolated { self?.wake(onlyIfNear: true) }
            return event
        }
        liquid.onNeedsFrames = { [weak self] in self?.wake(onlyIfNear: false) }
    }

    /// Pins the pill's LEFT edge just right of the notch (SnappWAU sits left
    /// of it), vertically centered in the menu bar. Without a notch it sits
    /// a little right of the menu bar's center.
    func place() {
        let screen = NSScreen.screens.first { $0.safeAreaInsets.top > 0 } ?? NSScreen.main ?? NSScreen.screens[0]
        let barHeight = screen.safeAreaInsets.top > 0
            ? screen.safeAreaInsets.top
            : max(screen.frame.maxY - screen.visibleFrame.maxY, 24)
        let notchRight = screen.auxiliaryTopRightArea?.minX ?? (screen.frame.midX + 90)
        let pillLeft = notchRight + 10
        liquid.top = ((barHeight - PillLiquid.pillH) / 2).rounded()
        let origin = NSPoint(x: pillLeft - liquid.anchorLeft, y: screen.frame.maxY - PillLiquid.windowSize.height)
        panel.setFrame(NSRect(origin: origin, size: PillLiquid.windowSize), display: true)
    }

    private func localMouse() -> CGPoint? {
        if let simMouse { return simMouse() }
        let m = NSEvent.mouseLocation
        let f = panel.frame
        return CGPoint(x: m.x - f.minX, y: f.maxY - m.y)
    }

    func wake(onlyIfNear: Bool) {
        if onlyIfNear, motion.isPaused, !liquid.expanded, liquid.isStill,
           let m = localMouse(), liquid.distanceToPill(m) > PillLiquid.reachRadius + 40 {
            return
        }
        motion.activate(self)
    }

    // MARK: MotionDriver

    func advance(_ dt: Double) -> Bool {
        let mouse = store.ticketsLoaded ? localMouse() : nil
        liquid.step(dt: dt, mouse: mouse)
        let wantsClicks = liquid.hot
        if panel.ignoresMouseEvents == wantsClicks { panel.ignoresMouseEvents = !wantsClicks }
        let cursorStill = mouse == lastMouse
        lastMouse = mouse
        return !(liquid.isStill && cursorStill && !liquid.isTiming) || simMouse != nil
    }

    func finish() {
        liquid.settle(mouse: store.ticketsLoaded ? localMouse() : nil, expanded: liquid.expanded)
    }
}

// MARK: - Views

struct PillRoot: View {
    let store: Store
    let liquid: PillLiquid
    let open: (PillItem?) -> Void

    var body: some View {
        let rect = liquid.shapeRect
        let e = liquid.expansion
        let body = liquid.bodyPath
        let size = PillLiquid.windowSize
        ZStack(alignment: .topLeading) {
            Canvas { ctx, _ in
                var shadowed = ctx
                shadowed.addFilter(.shadow(color: .black.opacity(lerp(0.16, 0.26, e)),
                                           radius: lerp(1.5, 18, e), x: 0, y: lerp(0.5, 9, e)))
                shadowed.fill(body, with: .color(.white))
                ctx.stroke(body, with: .color(.black.opacity(0.07)), lineWidth: 0.5)
            }

            PillContent(count: store.waitingTotal)
                .fixedSize()
                .onGeometryChange(for: CGFloat.self, of: { $0.size.width }) { liquid.setPillContentWidth($0) }
                .offset(x: liquid.headerOrigin.x, y: liquid.headerOrigin.y)

            ZStack(alignment: .topLeading) {
                PillDetails(store: store, liquid: liquid, open: open)
                    .frame(width: PillLiquid.cardW - 20, alignment: .leading)
                    .fixedSize(horizontal: false, vertical: true)
                    .onGeometryChange(for: CGFloat.self, of: { $0.size.height }) { liquid.setCardHeight(min($0 + 46, size.height - 30)) }
                    .offset(x: liquid.cardRect.minX + 10, y: liquid.top + 36)
                    .opacity(liquid.detailOpacity)
                    .offset(y: lerp(-6, 0, liquid.detailOpacity))
                    .allowsHitTesting(liquid.detailOpacity > 0.5)
            }
            .frame(width: size.width, height: size.height, alignment: .topLeading)
            .mask(RectShape(rect: rect, radius: liquid.cornerRadius))
        }
        .frame(width: size.width, height: size.height, alignment: .topLeading)
        .contentShape(RectShape(rect: rect, radius: liquid.cornerRadius))
        .onTapGesture { open(nil) }
        .opacity(store.ticketsLoaded ? 1 : 0)
        .scaleEffect(store.ticketsLoaded ? 1 : 0.8, anchor: UnitPoint(x: rect.minX / size.width, y: 0.05))
        .animation(.smooth(duration: 0.4), value: store.ticketsLoaded)
        .environment(\.colorScheme, .light)
    }
}

struct RectShape: Shape {
    var rect: CGRect
    var radius: CGFloat
    func path(in _: CGRect) -> Path { Path(roundedRect: rect, cornerRadius: radius, style: .continuous) }
}

struct PillContent: View {
    let count: Int

    var body: some View {
        HStack(spacing: 6) {
            TapMark(size: 14)
            Text(count, format: .number)
                .contentTransition(.numericText(value: Double(count)))
                .monospacedDigit()
                .font(.system(size: 13, weight: .semibold))
                .foregroundStyle(count > 0 ? Color.ink : Color.dim)
            if count > 0 {
                Circle().fill(Color.tapBlue).frame(width: 6, height: 6)
                    .transition(.scale.combined(with: .opacity))
            }
        }
        .frame(height: PillLiquid.pillH)
        .animation(.smooth(duration: 0.9), value: count)
    }
}

extension Store {
    /// Everything waiting on Hayden, in the order the pill lists it.
    var pillItems: [PillItem] {
        var out: [PillItem] = []
        for t in waitingCards {
            out.append(PillItem(id: "card:" + t.id, kind: .card, who: t.agent, text: t.title, status: "", number: 0))
        }
        for t in needsYouTaps {
            out.append(PillItem(id: t.key, kind: .tap(t.key), who: t.repoName, text: t.name, status: t.status, number: t.number))
        }
        for a in unreadAgents {
            let last = chats[a.name]?.last { $0.role == "master" }?.body ?? "replied"
            out.append(PillItem(id: a.key, kind: .agent(a.key), who: a.name, text: last.replacingOccurrences(of: "\n", with: " "), status: a.state, number: 0))
        }
        return out
    }
}

struct PillDetails: View {
    let store: Store
    let liquid: PillLiquid
    let open: (PillItem?) -> Void

    var body: some View {
        let items = Array(store.pillItems.prefix(8))
        let more = store.pillItems.count - items.count
        VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: 6) {
                Circle().fill(store.connection == .live ? Color.up : Color.orange).frame(width: 6, height: 6)
                Text("Waiting on you")
                Spacer(minLength: 0)
                Text(store.connection == .live ? "Live" : "Offline")
            }
            .font(.system(size: 11, weight: .medium))
            .foregroundStyle(Color.black.opacity(0.48))
            .padding(.horizontal, 4)
            .padding(.bottom, 9)

            Rectangle().fill(.black.opacity(0.07)).frame(height: 0.5)
                .padding(.bottom, 6)

            if items.isEmpty {
                Text("Nothing is waiting on you.")
                    .font(.system(size: 12, weight: .medium))
                    .foregroundStyle(Color.black.opacity(0.48))
                    .padding(.horizontal, 4)
                    .padding(.vertical, 8)
                    .opacity(0.2 + 0.8 * liquid.rowProgress(0))
            }
            ForEach(Array(items.enumerated()), id: \.element.id) { i, item in
                PillRow(item: item, progress: liquid.rowProgress(i)) { open(item) }
            }
            if more > 0 {
                Text("and \(more) more")
                    .font(.system(size: 11, weight: .medium))
                    .foregroundStyle(Color.black.opacity(0.4))
                    .padding(.horizontal, 4)
                    .padding(.top, 4)
            }
            PillRow(item: PillItem(id: "open", kind: .card, who: "", text: "Open TapSpace", status: "open", number: 0),
                    progress: liquid.rowProgress(items.count + 1)) { open(nil) }
                .padding(.top, 4)
        }
    }
}

struct PillRow: View {
    let item: PillItem
    let progress: Double
    let action: () -> Void
    @State private var hover = false

    var body: some View {
        Button(action: action) {
            HStack(spacing: 9) {
                icon.frame(width: 18, height: 18)
                if item.status == "open" {
                    Text(item.text)
                        .font(.system(size: 12, weight: .semibold))
                        .foregroundStyle(Color.ink)
                    Spacer(minLength: 0)
                    Image(systemName: "arrow.up.right")
                        .font(.system(size: 9.5, weight: .heavy))
                        .foregroundStyle(Color.dim)
                } else {
                    (Text(item.who).fontWeight(.semibold).foregroundStyle(Color.ink)
                        + Text(": " + item.text).foregroundStyle(Color.black.opacity(0.62)))
                        .font(.system(size: 12))
                        .lineLimit(1)
                        .truncationMode(.tail)
                    Spacer(minLength: 0)
                }
            }
            .padding(.horizontal, 6)
            .frame(height: 30)
            .background(RoundedRectangle(cornerRadius: 9, style: .continuous)
                .fill(Color.black.opacity(hover ? 0.045 : 0)))
            .contentShape(Rectangle())
        }
        .buttonStyle(PressStyle(scale: 0.98))
        .onHover { h in withAnimation(.easeOut(duration: 0.15)) { hover = h } }
        .opacity(0.2 + 0.8 * progress)
        .offset(y: 4 * (1 - progress))
    }

    @ViewBuilder private var icon: some View {
        switch item.kind {
        case .card:
            if item.status == "open" {
                TapMark(size: 13)
            } else {
                Image(systemName: "photo")
                    .font(.system(size: 10.5, weight: .semibold))
                    .foregroundStyle(Color.dim)
            }
        case .tap:
            TapAvatar(status: item.status, number: item.number, size: 18)
        case .agent:
            OrbView(state: item.status).frame(width: 15, height: 15)
        }
    }
}
