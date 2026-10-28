import AppKit
import CoreGraphics
import Observation

/// Pool pose and pool frames (harness lane, for the Chat tab's face pool, ChatPool.swift). The pool reads its
/// pose from here: talks and taps to show instead of the server's (a scratch server reads no board and the
/// talks are new), a pinned hover (a real pointer cannot hover offscreen), a pinned moment of a talk's beat
/// (offline frames draw model values, not running animations) and a pinned clock. Nothing here is read outside
/// an offscreen `--snapshot` run.
///
///   TAPSPACE_POOL_TAPS=<file>   taps to list in the pool (a saved GET /api/taps), with --snapshot only
///   TAPSPACE_POOL_HOVER=<key>   a face key whose card stays open, with --snapshot only
///   TAPSPACE_POOL_FRAMES=1      with --snapshot: POOLCHECK, then 2x frames of the reveal card opening and
///                               closing on a master and on a tap (`<dir>/f000.png`..., 30 per second) and
///                               `frames.json`, instead of the scenes
///
/// The talk line's frames (draw-on, chase, ping) are the link frames (TAPSPACE_LINK_FRAMES=1, HarnessLink).
@MainActor @Observable
final class HarnessPool {
    static let shared = HarnessPool()
    static let offscreen = CommandLine.arguments.contains("--snapshot")
    static let asked = offscreen && ProcessInfo.processInfo.environment["TAPSPACE_POOL_FRAMES"] != nil

    /// Talks the pool shows instead of the server's (nil = live data).
    var talks: [AgentTalk]?
    /// Taps the pool lists after the server's fleet (nil = none).
    var taps: [Tap]? = HarnessPool.savedTaps()
    /// A face whose card stays open, as if the pointer rested on it (nil = the real pointer).
    var hover: String? = offscreen ? ProcessInfo.processInfo.environment["TAPSPACE_POOL_HOVER"].flatMap { $0.isEmpty ? nil : $0 } : nil
    /// One talk's beat held at `t` seconds (nil = the running animation).
    var pin: (talk: String, t: Double)?
    /// The pool's clock (nil = the real one).
    var clock: Double?

    /// The taps named by TAPSPACE_POOL_TAPS: a saved /api/taps answer (or a bare list of its items).
    static func savedTaps() -> [Tap]? {
        guard offscreen, let path = ProcessInfo.processInfo.environment["TAPSPACE_POOL_TAPS"], !path.isEmpty,
              let data = FileManager.default.contents(atPath: (path as NSString).expandingTildeInPath) else { return nil }
        if let r = try? JSONDecoder().decode(TapsResponse.self, from: data) { return r.items }
        return (try? JSONDecoder().decode(Lossy<Tap>.self, from: data))?.items
    }

    /// A listed tap as the server's fleet row would carry it.
    static func entry(_ t: Tap) -> FleetEntry {
        FleetEntry(key: t.key, kind: "tap", title: String(t.name.prefix(80)), look: nil, initial: t.initial,
                   state: "", phase: t.phase ?? "", word: t.phaseLabel, done: t.goals.filter(\.done).count,
                   total: t.goals.count, needsYou: t.waits, working: t.replying == true)
    }

    /// A tap by face key in either casing: the harness's, else the store's.
    static func tap(_ key: String, store: Store) -> Tap? {
        let k = key.lowercased()
        return shared.taps?.first { $0.key.lowercased() == k } ?? store.taps.items.first { $0.key.lowercased() == k }
    }

    /// A talk row through the app's own decoder (the shape /api/agents serves in `talks`).
    static func talk(_ id: String, from: String, to: String, ts: Double, text: String = "", kind: String = "message") -> AgentTalk? {
        let row: [String: Any] = ["id": id, "from": from, "to": to, "ts": ts, "text": text, "kind": kind]
        guard let data = try? JSONSerialization.data(withJSONObject: row) else { return nil }
        return try? JSONDecoder().decode(AgentTalk.self, from: data)
    }

    /// The pool's members as the panel lays them out (masters, then taps).
    static func members(_ store: Store) -> [FleetEntry] { ChatPoolBoard.members(ChatPoolPanel.fleet(store)) }

    static func run(app: AppDelegate, dir: URL) {
        let drive = PoolDrive(app: app, dir: dir)
        Task { @MainActor in
            try? await Task.sleep(for: .seconds(120))
            drive.fail("watchdog: gave up after 120 s")
            drive.finish()
        }
        Task { @MainActor in await drive.go() }
    }
}

/// One pool-frames run: checks, pose, the reveal card on a master and on a tap, manifest, exit.
@MainActor
private final class PoolDrive {
    let app: AppDelegate
    let dir: URL
    private var problems: [String] = []
    private var count = 0
    private var finished = false
    private var faces: [String] = []

    init(app: AppDelegate, dir: URL) {
        self.app = app
        self.dir = dir
    }

    func go() async {
        if let why = ChatHarness.refusal() {
            Harness.log("POOLFRAMES refused: \(why). Point TAPSPACE_URL at a scratch server.")
            exit(1)
        }
        API.allowWrites = false
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let failed = ChatPoolBoard.checks()
        Harness.log("POOLCHECK " + (failed.isEmpty ? "all passed" : "\(failed.count) failed"))
        problems += failed
        if let p = app.pill.panel { p.setFrameOrigin(NSPoint(x: -32000, y: p.frame.minY)) }
        app.window.setContentSize(NSSize(width: 1512, height: 887))
        let store = app.store
        let me = HarnessPool.shared
        await Harness.wait(20) { store.agentsLoaded }
        guard let lead = MuddiOrder.lead(store.agents) else {
            fail("no lead on \(API.base.absoluteString)")
            return finish()
        }
        let members = HarnessPool.members(store)
        let master = members.first { $0.kind != "tap" }, tap = members.first { $0.kind == "tap" }
        guard master != nil || tap != nil else {
            fail("nothing in the pool under \(lead.title)")
            return finish()
        }
        // A talk with each, so their cards carry the talk line.
        let now = Date().timeIntervalSince1970
        me.talks = [master.flatMap { HarnessPool.talk("pool-1", from: lead.key, to: $0.key, ts: now - 90,
                                                       text: "Check the build before you push") },
                    tap.flatMap { HarnessPool.talk("pool-2", from: lead.key, to: $0.key, ts: now - 240,
                                                    text: "Send the proof when it lands", kind: "tap") }].compactMap { $0 }
        store.thread = lead.key
        store.tab = .chat
        await settle()
        await Harness.wait(5) { HarnessLink.shared.panel.width > 0 }
        if HarnessLink.shared.panel.width <= 0 { fail("the pool is not on screen (ChatView must host ChatPoolPanel)") }
        Motion.main.link?.invalidate()
        for _ in 0..<6 { await frame() }                                   // at rest
        for e in [master, tap].compactMap({ $0 }) {
            faces.append(e.title)
            me.hover = e.key
            for _ in 0..<27 { await frame() }                              // the face grows, the card opens
            me.hover = nil
            for _ in 0..<12 { await frame() }                              // and closes
        }
        me.talks = nil
        finish()
    }

    /// Step every spring by 1/30 s, let SwiftUI commit, render the window and keep the column.
    private func frame() async {
        Motion.main.run(seconds: 1.0 / 30, step: 1.0 / 240)
        try? await Task.sleep(for: .milliseconds(30))
        guard let view = app.window.contentView, let full = HarnessSnapshot.render(view) else {
            fail(String(format: "f%03d: render failed", count))
            return
        }
        let r = HarnessLink.shared.panel.insetBy(dx: -8, dy: -8).intersection(view.bounds)
        let crop = CGRect(x: r.minX * 2, y: r.minY * 2, width: r.width * 2, height: min(r.height, 760) * 2).integral
        let image = (r.isEmpty ? nil : full.cropping(to: crop)) ?? full
        if count == 0, HarnessSnapshot.ink(image) < 0.003 { fail("f000 looks blank") }
        let url = dir.appendingPathComponent(String(format: "f%03d.png", count))
        guard let png = NSBitmapImageRep(cgImage: image).representation(using: .png, properties: [:]),
              (try? png.write(to: url)) != nil else {
            fail("could not write \(url.path)")
            return
        }
        count += 1
    }

    /// Snap every spring to rest across a few layout passes (HarnessSnapshot's settle).
    private func settle() async {
        for _ in 0..<4 {
            Motion.main.settleAll()
            app.window.contentView?.layoutSubtreeIfNeeded()
            try? await Task.sleep(for: .milliseconds(90))
        }
        try? await Task.sleep(for: .milliseconds(450))
        Motion.main.settleAll()
    }

    func fail(_ s: String) { problems.append(s) }

    func finish() {
        guard !finished else { return }
        finished = true
        let manifest: [String: Any] = ["server": API.base.absoluteString, "frames": count, "fps": 30, "scale": 2,
                                       "faces": faces, "problems": problems]
        if let data = try? JSONSerialization.data(withJSONObject: manifest, options: [.prettyPrinted, .sortedKeys]) {
            try? data.write(to: dir.appendingPathComponent("frames.json"))
        }
        Harness.log("POOLFRAMES done: \(count) frames, \(problems.count) problems -> \(dir.path)")
        for p in problems { Harness.log("POOLFRAMES problem: \(p)") }
        exit(problems.isEmpty ? 0 : 1)
    }
}
