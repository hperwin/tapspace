import AppKit

/// Chat-lane verification drive (like PillHarness for the pill). Only runs
/// when TAPSPACE_CHAT_DRIVE names an output directory and TAPSPACE_URL names a
/// local scratch server: it refuses (logs CHATDRIVE refused, drives nothing)
/// when TAPSPACE_URL is unset or on the live port 4747.
///
///   TAPSPACE_CHAT_DRIVE=<dir>          where the PNG frames land
///   TAPSPACE_CHAT_IMAGES=<a.png:b.png> images to attach (paperclip path)
///   TAPSPACE_CHAT_TEXT=<text>          text to type with them
///   TAPSPACE_CHAT_TAP=owner/repo#n     a tap thread for the "taps take text" leg
///   TAPSPACE_CHAT_RAIL=1               instead: only step the rail selection (for --fps)
///
/// It attaches, types, sends with Return's code path, and renders the real
/// window at 2x every ~50 ms through the flight, then the landed bubble, the
/// lightbox, a tap thread (no paperclip) and the drop toast there. Exits after.
@MainActor
enum ChatHarness {
    /// The composer's text and send, published by the live ComposerBar.
    static var compose: ((String) -> Void)?
    static var send: (() -> Void)?
    static weak var draft: ChatDraft?
    private static var started = false
    /// Rail mode: the gaps between the liquid's own redraws while it glides.
    static let railTiming = ProcessInfo.processInfo.environment["TAPSPACE_CHAT_RAIL"] != nil
    private static var lastRailFrame: CFTimeInterval = 0
    private static var railWorst: CFTimeInterval = 0
    private static var railFrames = 0
    nonisolated(unsafe) private static var stepAt: CFTimeInterval = 0

    private static var lastRailBounds: CGRect = .null
    private static var railMoving = false

    /// Called from the rail liquid's body (rail mode only). Counts the gap
    /// between two redraws only while the body was already moving, so a
    /// redraw of a liquid at rest (in the frame that builds a thread) is not
    /// read as a glide frame.
    static func railFrame(_ bounds: CGRect) {
        let now = CACurrentMediaTime()
        let moved = bounds != lastRailBounds
        if moved, railMoving, now - lastRailFrame < 0.25 {
            railWorst = max(railWorst, now - lastRailFrame)
            railFrames += 1
        }
        railMoving = moved
        lastRailBounds = bounds
        lastRailFrame = now
    }

    /// The drive hooks are published only when a drive was asked for AND
    /// the server is a scratch one.
    static var enabled: Bool {
        ProcessInfo.processInfo.environment["TAPSPACE_CHAT_DRIVE"] != nil && refusal() == nil
    }

    /// Why a drive must not run against this server, or nil when it may.
    /// Both modes act as Hayden (the drive POSTs /api/chat; the rail marks
    /// threads read and refreshes taps), so they only run against a server
    /// named explicitly in TAPSPACE_URL, on this machine, off the live port.
    static func refusal(base: URL = API.base,
                        env: [String: String] = ProcessInfo.processInfo.environment) -> String? {
        guard let raw = env["TAPSPACE_URL"], !raw.isEmpty else {
            return "TAPSPACE_URL is unset, so the base is the live server"
        }
        guard ["127.0.0.1", "localhost"].contains(base.host ?? "") else {
            return "\(base.absoluteString) is not a local scratch server"
        }
        let port = base.port ?? (base.scheme == "https" ? 443 : 80)
        if port == CoreServer.livePort { return "port \(port) is the live server" }
        return nil
    }

    static func startIfAsked(store: Store) {
        let env = ProcessInfo.processInfo.environment
        let asked = env["TAPSPACE_CHAT_RAIL"] != nil || env["TAPSPACE_CHAT_DRIVE"] != nil
        if asked, !started, let why = refusal() {
            started = true
            Harness.log("CHATDRIVE refused: \(why). Point TAPSPACE_URL at a scratch server.")
            return
        }
        if env["TAPSPACE_CHAT_RAIL"] != nil, !started {
            // Rail-only motion for --fps: the selection steps every 1.5 s, nothing else moves.
            started = true
            // Long run-loop turns, stamped relative to the last rail step.
            var turnStart = CACurrentMediaTime()
            let obs = CFRunLoopObserverCreateWithHandler(nil, CFRunLoopActivity.afterWaiting.rawValue | CFRunLoopActivity.beforeWaiting.rawValue, true, 0) { _, act in
                let now = CACurrentMediaTime()
                if act == .afterWaiting { turnStart = now; return }
                let d = now - turnStart
                if d > 0.020 { Harness.log(String(format: "CHATRAIL long turn %.0fms at +%.0fms", d * 1000, (now - stepAt) * 1000)) }
            }
            CFRunLoopAddObserver(CFRunLoopGetMain(), obs, .commonModes)
            Task { @MainActor in
                await Harness.wait(15) { store.agentsLoaded && !store.threadKeys.isEmpty }
                store.tab = .chat
                for n in 0..<24 {
                    try? await Task.sleep(for: .seconds(1.5))
                    let keys = store.threadKeys
                    let i = keys.firstIndex(of: store.thread) ?? -1
                    if n > 0 {
                        Harness.log(String(format: "CHATRAIL step=%d glide frames=%d worst=%.0fms", n - 1, railFrames, railWorst * 1000))
                    }
                    railWorst = 0; railFrames = 0
                    stepAt = CACurrentMediaTime()
                    store.open(keys[(i + 1) % keys.count])
                    Harness.log("CHATRAIL step=\(n) \(store.thread)")
                }
                NSApp.terminate(nil)
            }
            return
        }
        guard let out = env["TAPSPACE_CHAT_DRIVE"], !started else { return }
        started = true
        let dir = URL(fileURLWithPath: out)
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let images = (env["TAPSPACE_CHAT_IMAGES"] ?? "").split(separator: ":").map { URL(fileURLWithPath: String($0)) }
        // TAPSPACE_CHAT_SCALE=1 renders frames faster (more of them through the flight).
        let text = env["TAPSPACE_CHAT_TEXT"] ?? ""
        Task { @MainActor in
            await Harness.wait(15) { store.agentsLoaded && store.thread.hasPrefix("agent:") && draft != nil && send != nil }
            store.tab = .chat
            try? await Task.sleep(for: .seconds(2))
            shot(dir, "c0-thread")
            if !images.isEmpty { draft?.add(images.map { .file($0) }, store: store) }
            await Harness.wait(5) { (draft?.attachments.count ?? 0) >= images.count }
            try? await Task.sleep(for: .seconds(1))
            shot(dir, "c1-tray")
            compose?(text)
            try? await Task.sleep(for: .milliseconds(400))
            shot(dir, "c2-typed")
            let t0 = CACurrentMediaTime()
            send?()
            var i = 0
            while CACurrentMediaTime() - t0 < 1.1 {
                shot(dir, String(format: "c3-fly-%02d", i))
                i += 1
                try? await Task.sleep(for: .milliseconds(40))
            }
            try? await Task.sleep(for: .seconds(3))
            shot(dir, "c4-landed")
            let p = store.agent(store.thread).flatMap { store.chats[$0.name]?.last { !$0.images.isEmpty }?.images.first }
            if let p, let img = await ChatMedia.shared.load(p) {
                ChatMedia.shared.lightbox.open(p, image: img, from: .zero)
                try? await Task.sleep(for: .seconds(1.2))
                shot(dir, "c5-lightbox")
                ChatMedia.shared.lightbox.close()
                try? await Task.sleep(for: .seconds(0.8))
            }
            if let tap = env["TAPSPACE_CHAT_TAP"] ?? store.taps.items.first?.key {
                store.open(tap)
                try? await Task.sleep(for: .seconds(1.5))
                shot(dir, "c6-tap")
                if !images.isEmpty { draft?.add(images.prefix(1).map { .file($0) }, store: store) }
                try? await Task.sleep(for: .seconds(0.6))
                shot(dir, "c7-tap-toast")
            }
            Harness.log("CHATDRIVE done \(dir.path)")
            NSApp.terminate(nil)
        }
    }

    private static func shot(_ dir: URL, _ name: String) {
        guard let v = NSApp.windows.first(where: { $0 is MainWindow })?.contentView,
              let img = HarnessSnapshot.render(v, scale: Double(ProcessInfo.processInfo.environment["TAPSPACE_CHAT_SCALE"] ?? "") ?? 2),
              let png = NSBitmapImageRep(cgImage: img).representation(using: .png, properties: [:]) else { return }
        try? png.write(to: dir.appendingPathComponent(name + ".png"))
    }
}
