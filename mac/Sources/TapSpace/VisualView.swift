import AVFoundation
import AVKit
import AppKit
import Observation
import SwiftUI

// MARK: - Models

@MainActor @Observable
final class CardModel: Identifiable {
    let id: String
    var ticket: Ticket
    var image: NSImage?
    var prevImage: NSImage?
    var poster: NSImage?
    var playing = false
    var highlights: PixelDiff.Result?
    var notesOpen = false
    var notesText = ""
    var focusToken = 0
    var sending = false
    var leaving = false
    var zooming = false
    /// The version Hayden sent notes on from here (shows "with agent" before the poll).
    var notesSentOn = 0
    @ObservationIgnored var placed = false
    @ObservationIgnored var mediaFrame: CGRect = .zero

    let appear = SpringBox(0, .gentle)
    let x = SpringBox(0, .liquid)
    let y = SpringBox(0, .liquid)
    let nw = SpringBox(0, .spreadW)
    let nh = SpringBox(0, .spreadH)
    let check = SpringBox(0, .draw)
    let collapseX = SpringBox(0, .retractW)
    let collapseY = SpringBox(0, .retractH)
    let swap = SpringBox(0, .gentle)
    let glow = SpringBox(0, .gentle)
    let bump = SpringBox(0, .pop)

    init(_ t: Ticket) {
        id = t.id
        ticket = t
    }

    var showsChanges: Bool {
        !notesOpen && (ticket.status == "changes" || notesSentOn == ticket.version)
    }
}

enum CardMetrics {
    static let meta: CGFloat = 34
    static let actions: CGFloat = 56
    static let stateRow: CGFloat = 46
    static func buttonW(_ w: CGFloat) -> CGFloat { (w - 24 - 8) / 2 }
    static func notQuite(_ w: CGFloat) -> CGRect { CGRect(x: 12 + buttonW(w) + 8, y: 10, width: buttonW(w), height: 34) }
    static func notes(_ w: CGFloat) -> CGRect { CGRect(x: 12, y: 52, width: w - 24, height: 132) }

    @MainActor static func bottom(_ c: CardModel, width w: CGFloat) -> CGFloat {
        if c.showsChanges && c.nh.value < 0.01 { return stateRow }
        let extra = notes(w).maxY + 12 - actions
        return max(actions, actions + extra * c.nh.value)
    }
}

@MainActor @Observable
final class Lightbox {
    let card: CardModel
    let aspect: CGFloat
    var image: NSImage
    var closing = false
    let progress = SpringBox(0, SpringPreset(stiffness: 300, damping: 28))

    init(card: CardModel, aspect: CGFloat, image: NSImage) {
        self.card = card
        self.aspect = aspect
        self.image = image
    }
}

/// The Visual tab's state: the cards, their spring grid, and the lightbox.
@MainActor @Observable
final class VisualModel {
    private(set) var cards: [CardModel] = []
    var lightbox: Lightbox?
    private(set) var size: CGSize = .zero
    private(set) var contentHeight: CGFloat = 0
    @ObservationIgnored let store: Store
    @ObservationIgnored private var stagger = 0

    init(store: Store) { self.store = store }

    var cardWidth: CGFloat { min(440, max(260, size.width * 0.24)) }
    var mediaHeight: CGFloat { max(200, size.height * 0.5 - 96) }
    var mediaReady: Bool { cards.allSatisfy { $0.ticket.isVideo ? ($0.poster != nil || $0.ticket.videoDeleted) : $0.image != nil } }

    func resize(_ s: CGSize) {
        guard s != size else { return }
        size = s
        relayout()
    }

    func sync(_ tickets: [Ticket]) {
        let live = tickets.filter { $0.status != "approved" }
        for t in live {
            if let c = cards.first(where: { $0.id == t.id }) {
                guard !c.leaving, c.ticket != t else { continue }
                let newVersion = t.version != c.ticket.version
                c.ticket = t
                if newVersion {
                    c.notesSentOn = 0
                    c.playing = false
                    loadMedia(c)
                }
            } else {
                let c = CardModel(t)
                cards.append(c)
                loadMedia(c)
                c.appear.set(1, delay: 0.03 + 0.12 * Double(stagger))
                stagger += 1
                Task { @MainActor [weak self] in
                    try? await Task.sleep(for: .seconds(1.2))
                    if let self { self.stagger = max(0, self.stagger - 1) }
                }
            }
        }
        // Approved or archived elsewhere: dissolve out.
        for c in cards where !c.leaving && !live.contains(where: { $0.id == c.id }) {
            leave(c)
        }
        relayout()
    }

    // MARK: Layout (positions are springs; heights follow the notes liquid)

    func relayout() {
        guard size.width > 0 else { return }
        let cw = cardWidth, gap: CGFloat = 26
        let cols = max(1, Int((size.width - 80 + gap) / (cw + gap)))
        var y: CGFloat = 0
        var i = 0
        while i < cards.count {
            let row = Array(cards[i..<min(i + cols, cards.count)])
            let rowW = CGFloat(row.count) * cw + CGFloat(row.count - 1) * gap
            var x = ((size.width - rowW) / 2).rounded()
            var rowH: CGFloat = 0
            for c in row {
                let h = CardMetrics.meta + mediaHeight + CardMetrics.bottom(c, width: cw)
                if !c.placed {
                    c.x.snap(x); c.y.snap(y)
                    c.placed = true
                } else {
                    if c.x.target != x { c.x.set(x, .liquid) }
                    if c.y.target != y { c.y.set(y, .liquid) }
                }
                x += cw + gap
                rowH = max(rowH, h)
            }
            y += rowH + gap
            i += cols
        }
        let h = max(0, y - gap)
        if h != contentHeight { contentHeight = h }
    }

    // MARK: Media

    private func loadMedia(_ c: CardModel) {
        let t = c.ticket
        let v = t.version
        c.highlights = nil
        if t.isVideo {
            c.prevImage = nil
            c.image = nil
            c.swap.snap(0)
            if t.videoDeleted { c.poster = nil; c.swap.set(1); return }
            Task { @MainActor in
                let img = await poster(for: API.url(t.mediaPath(v)))
                guard c.ticket.version == v else { return }
                c.poster = img
                c.swap.snap(0)
                c.swap.set(1)
            }
            return
        }
        Task { @MainActor in
            let img = await Images.shared.load(t.mediaPath(v), maxPixel: 1600)
            guard c.ticket.version == v, let img else { return }
            c.prevImage = c.image
            c.image = img
            c.poster = nil
            c.swap.snap(0)
            c.swap.set(1, then: { [weak c] in c?.prevImage = nil })
            await diff(c, v)
        }
    }

    private func diff(_ c: CardModel, _ v: Int) async {
        let t = c.ticket
        guard v >= 2, let cur = t.entry(v), let prev = t.entry(v - 1),
              !cur.isVideo, !prev.isVideo,
              let a = await Images.shared.load(t.mediaPath(v - 1), maxPixel: 1600)?.cgImage(forProposedRect: nil, context: nil, hints: nil),
              let b = await Images.shared.load(t.mediaPath(v), maxPixel: 1600)?.cgImage(forProposedRect: nil, context: nil, hints: nil)
        else { return }
        let result = await Task.detached(priority: .utility) { PixelDiff.regions(a, b) }.value
        guard c.ticket.version == v, let result else { return }
        c.highlights = result
        c.glow.snap(0)
        c.glow.set(1, delay: 0.55, then: { [weak c] in c?.glow.set(0.55) })
    }

    private func poster(for url: URL) async -> NSImage? {
        let gen = AVAssetImageGenerator(asset: AVURLAsset(url: url))
        gen.appliesPreferredTrackTransform = true
        gen.maximumSize = CGSize(width: 1600, height: 1600)
        guard let (cg, _) = try? await gen.image(at: CMTime(seconds: 0.6, preferredTimescale: 600)) else { return nil }
        return NSImage(cgImage: cg, size: NSSize(width: cg.width, height: cg.height))
    }

    // MARK: Decisions

    func approve(_ c: CardModel) {
        guard !c.leaving else { return }
        c.leaving = true
        if c.notesOpen { toggleNotes(c) }
        Task { @MainActor in
            guard await store.decide(c.ticket, approve: true) else { c.leaving = false; return }
            playApprove(c)
        }
    }

    /// The green ring and check draw themselves, then the card collapses
    /// like liquid and the grid reflows.
    func playApprove(_ c: CardModel) {
        c.leaving = true
        c.check.set(1, .draw, then: { [weak self, weak c] in
            guard let self, let c else { return }
            c.collapseY.set(1, .retractH, delay: 0.3)
            c.collapseX.set(1, .retractW, delay: 0.36, then: { [weak self, weak c] in
                guard let self, let c else { return }
                self.remove(c)
            })
        })
    }

    private func leave(_ c: CardModel) {
        c.leaving = true
        c.collapseY.set(1, .retractH)
        c.collapseX.set(1, .retractW, delay: 0.05, then: { [weak self, weak c] in
            guard let self, let c else { return }
            self.remove(c)
        })
    }

    private func remove(_ c: CardModel) {
        cards.removeAll { $0 === c }
        if lightbox?.card === c { lightbox = nil }
        relayout()
    }

    func toggleNotes(_ c: CardModel) {
        c.notesOpen.toggle()
        c.nh.onStep = { [weak self] _ in self?.relayout() }
        if c.notesOpen {
            c.nw.set(1, .spreadW)
            c.nh.set(1, .spreadH)
            c.focusToken += 1
        } else {
            // Height retracts first on the way back, like the pill.
            c.nw.set(0, .retractW)
            c.nh.set(0, .retractH)
        }
        relayout()
    }

    func sendNotes(_ c: CardModel) {
        let msg = c.notesText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !msg.isEmpty, !c.sending else { return }
        c.sending = true
        Task { @MainActor in
            let ok = await store.decide(c.ticket, approve: false, message: msg)
            c.sending = false
            guard ok else { return }
            c.notesText = ""
            c.notesSentOn = c.ticket.version
            if c.notesOpen { toggleNotes(c) }
        }
    }

    func focus(_ id: String) {
        guard let c = cards.first(where: { $0.id == id }) else { return }
        c.bump.snap(0)
        c.bump.kick(9)
    }

    // MARK: Lightbox

    func openLightbox(_ c: CardModel) {
        guard lightbox == nil, !c.ticket.isVideo, let img = c.image, img.size.height > 0 else { return }
        let lb = Lightbox(card: c, aspect: img.size.width / img.size.height, image: img)
        lightbox = lb
        c.zooming = true
        lb.progress.set(1)
        let path = c.ticket.mediaPath(c.ticket.version)
        Task { @MainActor in
            if let hi = await Images.shared.load(path, maxPixel: 3600), lightbox === lb { lb.image = hi }
        }
    }

    func closeLightbox() {
        guard let lb = lightbox, !lb.closing else { return }
        lb.closing = true
        lb.progress.set(0, SpringPreset(stiffness: 340, damping: 32), then: { [weak self, weak lb] in
            lb?.card.zooming = false
            if self?.lightbox === lb { self?.lightbox = nil }
        })
    }
}

// MARK: - Views

struct VisualView: View {
    let model: VisualModel
    let store: Store

    var body: some View {
        GeometryReader { geo in
            ScrollView(.vertical) {
                ZStack(alignment: .topLeading) {
                    ForEach(model.cards) { c in
                        PlacedCard(card: c, model: model)
                    }
                }
                .frame(width: geo.size.width, height: model.contentHeight, alignment: .topLeading)
                .padding(.top, 76)
                .padding(.bottom, 72)
            }
            .scrollIndicators(.automatic)
            .onChange(of: geo.size, initial: true) { _, s in model.resize(s) }
        }
        .overlay {
            if model.cards.isEmpty && store.ticketsLoaded {
                Breathing(low: 0.35, high: 0.75, period: 4.4) {
                    Text("THE SPACE IS CLEAR")
                        .font(.system(size: 13, weight: .regular))
                        .tracking(1.8)
                        .foregroundStyle(Color.faint)
                }
                .frame(width: 320, height: 40)
            }
        }
    }
}

/// Reads only its own position springs, so a moving card re-renders alone.
struct PlacedCard: View {
    let card: CardModel
    let model: VisualModel

    var body: some View {
        CardView(card: card, model: model, width: model.cardWidth, mediaH: model.mediaHeight)
            .offset(x: card.x.value, y: card.y.value)
    }
}

struct CardView: View {
    let card: CardModel
    let model: VisualModel
    let width: CGFloat
    let mediaH: CGFloat

    var body: some View {
        let a = card.appear.value
        let cx = card.collapseX.value, cy = card.collapseY.value
        let col = clamp01(max(cx, cy))
        let s = 0.955 + 0.045 * a
        let bump = 1 + 0.035 * card.bump.value
        let shape = RoundedRectangle(cornerRadius: 18, style: .continuous)
        VStack(spacing: 0) {
            CardMeta(card: card)
            CardMedia(card: card, model: model, height: mediaH)
            CardBottom(card: card, model: model, width: width)
        }
        .frame(width: width, alignment: .top)
        .clipShape(shape)
        .background(shape.fill(.white).softShadow(1 - col))
        .scaleEffect(x: s * (1 - 0.55 * cx) * bump, y: s * (1 - 0.55 * cy) * bump, anchor: .center)
        .blur(radius: 26 * (1 - clamp01(a)) + 18 * col)
        .opacity(clamp01(a * 1.15) * (1 - smoothstep(0.25, 0.9, col)))
        .offset(y: 14 * (1 - a))
        .allowsHitTesting(!card.leaving || card.check.value < 0.01)
    }
}

struct CardMeta: View {
    let card: CardModel

    var body: some View {
        let t = card.ticket
        HStack(alignment: .firstTextBaseline, spacing: 7) {
            Text(t.agent.uppercased())
                .font(.system(size: 10, weight: .semibold))
                .tracking(0.8)
                .foregroundStyle(Color.dim)
                .lineLimit(1)
                .fixedSize()
            Text(t.title)
                .font(.system(size: 11.5))
                .foregroundStyle(Color.dim.opacity(0.72))
                .lineLimit(1)
                .truncationMode(.tail)
                .frame(maxWidth: .infinity, alignment: .leading)
            TimelineView(.periodic(from: .now, by: 30)) { ctx in
                HStack(spacing: 0) {
                    Text("v")
                    Text("\(t.version)")
                        .contentTransition(.numericText(value: Double(t.version)))
                    Text(" · " + timeago(t.lastTs, now: ctx.date.timeIntervalSince1970))
                }
                .font(.system(size: 10))
                .monospacedDigit()
                .foregroundStyle(Color.faint)
                .animation(.smooth(duration: 0.5), value: t.version)
            }
        }
        .padding(.horizontal, 14)
        .frame(height: CardMetrics.meta)
        .help(t.title + ((t.current?.note ?? "").isEmpty ? "" : "\n\n" + (t.current?.note ?? "")))
    }
}

struct CardMedia: View {
    let card: CardModel
    let model: VisualModel
    let height: CGFloat

    var body: some View {
        let t = card.ticket
        ZStack {
            Color.well
            if t.videoDeleted {
                Text("VIDEO REVIEWED · DELETED PER HOUSE RULE")
                    .font(.system(size: 10.5))
                    .tracking(1)
                    .foregroundStyle(Color.faint)
                    .multilineTextAlignment(.center)
                    .padding(20)
            } else if t.isVideo {
                VideoWell(card: card)
            } else {
                ImageWell(card: card)
            }
            CheckVeil(progress: card.check.value)
        }
        .frame(height: height)
        .clipped()
        .contentShape(Rectangle())
        .onTapGesture { if !t.isVideo { model.openLightbox(card) } }
        .pointerStyle(t.isVideo ? nil : .zoomIn)
        .onGeometryChange(for: CGRect.self, of: { $0.frame(in: .named("root")) }) { card.mediaFrame = $0 }
    }
}

struct ImageWell: View {
    let card: CardModel

    var body: some View {
        let p = card.swap.value
        GeometryReader { geo in
            let box = CGRect(origin: .zero, size: geo.size)
            ZStack(alignment: .topLeading) {
                if let prev = card.prevImage {
                    Image(nsImage: prev).resizable().interpolation(.high)
                        .aspectRatio(contentMode: .fit)
                        .frame(width: geo.size.width, height: geo.size.height)
                        .opacity(1 - clamp01(p))
                        .blur(radius: 22 * clamp01(p))
                }
                if let img = card.image {
                    Image(nsImage: img).resizable().interpolation(.high)
                        .aspectRatio(contentMode: .fit)
                        .frame(width: geo.size.width, height: geo.size.height)
                        .opacity(clamp01(p))
                        .blur(radius: 22 * (1 - clamp01(p)))
                }
                if let h = card.highlights {
                    HighlightLayer(result: h, rect: aspectFit(h.aspect, in: box), glow: card.glow.value)
                }
            }
            .frame(width: geo.size.width, height: geo.size.height)
            .opacity(card.zooming ? 0 : 1)
        }
    }
}

struct HighlightLayer: View {
    let result: PixelDiff.Result
    let rect: CGRect
    let glow: Double
    var pad: CGFloat = 3

    var body: some View {
        ZStack(alignment: .topLeading) {
            ForEach(Array(result.boxes.enumerated()), id: \.offset) { _, b in
                let r = CGRect(x: rect.minX + b.minX * rect.width - pad, y: rect.minY + b.minY * rect.height - pad,
                               width: b.width * rect.width + pad * 2, height: b.height * rect.height + pad * 2)
                RoundedRectangle(cornerRadius: 7, style: .continuous)
                    .fill(Color.tapBlue.opacity(0.10))
                    .overlay(RoundedRectangle(cornerRadius: 7, style: .continuous).stroke(Color.tapBlue.opacity(0.28), lineWidth: 1))
                    .shadow(color: Color.tapBlue.opacity(0.14), radius: 7)
                    .frame(width: r.width, height: r.height)
                    .offset(x: r.minX, y: r.minY)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .opacity(clamp01(glow))
        .allowsHitTesting(false)
    }
}

struct CheckShape: Shape {
    func path(in r: CGRect) -> Path {
        let k = r.width / 76
        var p = Path()
        p.move(to: CGPoint(x: r.minX + 24 * k, y: r.minY + 39.5 * k))
        p.addLine(to: CGPoint(x: r.minX + 34 * k, y: r.minY + 49 * k))
        p.addLine(to: CGPoint(x: r.minX + 52 * k, y: r.minY + 28 * k))
        return p
    }
}

/// A green circle and check that draw themselves on a spring.
struct CheckVeil: View {
    let progress: Double

    var body: some View {
        if progress > 0.001 {
            let p = clamp01(progress)
            ZStack {
                Color.white.opacity(0.82 * smoothstep(0, 0.25, p))
                Circle()
                    .trim(from: 0, to: clamp01(p / 0.65))
                    .stroke(Color.up, style: StrokeStyle(lineWidth: 2.6, lineCap: .round))
                    .rotationEffect(.degrees(-90))
                    .frame(width: 60, height: 60)
                CheckShape()
                    .trim(from: 0, to: smoothstep(0.55, 1, p))
                    .stroke(Color.up, style: StrokeStyle(lineWidth: 3.4, lineCap: .round, lineJoin: .round))
                    .frame(width: 68, height: 68)
            }
            .scaleEffect(0.94 + 0.06 * p)
            .allowsHitTesting(false)
        }
    }
}

struct VideoWell: View {
    let card: CardModel
    @State private var hover = false

    var body: some View {
        ZStack {
            if card.playing {
                PlayerView(url: API.url(card.ticket.mediaPath(card.ticket.version)))
            } else {
                if let poster = card.poster {
                    Image(nsImage: poster).resizable().interpolation(.high)
                        .aspectRatio(contentMode: .fit)
                        .opacity(clamp01(card.swap.value))
                        .blur(radius: 22 * (1 - clamp01(card.swap.value)))
                }
                Button { card.playing = true } label: {
                    Image(systemName: "play.fill")
                        .font(.system(size: 18, weight: .semibold))
                        .foregroundStyle(Color.ink)
                        .offset(x: 1.5)
                        .frame(width: 52, height: 52)
                        .background(Circle().fill(.white.opacity(0.92)))
                        .softShadow(0.9)
                        .scaleEffect(hover ? 1.06 : 1)
                }
                .buttonStyle(PressStyle())
                .onHover { h in withAnimation(.spring(response: 0.3, dampingFraction: 0.6)) { hover = h } }
            }
        }
    }
}

struct PlayerView: NSViewRepresentable {
    let url: URL

    func makeNSView(context: Context) -> AVPlayerView {
        let v = AVPlayerView()
        v.controlsStyle = .inline
        v.videoGravity = .resizeAspect
        v.showsFullScreenToggleButton = true
        let p = AVPlayer(url: url)
        p.isMuted = true
        v.player = p
        p.play()
        return v
    }

    func updateNSView(_ v: AVPlayerView, context: Context) {
        if (v.player?.currentItem?.asset as? AVURLAsset)?.url != url {
            let p = AVPlayer(url: url)
            p.isMuted = true
            v.player = p
            p.play()
        }
    }

    static func dismantleNSView(_ v: AVPlayerView, coordinator: ()) {
        v.player?.pause()
    }
}

struct PressStyle: ButtonStyle {
    var scale: CGFloat = 0.965
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .scaleEffect(configuration.isPressed ? scale : 1)
            .animation(.spring(response: 0.22, dampingFraction: 0.62), value: configuration.isPressed)
    }
}

struct PillButton: View {
    let title: String
    var primary = false
    var active = false
    var disabled = false
    let action: () -> Void
    @State private var hover = false

    var body: some View {
        Button(action: action) {
            Text(title)
                .font(.system(size: 12.5, weight: .semibold))
                .tracking(0.1)
                .foregroundStyle(primary ? Color.white : Color.ink)
                .frame(maxWidth: .infinity)
                .frame(height: 34)
                .background(
                    RoundedRectangle(cornerRadius: 11, style: .continuous)
                        .fill(primary ? Color.ink.opacity(hover ? 0.86 : 1)
                              : Color(white: active ? 0.93 : (hover ? 0.935 : 0.955)))
                )
                .contentShape(Rectangle())
        }
        .buttonStyle(PressStyle())
        .disabled(disabled)
        .opacity(disabled ? 0.3 : 1)
        .onHover { hover = $0 }
        .animation(.easeOut(duration: 0.15), value: hover)
    }
}

/// Approve / Not quite, and the notes field that spreads out of "Not quite"
/// the way the WAU pill spreads into its card: width leads height going out,
/// height retracts first coming back, and a metaball neck tears as it opens.
struct CardBottom: View {
    let card: CardModel
    let model: VisualModel
    let width: CGFloat

    var body: some View {
        let nw = card.nw.value, nh = card.nh.value
        let open = nw > 0.002 || nh > 0.002
        ZStack(alignment: .topLeading) {
            if card.showsChanges && !open {
                HStack(spacing: 8) {
                    BreathingDot(color: Palette.faint, period: 2)
                        .frame(width: 6, height: 6)
                    Text("with \(card.ticket.agent), working on your notes")
                        .font(.system(size: 12))
                        .foregroundStyle(Color.dim)
                        .lineLimit(1)
                }
                .padding(.horizontal, 14)
                .frame(width: width, height: CardMetrics.stateRow, alignment: .leading)
                .transition(.opacity)
            } else {
                if open { NotesLiquid(nw: nw, nh: nh, width: width) }
                HStack(spacing: 8) {
                    PillButton(title: "Approve", primary: true) { model.approve(card) }
                    PillButton(title: "Not quite", active: card.notesOpen) { model.toggleNotes(card) }
                }
                .padding(.horizontal, 12)
                .padding(.top, 10)
                if open { NotesContent(card: card, model: model, width: width) }
            }
        }
        .frame(width: width, height: CardMetrics.bottom(card, width: width), alignment: .topLeading)
    }
}

struct NotesLiquid: View {
    let nw: Double
    let nh: Double
    let width: CGFloat

    var body: some View {
        let from = CardMetrics.notQuite(width)
        let to = CardMetrics.notes(width)
        let r = CGRect(x: lerp(from.minX, to.minX, nw), y: lerp(from.minY, to.minY, nh),
                       width: lerp(from.width, to.width, nw), height: max(1, lerp(from.height, to.height, nh)))
        let rad = min(lerp(11, 14, clamp01(nh)), r.height / 2)
        var path = Path(roundedRect: r, cornerRadius: rad, style: .continuous)
        // The neck between the button and the spreading body thins, then tears.
        let neckR = 15 * CGFloat(1 - smoothstep(0.62, 0.96, nh))
        if neckR > 0.5, r.minY > from.minY + 2 {
            let cx = min(max(from.midX, r.minX + neckR), r.maxX - neckR)
            if let neck = metaballNeck(CGPoint(x: from.midX, y: from.maxY - 15), 15,
                                       CGPoint(x: cx, y: r.minY + neckR), neckR) {
                path = path.union(neck)
            }
        }
        return Canvas { ctx, _ in
            ctx.fill(path, with: .color(Color(white: 0.955)))
        }
        .frame(width: width, height: to.maxY + 20)
        .allowsHitTesting(false)
    }
}

struct NotesContent: View {
    @Bindable var card: CardModel
    let model: VisualModel
    let width: CGFloat

    var body: some View {
        let r = CardMetrics.notes(width)
        let detail = smoothstep(0.62, 0.97, card.nw.value) * smoothstep(0.55, 0.97, card.nh.value)
        let empty = card.notesText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        VStack(spacing: 8) {
            GrowingTextView(text: $card.notesText, placeholder: "Tell the agent what to change…",
                            fontSize: 13, returnSends: false, focusToken: card.focusToken,
                            onSend: { model.sendNotes(card) })
                .padding(.horizontal, 10)
                .padding(.vertical, 7)
                .frame(height: 82)
                .background(RoundedRectangle(cornerRadius: 11, style: .continuous).fill(.white))
                .overlay(RoundedRectangle(cornerRadius: 11, style: .continuous).stroke(Color.black.opacity(0.07), lineWidth: 1))
            PillButton(title: card.sending ? "Sending" : "Send to agent", primary: true, disabled: empty || card.sending) {
                model.sendNotes(card)
            }
        }
        .padding(8)
        .frame(width: r.width, height: r.height, alignment: .top)
        .offset(x: r.minX, y: r.minY + lerp(-6, 0, detail))
        .opacity(detail)
        .allowsHitTesting(detail > 0.5)
    }
}

/// The zoom: the card's image flies out of its well to the center on a
/// spring (matched geometry driven by the same display link), and back.
struct LightboxView: View {
    let lb: Lightbox
    let model: VisualModel

    var body: some View {
        GeometryReader { geo in
            let p = lb.progress.value
            let size = geo.size
            let target = aspectFit(lb.aspect, in: CGRect(x: size.width * 0.04, y: size.height * 0.05,
                                                         width: size.width * 0.92, height: size.height * 0.84))
            let src = aspectFit(lb.aspect, in: lb.card.mediaFrame)
            let r = lerpRect(src, target, p)
            let t = lb.card.ticket
            ZStack(alignment: .topLeading) {
                Color.white.opacity(0.88 * clamp01(p))
                Image(nsImage: lb.image).resizable().interpolation(.high)
                    .frame(width: max(1, r.width), height: max(1, r.height))
                    .overlay {
                        if let h = lb.card.highlights {
                            HighlightLayer(result: h, rect: CGRect(origin: .zero, size: r.size), glow: 0.55 * clamp01(p), pad: 5)
                        }
                    }
                    .clipShape(RoundedRectangle(cornerRadius: lerp(0, 14, clamp01(p)), style: .continuous))
                    .shadow(color: .black.opacity(0.14 * clamp01(p)), radius: 24, y: 12)
                    .shadow(color: .black.opacity(0.10 * clamp01(p)), radius: 60, y: 40)
                    .offset(x: r.minX, y: r.minY)
                Text("\(t.agent) · \(t.title) · v\(t.version)".uppercased())
                    .font(.system(size: 11))
                    .tracking(0.7)
                    .foregroundStyle(Color.dim)
                    .lineLimit(1)
                    .frame(width: size.width * 0.8)
                    .position(x: size.width / 2, y: min(size.height - 20, target.maxY + 22))
                    .opacity(smoothstep(0.6, 1, p))
            }
            .contentShape(Rectangle())
            .onTapGesture { model.closeLightbox() }
            .pointerStyle(.zoomOut)
        }
    }
}
