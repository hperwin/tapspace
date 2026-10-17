import AppKit
import CoreGraphics

/// `--snapshot <dir>`: poses the REAL window and pill into every scene in
/// SPEC.md, settles all springs, renders each with `cacheDisplay` into an
/// exact 2x bitmap, writes `<dir>/NN-scene.png` plus `scenes.json`, and exits
/// (0 = every scene rendered and has ink, 1 = something was missing).
/// The run is offscreen and never activates (App.swift parks the window and
/// the pill). `scenes.json` also carries `masters`: what the app decoded for
/// the masters column, the rail notes and the links, which Scripts/verify.sh
/// holds against what the server served. Scenes 11 and 12 are the lead's
/// thread with its update cards at rest and the rail with everything under
/// the lead shown; the rail starts folded, as Hayden first sees it.
@MainActor
final class HarnessSnapshot {
    let app: AppDelegate
    let dir: URL
    private var scenes: [[String: Any]] = []
    private var problems: [String] = []
    private var masters: [String: Int] = [:]
    private var finished = false

    private var store: Store { app.store }
    private var visual: VisualModel { app.visual }

    init(app: AppDelegate, dir: URL) {
        self.app = app
        self.dir = dir
    }

    func start() {
        Task { @MainActor in
            try? await Task.sleep(for: .seconds(150))
            self.problems.append("watchdog: gave up after 150 s")
            self.finish()
        }
        Task { @MainActor in await self.run() }
    }

    // MARK: Scenes

    private func run() async {
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        Harness.log("SNAPSHOT server \(API.base.absoluteString) -> \(dir.path)")
        // The card motion rules (the package has no test target).
        let motion = CardMotion.checks()
        Harness.log("CARDMOTION " + (motion.isEmpty ? "ok" : "bad " + motion.joined(separator: ", ")))
        if !motion.isEmpty { problems.append("CARDMOTION bad " + motion.joined(separator: ", ")) }
        // The pill is already parked off the real menu bar (App.swift); its view still renders on demand.

        // Data and media first: cards, decoded images, the v2 change boxes.
        let cardsReady = await Harness.wait(30) {
            store.ticketsLoaded && !visual.cards.isEmpty && visual.mediaReady
        }
        if !cardsReady { problems.append("visual: cards or media not ready after 30 s") }
        await Harness.wait(6) {
            visual.cards.allSatisfy { $0.ticket.version < 2 || $0.ticket.isVideo || $0.highlights != nil }
        }

        // 1. Visual tab.
        store.tab = .visual
        await settle()
        shot("01-visual", note: "\(visual.cards.count) cards, \(store.visualBadge) waiting")

        // 2. A card in notes mode: the bottom spreads like the pill into its card.
        if let c = visual.cards.first(where: { !$0.ticket.isVideo && $0.ticket.status == "waiting" }) {
            visual.toggleNotes(c)
            c.notesText = "Tighten the spacing under the header, and keep the player names on one line."
            await settle()
            shot("02-visual-notes", note: c.ticket.title)
            c.notesText = ""
            visual.toggleNotes(c)
            await settle()
        } else {
            problems.append("notes: no waiting image card")
        }

        // 3. The lightbox (full-resolution image swapped in once it decodes).
        if let c = visual.cards.first(where: { !$0.ticket.isVideo && $0.image != nil }) {
            visual.openLightbox(c)
            await Harness.wait(4) { (visual.lightbox?.image.size.width ?? 0) > 2000 }
            await settle()
            shot("03-lightbox", note: c.ticket.title)
            visual.closeLightbox()
            await settle()
            await Harness.wait(2) { visual.lightbox == nil }
        } else {
            problems.append("lightbox: no image card")
        }

        // 4, 10, 5. Chat on agent threads. 04 opens a master with no card notes, so
        // another master's note rows stay in the rail; 10 is the same pose in a window
        // the size of Hayden's (1512 x 887); 05 opens a thinking master the way a rail
        // click does, which reads its notes.
        await Harness.wait(10) { store.agentsLoaded && !store.agents.isEmpty }
        UserDefaults.standard.set(false, forKey: RailView.openKey)
        masters = mastersData()
        let agents = store.agents
        let talking = agents.first { $0.state != "thinking" && $0.count > 0 && $0.notes.isEmpty }
            ?? agents.first { $0.state != "thinking" && $0.count > 0 } ?? agents.first
        let thinking = agents.first { $0.state == "thinking" && $0.key != talking?.key && !$0.notes.isEmpty }
            ?? agents.first { $0.state == "thinking" && $0.key != talking?.key }
        // The thread before the tab: switching to Chat marks the open thread read, so
        // a thread restored from an earlier run is replaced before that happens.
        if let talking { store.thread = talking.key }
        // Key, as when Hayden looks at it (an offscreen launch is never made key).
        store.windowKey = true
        store.tab = .chat
        if let talking {
            await openThread(talking)
            let others = store.agents.filter { $0.key != talking.key }
            let sent = others.reduce(0) { $0 + $1.notes.count }
            let listed = others.reduce(0) { $0 + listedNotes($1) }
            if sent > 0, listed == 0 { problems.append("04-chat-agent: the server sent \(sent) notes, the rail lists none") }
            shot("04-chat-agent", note: chatNote(talking))
            app.window.setContentSize(NSSize(width: 1512, height: 887))
            await settle()
            shot("10-chat-wide", note: chatNote(talking))
            app.window.setContentSize(NSSize(width: 1240, height: 820))
            await settle()
        } else {
            problems.append("04-chat-agent: no such agent on the server")
        }
        if let thinking {
            let before = listedNotes(thinking)
            await openThread(thinking)
            shot("05-chat-agent-thinking", note: chatNote(thinking))
            let after = listedNotes(thinking)
            if before > 0 {
                masters["notes_after_open"] = after
                if after > 0 { problems.append("05-chat-agent-thinking: \(after) notes still listed after opening the thread") }
            }
        } else {
            // A gap in the data (no master thinking right now), not a fault in the app.
            Harness.log("SNAPSHOT skipped 05-chat-agent-thinking: no thinking master on the server")
        }

        // 11, 12. The lead's thread with its update cards (history: every card at rest, its answers folded in), in
        // a window tall enough for a whole card; then the rail with everything under the lead shown.
        if let lead = store.agents.first(where: \.isLead) {
            app.window.setContentSize(NSSize(width: 1512, height: 1200))
            await openThread(lead)
            let rows = store.chats[lead.name] ?? []
            let cards = rows.filter { $0.card != nil }.count
            masters["cards"] = cards
            if cards == 0 { problems.append("11-chat-card: the lead's thread has no update card") }
            shot("11-chat-card", note: "\(lead.title): \(cards) cards, \(rows.filter { $0.answer != nil }.count) answers")
            UserDefaults.standard.set(true, forKey: RailView.openKey)
            await settle()
            shot("12-chat-rail-open", note: "\(store.fleet.count) running under \(lead.title)")
            UserDefaults.standard.set(false, forKey: RailView.openKey)
            app.window.setContentSize(NSSize(width: 1240, height: 820))
            await settle()
        } else {
            problems.append("11-chat-card: no lead on the server")
        }

        // 6. Chat on a tap thread (real GitHub data through the server, read-only).
        await Harness.wait(20) { store.taps.fetched > 0 }
        if let tap = store.taps.items.max(by: { $0.messages.count < $1.messages.count }) {
            store.thread = tap.key
            await settle()
            shot("06-chat-tap", note: "\(tap.title) (\(tap.status))")
        } else {
            problems.append("06-chat-tap: no taps (\(store.taps.error.isEmpty ? "none under this user" : store.taps.error))")
        }

        // 7-9. The menu-bar pill: collapsed, the droplet bulging toward a
        // cursor, and spread into its card.
        store.tab = .visual
        for (name, pose) in [("07-pill-collapsed", PillController.Pose.collapsed),
                             ("08-pill-bulging", .bulging),
                             ("09-pill-expanded", .expanded)] {
            app.pill.pose(pose)
            await settle()
            app.pill.pose(pose)
            await settle(rounds: 2)
            shotPill(name, note: app.pill.liquid.debugState)
        }
        app.pill.releasePose()
        finish()
    }

    /// A rail click: the thread opens and reads (Store.open), then its messages land.
    private func openThread(_ agent: Agent) async {
        store.open(agent.key)
        await Harness.wait(6) { !(store.chats[agent.name] ?? []).isEmpty }
        await settle()
    }

    /// Titles only, like every surface Hayden sees, plus what the rail lists under it.
    private func chatNote(_ agent: Agent) -> String {
        "\(agent.title) (\(agent.state)), messages \(store.chats[agent.name]?.count ?? 0), notes \(listedNotes(agent)), links \(store.links.filter(\.active).count) active"
    }

    /// Snap every spring to rest, across a few layout passes (layout can arm
    /// new springs, like the rail's liquid moving to a row it just measured),
    /// then give SwiftUI's own short transitions time to land.
    private func settle(rounds: Int = 4) async {
        for _ in 0..<rounds {
            Motion.main.settleAll()
            Motion.pill.settleAll()
            app.window.contentView?.layoutSubtreeIfNeeded()
            try? await Task.sleep(for: .milliseconds(90))
        }
        try? await Task.sleep(for: .milliseconds(450))
        // A last snap, then one more pass so SwiftUI draws the snapped values
        // (rows that mounted late, like a bubble from the last poll).
        for _ in 0..<2 {
            Motion.main.settleAll()
            Motion.pill.settleAll()
            try? await Task.sleep(for: .milliseconds(80))
        }
    }

    // MARK: Rendering

    private func shot(_ name: String, note: String) {
        guard let view = app.window.contentView, let image = Self.render(view) else {
            problems.append("\(name): render failed")
            return
        }
        save(image, name, note: note)
    }

    /// The pill panel is transparent: composite it over a menu bar (with the
    /// notch just to its left, where it really sits) so it reads in context.
    private func shotPill(_ name: String, note: String) {
        guard let view = app.pill.panel?.contentView, let pill = Self.render(view) else {
            problems.append("\(name): render failed")
            return
        }
        let size = view.bounds.size
        let liquid = app.pill.liquid
        let barH = liquid.top * 2 + PillLiquid.pillH
        let notchRight = liquid.anchorLeft - 10
        let pw = pill.width, ph = pill.height, k = CGFloat(pw) / size.width
        guard let ctx = CGContext(data: nil, width: pw, height: ph, bitsPerComponent: 8, bytesPerRow: 0,
                                  space: CGColorSpace(name: CGColorSpace.sRGB)!,
                                  bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else { return }
        ctx.scaleBy(x: k, y: k)
        let H = size.height
        // A soft desktop, then the menu bar strip and the notch.
        let desk = CGGradient(colorsSpace: nil, colors: [CGColor(srgbRed: 0.93, green: 0.95, blue: 0.98, alpha: 1),
                                                         CGColor(srgbRed: 0.83, green: 0.88, blue: 0.95, alpha: 1)] as CFArray,
                              locations: [0, 1])!
        ctx.drawLinearGradient(desk, start: CGPoint(x: 0, y: H), end: CGPoint(x: 0, y: 0), options: [])
        ctx.setFillColor(CGColor(srgbRed: 0.97, green: 0.975, blue: 0.985, alpha: 0.92))
        ctx.fill(CGRect(x: 0, y: H - barH, width: size.width, height: barH))
        ctx.setFillColor(CGColor(gray: 0, alpha: 0.08))
        ctx.fill(CGRect(x: 0, y: H - barH - 0.5, width: size.width, height: 0.5))
        let notch = CGPath(roundedRect: CGRect(x: -20, y: H - barH, width: notchRight + 20, height: barH + 12),
                           cornerWidth: 9, cornerHeight: 9, transform: nil)
        ctx.addPath(notch)
        ctx.setFillColor(CGColor(gray: 0, alpha: 1))
        ctx.fillPath()
        ctx.draw(pill, in: CGRect(origin: .zero, size: size))
        guard var image = ctx.makeImage() else { return }
        // Crop to the liquid plus room for its shadow (and the droplet's reach).
        let keep = min(size.height, max(130, liquid.shapeRect.maxY + 56))
        if let c = image.cropping(to: CGRect(x: 0, y: 0, width: pw, height: Int(keep * k))) { image = c }
        // Blank check on the panel's own pixels, not the backdrop.
        save(image, name, note: note, ink: Self.ink(pill))
    }

    /// The view's own drawing (the real hierarchy, not a screen grab) into an
    /// exact 2x bitmap, whatever screen the window happens to be on.
    static func render(_ view: NSView, scale: CGFloat = 2) -> CGImage? {
        view.layoutSubtreeIfNeeded()
        view.displayIfNeeded()
        let b = view.bounds
        guard b.width > 0, b.height > 0,
              let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: Int(b.width * scale), pixelsHigh: Int(b.height * scale),
                                         bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
                                         colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)
        else { return nil }
        rep.size = b.size
        view.cacheDisplay(in: b, to: rep)
        return rep.cgImage
    }

    private func save(_ image: CGImage, _ name: String, note: String, ink measured: Double? = nil) {
        let url = dir.appendingPathComponent(name + ".png")
        let rep = NSBitmapImageRep(cgImage: image)
        guard let png = rep.representation(using: .png, properties: [:]), (try? png.write(to: url)) != nil else {
            problems.append("\(name): could not write \(url.path)")
            return
        }
        let ink = measured ?? Self.ink(image)
        if ink < 0.003 { problems.append("\(name): looks blank (ink \(ink))") }
        scenes.append(["name": name, "file": url.lastPathComponent, "width": image.width, "height": image.height,
                       "ink": (ink * 1000).rounded() / 1000, "note": note])
        Harness.log("SNAPSHOT \(name) \(image.width)x\(image.height) ink=\(String(format: "%.3f", ink)) \(note)")
    }

    /// Fraction of pixels that differ from the corner color (a blank-render check).
    static func ink(_ image: CGImage) -> Double {
        let n = 96
        var px = [UInt8](repeating: 0, count: n * n * 4)
        guard let ctx = CGContext(data: &px, width: n, height: n, bitsPerComponent: 8, bytesPerRow: n * 4,
                                  space: CGColorSpace(name: CGColorSpace.sRGB)!,
                                  bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else { return 0 }
        ctx.interpolationQuality = .medium
        ctx.draw(image, in: CGRect(x: 0, y: 0, width: n, height: n))
        let bg = (Int(px[0]), Int(px[1]), Int(px[2]), Int(px[3]))
        var hits = 0
        for i in stride(from: 0, to: px.count, by: 4) {
            let d = abs(Int(px[i]) - bg.0) + abs(Int(px[i + 1]) - bg.1) + abs(Int(px[i + 2]) - bg.2) + abs(Int(px[i + 3]) - bg.3)
            if d > 24 { hits += 1 }
        }
        return Double(hits) / Double(n * n)
    }

    private func finish() {
        guard !finished else { return }
        finished = true
        let manifest: [String: Any] = ["server": API.base.absoluteString, "scale": 2, "scenes": scenes, "problems": problems,
                                       "masters": masters]
        if let data = try? JSONSerialization.data(withJSONObject: manifest, options: [.prettyPrinted, .sortedKeys]) {
            try? data.write(to: dir.appendingPathComponent("scenes.json"))
        }
        Harness.log("SNAPSHOT done: \(scenes.count) scenes, \(problems.count) problems")
        for p in problems { Harness.log("SNAPSHOT problem: \(p)") }
        exit(problems.isEmpty ? 0 : 1)
    }
}

// MARK: Masters data (the column, the rail notes, the links), read through the core lane's models

private extension HarnessSnapshot {
    /// What the app decoded, before any thread opens, for verify.sh to hold
    /// against what the server served.
    func mastersData() -> [String: Int] {
        let agents = store.agents
        return ["agents": agents.count,
                "with_progress": agents.filter { $0.progress != nil }.count,
                "notes": agents.reduce(0) { $0 + listedNotes($1) },
                "links": store.links.count,
                "links_active": store.links.filter(\.active).count,
                "owned_cards": store.tickets.filter { $0.owner != nil }.count,
                "fleet": store.fleet.count,
                "looks": agents.filter { $0.look != nil }.count]
    }

    /// The note rows the rail lists under a master.
    func listedNotes(_ agent: Agent) -> Int { store.notes(for: agent.key).count }
}
