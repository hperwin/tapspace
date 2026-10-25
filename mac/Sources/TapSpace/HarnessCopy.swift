import AppKit
import ObjectiveC
import Vision

/// `TAPSPACE_COPY_TEST=1` with `--snapshot <dir> --tab chat --thread agent:NAME`: drags a selection through the
/// open thread the way a mouse does (one mouse-down on the view under it, drags, one mouse-up), waits past
/// two chat polls, sends Copy up the window's responder chain (as Edit > Copy does), and writes what was copied to `<dir>/copy.json`,
/// with a still per drag. The text is found where it shows (text recognition on the rendered window), and the
/// app's general pasteboard is swapped for a private one in this process, so Copy never reaches the clipboard
/// (the real one's change count is checked unchanged) and the same drive runs on any build.
///   TAPSPACE_COPY_DRAGS="A=from>to;B=from>to"   each drag runs from the start of `from` to the end of `to`
/// (texts as shown in the thread). Offscreen and against a scratch server only, like every --snapshot run.
@MainActor
enum HarnessCopy {
    static let asked = ProcessInfo.processInfo.environment["TAPSPACE_COPY_TEST"] != nil
    nonisolated(unsafe) static let board = NSPasteboard(name: .init("com.tapspace.copy-test"))

    static func run(app: AppDelegate, dir: URL) {
        Task { @MainActor in
            try? await Task.sleep(for: .seconds(90))
            Harness.log("COPYTEST watchdog: gave up after 90 s")
            exit(1)
        }
        Task { @MainActor in await go(app: app, dir: dir) }
    }

    private static func go(app: AppDelegate, dir: URL) async {
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let clip = NSPasteboard(name: .general).changeCount   // the real clipboard, by name
        redirectGeneral()
        let drags = (ProcessInfo.processInfo.environment["TAPSPACE_COPY_DRAGS"] ?? "")
            .split(separator: ";").compactMap { d -> (String, String, String)? in
                let kv = d.split(separator: "=", maxSplits: 1), ft = kv.last?.components(separatedBy: ">") ?? []
                return kv.count == 2 && ft.count == 2 ? (String(kv[0]), ft[0], ft[1]) : nil
            }
        guard let win = app.window, !drags.isEmpty else { Harness.log("COPYTEST nothing to drag"); exit(1) }
        let ready = await Harness.wait(30) { drags.allSatisfy { point($0.1, end: false, in: win) != nil } }
        if !ready { Harness.log("COPYTEST the thread's text never showed") }
        try? await Task.sleep(for: .seconds(2))           // entrances settle
        var out: [String: Any] = [:]
        for (name, from, to) in drags {
            guard let a = point(from, end: false, in: win), let b = point(to, end: true, in: win) else {
                out[name] = ["error": "text not on screen"]; continue
            }
            board.clearContents()
            drag(from: a, to: b, in: win)
            try? await Task.sleep(for: .seconds(4))       // past two chat polls: a repaint that dropped it shows
            let responder = win.firstResponder
            _ = responder?.tryToPerform(#selector(NSText.copy(_:)), with: nil)   // Edit > Copy's walk up the chain
            out[name] = ["from": from, "to": to, "responder": responder.map { String(describing: type(of: $0)) } ?? "none",
                         "copied": board.string(forType: .string) ?? ""]
            Harness.log("COPYTEST \(name):\n" + (board.string(forType: .string) ?? "<nothing>"))
            shot(win, to: dir.appendingPathComponent("copy-\(name).png"))
        }
        out["clipboardUntouched"] = NSPasteboard(name: .general).changeCount == clip
        Harness.log("COPYTEST clipboard untouched: \(NSPasteboard(name: .general).changeCount == clip)")
        if let data = try? JSONSerialization.data(withJSONObject: out, options: [.prettyPrinted, .sortedKeys]) {
            try? data.write(to: dir.appendingPathComponent("copy.json"))
        }
        exit(0)
    }

    /// The window point at the start (or end) of the shown line that contains `text`: inside the line,
    /// 1 pt from its edge, found by text recognition on the window as rendered.
    private static func point(_ text: String, end: Bool, in win: NSWindow) -> NSPoint? {
        guard let v = win.contentView, let rep = v.bitmapImageRepForCachingDisplay(in: v.bounds) else { return nil }
        v.cacheDisplay(in: v.bounds, to: rep)
        guard let cg = rep.cgImage else { return nil }
        let req = VNRecognizeTextRequest()
        req.recognitionLevel = .accurate
        req.usesLanguageCorrection = false
        try? VNImageRequestHandler(cgImage: cg).perform([req])
        guard let line = (req.results ?? []).first(where: { $0.topCandidates(1).first?.string.contains(text) == true }) else {
            return nil
        }
        let b = line.boundingBox                    // normalized, origin bottom-left, like the window's points
        let r = NSRect(x: b.minX * v.bounds.width, y: b.minY * v.bounds.height,
                       width: b.width * v.bounds.width, height: b.height * v.bounds.height)
        return v.convert(NSPoint(x: end ? r.maxX - 1 : r.minX + 1, y: v.isFlipped ? v.bounds.height - r.midY : r.midY), to: nil)
    }

    /// `NSPasteboard.general` answers a private board for the rest of this run.
    private static func redirectGeneral() {
        guard let general = class_getClassMethod(NSPasteboard.self, #selector(getter: NSPasteboard.general)),
              let test = class_getClassMethod(NSPasteboard.self, #selector(getter: NSPasteboard.copyTestBoard)) else { return }
        method_exchangeImplementations(general, test)
    }

    /// One mouse-down, 24 drags and a mouse-up, as a mouse makes them.
    private static func drag(from a: NSPoint, to b: NSPoint, in win: NSWindow) {
        func event(_ type: NSEvent.EventType, _ p: NSPoint) -> NSEvent? {
            NSEvent.mouseEvent(with: type, location: p, modifierFlags: [], timestamp: ProcessInfo.processInfo.systemUptime,
                               windowNumber: win.windowNumber, context: nil, eventNumber: 0, clickCount: 1,
                               pressure: type == .leftMouseUp ? 0 : 1)
        }
        let steps = 24
        for i in 1...steps {
            let t = CGFloat(i) / CGFloat(steps)
            if let e = event(.leftMouseDragged, NSPoint(x: a.x + (b.x - a.x) * t, y: a.y + (b.y - a.y) * t)) {
                NSApp.postEvent(e, atStart: false)
            }
        }
        if let up = event(.leftMouseUp, b) { NSApp.postEvent(up, atStart: false) }
        // The press goes to the view under it, as the window sends it once key (this window never is: it stays
        // behind Hayden's apps). That view's own tracking then takes the drags and the release queued above.
        guard let down = event(.leftMouseDown, a), let hit = win.contentView?.superview?.hitTest(a) else { return }
        Harness.log("COPYTEST press lands on \(type(of: hit)), scrolled by \(hit.enclosingScrollView.map { String(describing: type(of: $0)) } ?? "nothing")")
        if hit.acceptsFirstResponder { win.makeFirstResponder(hit) }
        hit.mouseDown(with: down)
    }

    private static func shot(_ win: NSWindow, to url: URL) {
        guard let v = win.contentView, let rep = v.bitmapImageRepForCachingDisplay(in: v.bounds) else { return }
        v.cacheDisplay(in: v.bounds, to: rep)
        try? rep.representation(using: .png, properties: [:])?.write(to: url)
    }
}

extension NSPasteboard {
    /// HarnessCopy's stand-in for the general pasteboard.
    @objc class var copyTestBoard: NSPasteboard { HarnessCopy.board }
}
