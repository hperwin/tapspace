import AppKit
import AVFoundation
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
    /// A card's current media path (thumbnail), empty otherwise.
    var media: String = ""
    var isVideo = false
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
    /// The hovered row's liquid highlight (springs from row to row).
    let highlight = PillHighlight()
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
        // While the card takes clicks, moves over it are ours (the global
        // monitor no longer sees them), so let the local monitor hear them.
        panel.acceptsMouseMovedEvents = true
        panel.collectionBehavior = [.canJoinAllSpaces, .stationary, .fullScreenAuxiliary, .ignoresCycle]
        panel.level = .statusBar
        let root = PillRoot(store: store, liquid: liquid, highlight: highlight,
                            open: { [weak self] in self?.onOpen?($0) })
        let hosting = FirstMouseHostingView(rootView: root)
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

        // The display link sleeps whenever nothing moves. Cursor motion
        // (no permissions needed for mouse events) and data changes wake it.
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
        highlight.track(mouse: mouse, liquid: liquid)
        let wantsClicks = liquid.hot
        if panel.ignoresMouseEvents == wantsClicks { panel.ignoresMouseEvents = !wantsClicks }
        let cursorStill = mouse == lastMouse
        lastMouse = mouse
        return !(liquid.isStill && cursorStill && !liquid.isTiming) || simMouse != nil
    }

    func finish() {
        let mouse = store.ticketsLoaded ? localMouse() : nil
        liquid.settle(mouse: mouse, expanded: liquid.expanded)
        highlight.track(mouse: mouse, liquid: liquid)
        highlight.finish()
    }
}

// MARK: - Row highlight

/// The hovered row's highlight: ONE soft grey liquid body that springs from
/// row to row (stretching toward the next row and trailing a droplet), the
/// way the chat rail's selection travels, instead of per-row backgrounds.
/// Hover is computed from the same cursor the liquid sees, so it works while
/// the app is in the background.
@MainActor
final class PillHighlight {
    let body = LiquidTrack(axis: .vertical, cornerRadius: 9, dropRadius: 5, anchorRadius: 9,
                           squish: 0.08, motion: .pill)
    let alpha = SpringBox(0, .snappy, motion: .pill)
    /// Row frames in window space (top-left origin), by row index.
    var rows: [Int: CGRect] = [:]
    private(set) var hovered: Int?

    func track(mouse: CGPoint?, liquid: PillLiquid) {
        var hit: Int?
        if let m = mouse, liquid.expanded, liquid.detailOpacity > 0.6 {
            hit = rows.first { $0.value.insetBy(dx: -4, dy: 0).contains(m) }?.key
        }
        guard hit != hovered else { return }
        hovered = hit
        if let i = hit, let r = rows[i] {
            // Born in place when it was gone; otherwise it travels.
            body.move(to: r, animated: alpha.value > 0.05)
            alpha.set(1, .snappy)
        } else {
            alpha.set(0, SpringPreset(stiffness: 300, damping: 34))
        }
    }

    func finish() {
        body.finish()
        alpha.finish()
    }
}

// MARK: - Views

struct PillRoot: View {
    let store: Store
    let liquid: PillLiquid
    let highlight: PillHighlight
    let open: (PillItem?) -> Void

    var body: some View {
        let rect = liquid.shapeRect
        let radius = liquid.cornerRadius
        let size = PillLiquid.windowSize
        ZStack(alignment: .topLeading) {
            PillBodyCanvas(liquid: liquid)

            PillContent(count: store.waitingTotal)
                .fixedSize()
                .onGeometryChange(for: CGFloat.self, of: { $0.size.width }) { liquid.setPillContentWidth($0) }
                .offset(x: liquid.headerOrigin.x, y: liquid.headerOrigin.y)

            ZStack(alignment: .topLeading) {
                PillHighlightCanvas(highlight: highlight, liquid: liquid)

                PillDetails(store: store, liquid: liquid, highlight: highlight, open: open)
                    .frame(width: PillLiquid.cardW - 20, alignment: .leading)
                    .fixedSize(horizontal: false, vertical: true)
                    .onGeometryChange(for: CGFloat.self, of: { $0.size.height }) { liquid.setCardHeight(min($0 + 46, size.height - 30)) }
                    .offset(x: liquid.cardRect.minX + 10, y: liquid.top + 36)
                    .opacity(liquid.detailOpacity)
                    .offset(y: lerp(-6, 0, liquid.detailOpacity))
                    .allowsHitTesting(liquid.detailOpacity > 0.5)
            }
            .frame(width: size.width, height: size.height, alignment: .topLeading)
            .mask(RectShape(rect: rect, radius: radius))
        }
        .frame(width: size.width, height: size.height, alignment: .topLeading)
        .coordinateSpace(.named(PillRoot.space))
        .contentShape(RectShape(rect: rect, radius: radius))
        .onTapGesture { open(nil) }
        .opacity(store.ticketsLoaded ? 1 : 0)
        .scaleEffect(store.ticketsLoaded ? 1 : 0.8, anchor: UnitPoint(x: rect.minX / size.width, y: 0.05))
        .animation(.smooth(duration: 0.4), value: store.ticketsLoaded)
        .environment(\.colorScheme, .light)
    }

    static let space = "pill"
}

/// The white liquid body (pill, bulge droplet, card). Its own view so a
/// bulge frame redraws only this canvas.
struct PillBodyCanvas: View {
    let liquid: PillLiquid

    var body: some View {
        let path = liquid.bodyPath
        let e = liquid.expansion
        Canvas { ctx, _ in
            var shadowed = ctx
            shadowed.addFilter(.shadow(color: .black.opacity(lerp(0.16, 0.26, e)),
                                       radius: lerp(1.5, 18, e), x: 0, y: lerp(0.5, 9, e)))
            shadowed.fill(path, with: .color(.white))
            ctx.stroke(path, with: .color(.black.opacity(0.07)), lineWidth: 0.5)
        }
        .allowsHitTesting(false)
    }
}

struct PillHighlightCanvas: View {
    let highlight: PillHighlight
    let liquid: PillLiquid

    var body: some View {
        let path = highlight.body.path
        let a = highlight.alpha.value * liquid.detailOpacity
        Canvas { ctx, _ in
            guard a > 0.01 else { return }
            ctx.fill(path, with: .color(.black.opacity(0.05 * a)))
        }
        .allowsHitTesting(false)
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
            out.append(PillItem(id: "card:" + t.id, kind: .card, who: t.agent, text: t.title, status: "", number: 0,
                                media: t.videoDeleted ? "" : t.mediaPath(t.version), isVideo: t.isVideo))
        }
        for t in needsYouTaps {
            out.append(PillItem(id: t.key, kind: .tap(t.key), who: t.repoName, text: t.name, status: t.status, number: t.number))
        }
        for a in unreadAgents {
            let last = chats[a.name]?.last { $0.role == "master" }?.body ?? "replied"
            out.append(PillItem(id: a.key, kind: .agent(a.key), who: a.name, text: pillPlain(last), status: a.state, number: 0))
        }
        return out
    }
}

/// One line of plain text from a markdown message.
func pillPlain(_ s: String) -> String {
    var t = s.replacingOccurrences(of: "\n", with: " ")
    for mark in ["**", "__", "`", "# "] { t = t.replacingOccurrences(of: mark, with: "") }
    return t.trimmingCharacters(in: .whitespaces)
}

struct PillDetails: View {
    let store: Store
    let liquid: PillLiquid
    let highlight: PillHighlight
    let open: (PillItem?) -> Void

    var body: some View {
        let all = store.pillItems
        let items = Array(all.prefix(8))
        let more = all.count - items.count
        VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: 6) {
                Circle().fill(store.connection == .live ? Color.up : Color.orange).frame(width: 6, height: 6)
                Text(all.isEmpty ? "All clear" : "Waiting on you")
                Spacer(minLength: 0)
                Text(store.connection == .live ? "Live" : "Offline")
            }
            .font(.system(size: 11, weight: .medium))
            .foregroundStyle(Color.black.opacity(0.48))
            .padding(.horizontal, 6)
            .padding(.bottom, 9)
            .opacity(0.2 + 0.8 * liquid.rowProgress(0))

            Rectangle().fill(.black.opacity(0.07)).frame(height: 0.5)
                .padding(.horizontal, 2)
                .padding(.bottom, 6)

            if items.isEmpty {
                Text("Nothing is waiting on you.")
                    .font(.system(size: 12, weight: .medium))
                    .foregroundStyle(Color.black.opacity(0.48))
                    .padding(.horizontal, 6)
                    .padding(.vertical, 8)
                    .opacity(0.2 + 0.8 * liquid.rowProgress(1))
            }
            ForEach(Array(items.enumerated()), id: \.element.id) { i, item in
                PillRow(item: item, index: i, highlight: highlight, progress: liquid.rowProgress(i + 1)) { open(item) }
            }
            if more > 0 {
                Text("and \(more) more")
                    .font(.system(size: 11, weight: .medium))
                    .foregroundStyle(Color.black.opacity(0.4))
                    .padding(.horizontal, 6)
                    .padding(.top, 4)
                    .opacity(0.2 + 0.8 * liquid.rowProgress(items.count + 1))
            }
            Rectangle().fill(.black.opacity(0.07)).frame(height: 0.5)
                .padding(.horizontal, 2)
                .padding(.top, 6)
                .padding(.bottom, 4)
                .opacity(liquid.rowProgress(items.count + 2))
            PillRow(item: PillItem(id: "open", kind: .card, who: "", text: "Open TapSpace", status: "open", number: 0),
                    index: PillRow.openIndex, highlight: highlight,
                    progress: liquid.rowProgress(items.count + 2)) { open(nil) }
        }
        .onChange(of: items.map(\.id)) { _, ids in
            // Rows that left take their frames with them.
            highlight.rows = highlight.rows.filter { $0.key == PillRow.openIndex || $0.key < ids.count }
        }
    }
}

struct PillRow: View {
    let item: PillItem
    let index: Int
    let highlight: PillHighlight
    let progress: Double
    let action: () -> Void

    static let openIndex = 1000

    var body: some View {
        Button(action: action) {
            HStack(spacing: 9) {
                icon.frame(width: 22, height: 22)
                if item.status == "open" {
                    Text(item.text)
                        .font(.system(size: 12, weight: .semibold))
                        .foregroundStyle(Color.ink)
                    Spacer(minLength: 0)
                    Image(systemName: "arrow.up.right")
                        .font(.system(size: 9.5, weight: .heavy))
                        .foregroundStyle(Color.dim)
                } else {
                    Text("\(Text(item.who).fontWeight(.semibold).foregroundStyle(Color.ink))  \(Text(item.text).foregroundStyle(Color.black.opacity(0.6)))")
                        .font(.system(size: 12))
                        .lineLimit(1)
                        .truncationMode(.tail)
                    Spacer(minLength: 0)
                }
            }
            .padding(.horizontal, 6)
            .frame(height: 32)
            .contentShape(Rectangle())
        }
        .buttonStyle(PressStyle(scale: 0.98))
        .onGeometryChange(for: CGRect.self, of: { $0.frame(in: .named(PillRoot.space)) }) { highlight.rows[index] = $0 }
        .opacity(0.2 + 0.8 * progress)
        .offset(y: 4 * (1 - progress))
    }

    @ViewBuilder private var icon: some View {
        switch item.kind {
        case .card:
            if item.status == "open" {
                TapMark(size: 14)
            } else {
                PillThumb(path: item.media, isVideo: item.isVideo)
            }
        case .tap:
            TapAvatar(status: item.status, number: item.number, size: 22)
        case .agent:
            OrbView(state: item.status).frame(width: 18, height: 18)
        }
    }
}

/// A card's media as a tiny rounded thumbnail (video: a play glyph over it).
struct PillThumb: View {
    let path: String
    let isVideo: Bool
    @State private var image: NSImage?

    var body: some View {
        ZStack {
            RoundedRectangle(cornerRadius: 5.5, style: .continuous).fill(Color(white: 0.95))
            if let image {
                Image(nsImage: image)
                    .resizable()
                    .interpolation(.high)
                    .aspectRatio(contentMode: .fill)
                    .frame(width: 22, height: 22)
                    .clipShape(RoundedRectangle(cornerRadius: 5.5, style: .continuous))
                    .transition(.opacity)
            } else if !isVideo {
                Image(systemName: "photo")
                    .font(.system(size: 9.5, weight: .semibold))
                    .foregroundStyle(Color.dim)
            }
            if isVideo {
                Image(systemName: "play.fill")
                    .font(.system(size: 8, weight: .bold))
                    .foregroundStyle(image == nil ? Color.dim : .white)
                    .shadow(color: .black.opacity(0.3), radius: 1.5)
            }
            RoundedRectangle(cornerRadius: 5.5, style: .continuous)
                .strokeBorder(.black.opacity(0.08), lineWidth: 0.5)
        }
        .frame(width: 22, height: 22)
        .task(id: path) {
            // Same size key as the board, so the board's decode is reused.
            guard !path.isEmpty else { image = nil; return }
            if isVideo {
                let img = await PillPoster.shared.frame(path)
                withAnimation(.smooth(duration: 0.3)) { image = img }
                return
            }
            if let hit = Images.shared.cached(path, maxPixel: 1600) { image = hit; return }
            let img = await Images.shared.load(path, maxPixel: 1600)
            withAnimation(.smooth(duration: 0.3)) { image = img }
        }
    }
}

/// First frames of card videos, for row thumbnails.
@MainActor
final class PillPoster {
    static let shared = PillPoster()
    private var cache: [String: NSImage] = [:]

    func frame(_ path: String) async -> NSImage? {
        if let hit = cache[path] { return hit }
        let gen = AVAssetImageGenerator(asset: AVURLAsset(url: API.base.appending(path: path)))
        gen.appliesPreferredTrackTransform = true
        gen.maximumSize = CGSize(width: 96, height: 96)
        guard let cg = try? await gen.image(at: .zero).image else { return nil }
        let img = NSImage(cgImage: cg, size: NSSize(width: cg.width, height: cg.height))
        cache[path] = img
        return img
    }
}
