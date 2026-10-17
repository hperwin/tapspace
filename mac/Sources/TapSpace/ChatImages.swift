import AppKit
import Observation
import SwiftUI
import UniformTypeIdentifiers

// Chat image attachments (SPEC "Chat images"): attach by paperclip, paste or
// drop; pending images in a springing tray above the composer; send with
// POST /api/chat {agent, body, images:[{b64, ext}]}; messages that carry
// `images` render them as a rounded grid that opens a lightbox.

let chatImageExts: Set<String> = ["png", "jpg", "gif", "webp", "heic"]
private let chatImageMaxBytes = 20_000_000
let chatImageLimit = 10

/// One image waiting in the tray: the bytes that get sent, and a decoded copy to show.
struct ChatAttachment: Identifiable, Equatable {
    let id = UUID()
    let data: Data
    let ext: String
    let image: NSImage

    /// "local:<id>": how a not-yet-uploaded image is addressed in a bubble.
    var localPath: String { "local:" + id.uuidString }

    static func == (a: ChatAttachment, b: ChatAttachment) -> Bool { a.id == b.id }

    /// Decodes off the main thread; nil for anything that is not a supported image.
    static func make(data: Data, ext rawExt: String) async -> ChatAttachment? {
        var ext = rawExt.lowercased()
        if ext == "jpeg" { ext = "jpg" }
        var bytes = data
        if !chatImageExts.contains(ext) {
            // TIFF from the clipboard, BMP and friends go up as PNG.
            guard let png = await Task.detached(priority: .userInitiated, operation: { () -> Data? in
                NSBitmapImageRep(data: data)?.representation(using: .png, properties: [:])
            }).value else { return nil }
            bytes = png
            ext = "png"
        }
        guard !bytes.isEmpty, bytes.count <= chatImageMaxBytes else { return nil }
        let decoded = bytes
        guard let cg = await Task.detached(priority: .userInitiated, operation: { downsample(decoded, 1400) }).value else { return nil }
        return ChatAttachment(data: bytes, ext: ext, image: NSImage(cgImage: cg, size: NSSize(width: cg.width, height: cg.height)))
    }
}

/// Where attachments come from before they are read.
enum ChatImageSource {
    case file(URL)
    case data(Data, String)

    /// The image files, image data or image paths on a pasteboard; nil when it holds none.
    static func from(_ pb: NSPasteboard) -> [ChatImageSource]? {
        if let urls = pb.readObjects(forClasses: [NSURL.self], options: [.urlReadingFileURLsOnly: true]) as? [URL] {
            let imgs = urls.filter { isImageFile($0) }
            if !imgs.isEmpty { return imgs.map { .file($0) } }
        }
        for (type, ext) in [(NSPasteboard.PasteboardType.png, "png"), (.init("public.jpeg"), "jpg"),
                            (.init("public.heic"), "heic"), (.init("com.compuserve.gif"), "gif"),
                            (.init("org.webmproject.webp"), "webp"), (.tiff, "tiff")] {
            if let d = pb.data(forType: type), !d.isEmpty { return [.data(d, ext)] }
        }
        // A copied path to an image file (Terminal, a chat, a screenshot's path).
        if let text = pb.string(forType: .string) {
            let paths = imagePaths(text)
            if !paths.isEmpty { return paths.map { .file($0) } }
        }
        return nil
    }

    static func isImageFile(_ u: URL) -> Bool {
        guard u.isFileURL else { return false }
        let ext = u.pathExtension.lowercased()
        return chatImageExts.contains(ext == "jpeg" ? "jpg" : ext)
    }

    /// Lines that are nothing but a path to an image file (the web page's IMG_PATH).
    static func imagePaths(_ text: String) -> [URL] {
        let lines = text.split(whereSeparator: \.isNewline).map { $0.trimmingCharacters(in: .whitespaces) }.filter { !$0.isEmpty }
        guard !lines.isEmpty else { return [] }
        var out: [URL] = []
        for l in lines {
            let raw = l.hasPrefix("file://") ? (URL(string: l)?.path ?? "") : (l.removingPercentEncoding ?? l)
            guard raw.hasPrefix("/") || raw.hasPrefix("~") else { return [] }
            let u = URL(fileURLWithPath: (raw as NSString).expandingTildeInPath)
            guard isImageFile(u), FileManager.default.isReadableFile(atPath: u.path) else { return [] }
            out.append(u)
        }
        return out
    }

    func load() async -> ChatAttachment? {
        switch self {
        case .file(let u):
            guard let d = await Task.detached(priority: .userInitiated, operation: { try? Data(contentsOf: u) }).value else { return nil }
            return await ChatAttachment.make(data: d, ext: u.pathExtension)
        case .data(let d, let ext):
            return await ChatAttachment.make(data: d, ext: ext)
        }
    }
}

/// The composer's draft images, shared by the paperclip, paste and drop.
@MainActor @Observable
final class ChatDraft {
    private(set) var attachments: [ChatAttachment] = []
    /// Bumped when an attachment lands, so the field takes focus.
    private(set) var focusBump = 0

    /// Reads and adds the sources (agent threads only; taps take text).
    func add(_ sources: [ChatImageSource], store: Store) {
        guard !sources.isEmpty else { return }
        guard store.thread.hasPrefix("agent:") else {
            store.show("Images go to agents. Taps take text.")
            return
        }
        Task { @MainActor in
            var failed = 0, dropped = 0
            for s in sources {
                guard attachments.count < chatImageLimit else { dropped += 1; continue }
                if let a = await s.load() { attachments.append(a) } else { failed += 1 }
            }
            focusBump += 1
            if dropped > 0 { store.show("Up to \(chatImageLimit) images per message.") }
            else if failed > 0 { store.show(failed == 1 ? "Couldn’t attach that image." : "Couldn’t attach \(failed) of those images.") }
        }
    }

    func remove(_ id: UUID) { attachments.removeAll { $0.id == id } }

    /// Hands the images to a send and empties the tray.
    func take() -> [ChatAttachment] {
        defer { attachments = [] }
        return attachments
    }

    /// A send that failed gives its images back (in front of anything added since).
    func restore(_ imgs: [ChatAttachment]) {
        attachments = Array((imgs + attachments).prefix(chatImageLimit))
    }

    /// NSOpenPanel, several at once.
    func pick(store: Store) {
        let panel = NSOpenPanel()
        panel.allowsMultipleSelection = true
        panel.canChooseDirectories = false
        panel.allowedContentTypes = [.png, .jpeg, .gif, .webP, .heic]
        panel.message = "Attach images"
        panel.prompt = "Attach"
        guard let window = NSApp.keyWindow ?? NSApp.mainWindow else {
            if panel.runModal() == .OK { add(panel.urls.map { .file($0) }, store: store) }
            return
        }
        panel.beginSheetModal(for: window) { [weak self] r in
            guard r == .OK else { return }
            Task { @MainActor in self?.add(panel.urls.map { .file($0) }, store: store) }
        }
    }

    /// Drag and drop: file URLs and raw image data.
    func drop(_ providers: [NSItemProvider], store: Store) -> Bool {
        let usable = providers.filter {
            $0.hasItemConformingToTypeIdentifier(UTType.fileURL.identifier) || $0.hasItemConformingToTypeIdentifier(UTType.image.identifier)
        }
        guard !usable.isEmpty else { return false }
        guard store.thread.hasPrefix("agent:") else {
            store.show("Images go to agents. Taps take text.")
            return true
        }
        Task { @MainActor in
            var sources: [ChatImageSource] = []
            for p in usable {
                if let s = await Self.source(p) { sources.append(s) }
            }
            if sources.isEmpty { store.show("Only images attach here.") } else { add(sources, store: store) }
        }
        return true
    }

    private static func source(_ p: NSItemProvider) async -> ChatImageSource? {
        if p.hasItemConformingToTypeIdentifier(UTType.fileURL.identifier) {
            let url: URL? = await withCheckedContinuation { c in
                _ = p.loadObject(ofClass: URL.self) { u, _ in c.resume(returning: u) }
            }
            if let url, ChatImageSource.isImageFile(url) { return .file(url) }
            if url != nil { return nil }
        }
        let type = p.registeredTypeIdentifiers.compactMap(UTType.init).first { $0.conforms(to: .image) } ?? .png
        let data: Data? = await withCheckedContinuation { c in
            _ = p.loadDataRepresentation(forTypeIdentifier: type.identifier) { d, _ in c.resume(returning: d) }
        }
        guard let data else { return nil }
        return .data(data, type.preferredFilenameExtension ?? "png")
    }
}

// MARK: - Sending with images, and the images on server rows

/// Agent sends that carry images. Text-only sends stay on Store.send; these
/// need the images in the POST and in the optimistic bubble, which the core
/// send does not carry.
@MainActor @Observable
final class ChatOutbox {
    static let shared = ChatOutbox()

    struct Item: Identifiable, Equatable {
        let id: String
        let key: String
        let body: String
        let ts: Double
        let images: [ChatAttachment]
    }

    private(set) var items: [Item] = []
    /// Server message key ("agent:NAME#id") -> the outbox bubble it replaced.
    private(set) var confirmed: [String: String] = [:]

    func send(_ body: String, images: [ChatAttachment], to key: String, store: Store) async -> Bool {
        guard key.hasPrefix("agent:"), !images.isEmpty || !body.isEmpty else { return false }
        let name = String(key.dropFirst(6))
        let item = Item(id: "o:" + UUID().uuidString, key: key, body: body, ts: Date().timeIntervalSince1970, images: images)
        for a in images { ChatMedia.shared.seed(a.localPath, a) }
        items.append(item)
        let before = store.agent(key)?.state
        if let i = store.agents.firstIndex(where: { $0.key == key }) { store.agents[i].state = "thinking" }
        do {
            let payload: [String: Any] = ["agent": name, "body": body,
                                          "images": images.map { ["b64": $0.data.base64EncodedString(), "ext": $0.ext] }]
            let data = try await API.postData("/api/chat", payload)
            if let row = (try? JSONDecoder().decode(MediaPost.self, from: data))?.message {
                confirmed["\(key)#\(row.id)"] = item.id
                // The served copies are the bytes we just sent: show them from memory, no reload.
                for (path, a) in zip(row.images, images) { ChatMedia.shared.seed(path, a) }
            }
            store.chatPoller.kick()
            store.agentsPoller.kick()
            return true
        } catch {
            items.removeAll { $0.id == item.id }
            if let i = store.agents.firstIndex(where: { $0.key == key }), let before { store.agents[i].state = before }
            store.show(error.localizedDescription.isEmpty ? "Didn’t send. Is TapSpace still running?" : "Didn’t send: \(error.localizedDescription)")
            return false
        }
    }

    /// The server row (by key) that confirmed this bubble, if any.
    func serverKey(_ itemID: String) -> String? { confirmed.first { $0.value == itemID }?.key }

    /// Bubbles whose server row is now in the thread leave the outbox (the row
    /// takes over under the same key, so nothing re-enters).
    func prune(_ present: Set<String>) {
        let keep = items.filter { serverKey($0.id).map { !present.contains($0) } ?? true }
        if keep != items { items = keep }
    }
}

private struct MediaPost: Decodable { let message: ChatMessage? }

/// Decoded chat images (sent ones straight from memory) and the lightbox.
/// Which rows carry images comes from `ChatMessage.images`.
@MainActor @Observable
final class ChatMedia {
    static let shared = ChatMedia()

    let lightbox = ChatLightbox()
    @ObservationIgnored private var local: [String: ChatAttachment] = [:]

    func seed(_ path: String, _ a: ChatAttachment) { local[path] = a }

    func cached(_ path: String) -> NSImage? { local[path]?.image ?? Images.shared.cached(path, maxPixel: 1400) }

    func load(_ path: String) async -> NSImage? {
        if let hit = cached(path) { return hit }
        return await Images.shared.load(path, maxPixel: 1400)
    }

    /// Full resolution for the lightbox.
    func full(_ path: String) async -> NSImage? {
        if let a = local[path] {
            let d = a.data
            guard let cg = await Task.detached(priority: .userInitiated, operation: { downsample(d, 3600) }).value else { return nil }
            return NSImage(cgImage: cg, size: NSSize(width: cg.width, height: cg.height))
        }
        return await Images.shared.load(path, maxPixel: 3600)
    }
}

// MARK: - Image grid in a bubble

/// 1 image = natural aspect up to 340 pt tall; 2 or 4 = two columns; 3 or 5+ =
/// three columns of square crops. Max 360 pt wide, like the web page.
struct ChatImageGrid: View {
    let paths: [String]
    let mine: Bool
    var maxWidth: CGFloat = 360
    private let gap: CGFloat = 6

    var body: some View {
        let w = min(360, maxWidth)
        let n = paths.count
        if n == 1 {
            ChatImageCell(path: paths[0], single: CGSize(width: w, height: 340))
        } else {
            let cols = (n == 2 || n == 4) ? 2 : 3
            let side = floor((w - gap * CGFloat(cols - 1)) / CGFloat(cols))
            let rows = stride(from: 0, to: n, by: cols).map { Array(paths[$0..<min(n, $0 + cols)]) }
            VStack(alignment: mine ? .trailing : .leading, spacing: gap) {
                ForEach(Array(rows.enumerated()), id: \.offset) { _, row in
                    HStack(spacing: gap) {
                        ForEach(row, id: \.self) { p in ChatImageCell(path: p, square: side) }
                    }
                }
            }
        }
    }
}

struct ChatImageCell: View {
    let path: String
    var single: CGSize? = nil
    var square: CGFloat = 0
    /// A card's picture (CONTRACT 18): exactly this box from the first layout, before the picture decodes, so a card
    /// arriving live never shifts; the whole picture sits inside it at its own aspect, on the well.
    var box: CGSize? = nil
    @State private var image: NSImage?
    @State private var hover = SpringBox(0, .snappy)
    @State private var frame: CGRect = .zero

    var body: some View {
        let img = image ?? ChatMedia.shared.cached(path)
        let size = cellSize(img)
        let shape = RoundedRectangle(cornerRadius: 14, style: .continuous)
        ZStack {
            shape.fill(Color.well)
            if let img {
                Image(nsImage: img)
                    .resizable()
                    .interpolation(.high)
                    .aspectRatio(contentMode: box == nil ? .fill : .fit)
                    .frame(width: size.width, height: size.height)
            }
        }
        .frame(width: size.width, height: size.height)
        .clipShape(shape)
        .shadow(color: .black.opacity(0.05), radius: 1, y: 1)
        .shadow(color: .black.opacity(0.08), radius: 10, y: 6)
        .scaleEffect(1 + 0.02 * hover.value)
        .contentShape(shape)
        .onGeometryChange(for: CGRect.self, of: { $0.frame(in: .named("chat")) }) { frame = $0 }
        .onHover { h in hover.set(h ? 1 : 0, h ? .pop : .snappy) }
        .pointerStyle(.zoomIn)
        .onTapGesture {
            if let img { ChatMedia.shared.lightbox.open(path, image: img, from: frame) }
        }
        .task(id: path) {
            guard image == nil else { return }
            if let hit = ChatMedia.shared.cached(path) { image = hit; return }
            // Only a picture that arrives late fades in; one already decoded is just there.
            if let loaded = await ChatMedia.shared.load(path) { withAnimation(.smooth(duration: 0.25)) { image = loaded } }
        }
    }

    private func cellSize(_ img: NSImage?) -> CGSize {
        if let box { return CGSize(width: pixel(box.width), height: pixel(box.height)) }
        guard let single else { return CGSize(width: square, height: square) }
        guard let img, img.size.width > 0, img.size.height > 0 else { return CGSize(width: 240, height: 180) }
        let aspect = img.size.width / img.size.height
        var w = single.width, h = w / aspect
        if h > single.height { h = single.height; w = h * aspect }
        return CGSize(width: pixel(w), height: pixel(h))
    }
}

// MARK: - The tray above the composer

/// Pending images as 84 pt rounded thumbs; the tray springs open and closed,
/// each thumb springs in, and its × springs it back out.
struct ChatTray: View {
    let draft: ChatDraft
    let visible: Bool
    @State private var open = SpringBox(0, .chatTray)

    var body: some View {
        let p = open.value
        let full: CGFloat = 84 + 14 + 2
        ScrollView(.horizontal) {
            HStack(spacing: 10) {
                ForEach(draft.attachments) { a in
                    ChatThumb(attachment: a) { draft.remove(a.id) }
                }
            }
            .padding(.horizontal, 14)
            .padding(.top, 14)
            .padding(.bottom, 2)
        }
        .scrollIndicators(.never)
        .scrollClipDisabled()
        .frame(height: max(0, full * CGFloat(p)), alignment: .top)
        .clipped()
        .opacity(clamp01(p * 1.6))
        .onAppear { open.snap(want ? 1 : 0) }
        .onChange(of: want) { _, w in open.set(w ? 1 : 0, w ? .chatTray : .snappy) }
    }

    private var want: Bool { visible && !draft.attachments.isEmpty }
}

struct ChatThumb: View {
    let attachment: ChatAttachment
    let remove: () -> Void
    @State private var s = SpringBox(0, .chatThumb)
    @State private var leaving = false

    var body: some View {
        let p = s.value
        let k = clamp01(1 - p)
        let shape = RoundedRectangle(cornerRadius: 14, style: .continuous)
        Image(nsImage: attachment.image)
            .resizable()
            .interpolation(.high)
            .aspectRatio(contentMode: .fill)
            .frame(width: 84, height: 84)
            .clipShape(shape)
            .background(shape.fill(Color.well))
            .overlay(alignment: .topTrailing) {
                Button {
                    guard !leaving else { return }
                    leaving = true
                    s.set(0, .snappy) { remove() }
                } label: {
                    Image(systemName: "xmark")
                        .font(.system(size: 8.5, weight: .bold))
                        .foregroundStyle(.white)
                        .frame(width: 20, height: 20)
                        .background(Circle().fill(Color.black.opacity(0.6)))
                }
                .buttonStyle(ChatPress(scale: 0.85))
                .padding(5)
                .help("Remove")
            }
            .shadow(color: .black.opacity(0.06), radius: 1, y: 1)
            .shadow(color: .black.opacity(0.08), radius: 9, y: 6)
            .scaleEffect(max(0.001, 0.6 + 0.4 * p))
            .offset(y: leaving ? 0 : 10 * k)
            .blur(radius: k > 0.01 ? 8 * k : 0)
            .opacity(clamp01(p))
            .onAppear { s.set(1) }
    }
}

// MARK: - Paperclip

struct AttachButton: View {
    let shown: Bool
    let action: () -> Void
    @State private var s = SpringBox(1, .pop)
    @State private var hover = false

    var body: some View {
        let v = s.value
        Button(action: action) {
            Image(systemName: "paperclip")
                .font(.system(size: 16, weight: .semibold))
                .foregroundStyle(Color.ink)
                .frame(width: 44, height: 44)
                .background(Circle().fill(Color.black.opacity(hover ? 0.09 : 0.05)))
        }
        .buttonStyle(ChatPress(scale: 0.9))
        .help("Attach images")
        .disabled(!shown)
        .onHover { h in withAnimation(.easeOut(duration: 0.15)) { hover = h } }
        .scaleEffect(max(0.001, 0.6 + 0.4 * v))
        .opacity(clamp01(v))
        // Folds away on tap threads: the field takes the room back with the spring.
        .frame(width: max(0, 44 * CGFloat(v)), alignment: .leading)
        .padding(.trailing, max(0, 10 * CGFloat(v)) - 10)
        .onAppear { s.snap(shown ? 1 : 0) }
        .onChange(of: shown) { _, on in s.set(on ? 1 : 0, on ? .pop : .snappy) }
    }
}

// MARK: - Drop overlay

struct DropOverlay: View {
    let active: Bool
    @State private var s = SpringBox(0, .pop)

    var body: some View {
        let p = s.value
        let shape = RoundedRectangle(cornerRadius: 18, style: .continuous)
        ZStack {
            shape.fill(Color.tapBlue.opacity(0.05))
            shape.strokeBorder(Color.tapBlue.opacity(0.5), style: StrokeStyle(lineWidth: 2, dash: [7, 6]))
            VStack(spacing: 8) {
                Image(systemName: "photo.on.rectangle.angled")
                    .font(.system(size: 22, weight: .medium))
                Text("Drop images to attach")
                    .font(.system(size: 13, weight: .semibold))
                    .tracking(0.5)
            }
            .foregroundStyle(Color.tapBlue)
            .offset(y: 6 * (1 - p))
        }
        .padding(10)
        .scaleEffect(max(0.001, 0.97 + 0.03 * p))
        .opacity(clamp01(p))
        .allowsHitTesting(false)
        .onChange(of: active) { _, on in s.set(on ? 1 : 0, on ? .pop : .snappy) }
    }
}

// MARK: - Lightbox

@MainActor @Observable
final class ChatLightbox {
    private(set) var path: String?
    private(set) var image: NSImage?
    private(set) var from: CGRect = .zero
    private(set) var closing = false
    let progress = SpringBox(0, SpringPreset(stiffness: 300, damping: 28))
    @ObservationIgnored private var monitor: Any?

    var aspect: CGFloat {
        guard let s = image?.size, s.height > 0 else { return 1 }
        return s.width / s.height
    }

    func open(_ p: String, image img: NSImage, from rect: CGRect) {
        guard path == nil else { return }
        path = p
        image = img
        from = rect
        closing = false
        progress.set(1)
        monitor = NSEvent.addLocalMonitorForEvents(matching: .keyDown) { [weak self] e in
            guard e.keyCode == 53, let self, self.path != nil else { return e }
            self.close()
            return nil
        }
        Task { @MainActor in
            if let hi = await ChatMedia.shared.full(p), self.path == p { self.image = hi }
        }
    }

    func close() {
        guard path != nil, !closing else { return }
        closing = true
        if let m = monitor { NSEvent.removeMonitor(m); monitor = nil }
        progress.set(0, SpringPreset(stiffness: 340, damping: 32)) { [weak self] in
            guard let self, self.closing else { return }
            self.path = nil
            self.image = nil
            self.closing = false
        }
    }
}

struct ChatLightboxView: View {
    let lb: ChatLightbox

    var body: some View {
        if let img = lb.image {
            GeometryReader { geo in
                let p = lb.progress.value
                let size = geo.size
                let target = aspectFit(lb.aspect, in: CGRect(x: size.width * 0.04, y: size.height * 0.04,
                                                             width: size.width * 0.92, height: size.height * 0.92))
                let src = lb.from == .zero ? target : aspectFit(lb.aspect, in: lb.from)
                let r = lerpRect(src, target, p)
                ZStack(alignment: .topLeading) {
                    Color.white.opacity(0.88 * clamp01(p))
                    Image(nsImage: img).resizable().interpolation(.high)
                        .frame(width: max(1, r.width), height: max(1, r.height))
                        .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
                        .shadow(color: .black.opacity(0.14 * clamp01(p)), radius: 24, y: 12)
                        .shadow(color: .black.opacity(0.10 * clamp01(p)), radius: 60, y: 40)
                        .offset(x: r.minX, y: r.minY)
                }
                .contentShape(Rectangle())
                .onTapGesture { lb.close() }
                .pointerStyle(.zoomOut)
            }
        }
    }
}
