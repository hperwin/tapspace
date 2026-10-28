import AppKit
import CoreGraphics
import Observation

/// Link frames (harness lane, for the third column's talk lines): motion
/// evidence without an on-screen recording. Run with `--snapshot <dir>` and
/// `TAPSPACE_LINK_FRAMES=1`; Harness.run then dispatches here instead of the
/// scene run, so the window is offscreen and nothing activates. It first
/// checks the progress board's and the pool's rules on synthetic rows (the
/// package has no test target: LINKCHECK, POOLCHECK), then poses the REAL
/// face pool (ChatPoolPanel) against a scratch server and renders a new talk
/// between the lead and a pool face (the line drawing on from the sender,
/// the chase, the receiver's ping), the answer coming back the other way, and
/// the line leaving, as 2x PNG frames (`<dir>/f000.png`...), 30 per second of
/// motion, plus `frames.json`. Springs step offline on a fixed 1/30 s clock;
/// the beat is scrubbed through model values (`HarnessPool.pin`: cacheDisplay
/// draws models, not running animations). Refuses unless TAPSPACE_URL names a
/// local scratch server; never writes (allowWrites is off).
///
///   TAPSPACE_URL=http://127.0.0.1:4748 TAPSPACE_LINK_FRAMES=1 .build/debug/TapSpace --snapshot <dir>
///   ffmpeg -framerate 30 -i <dir>/f%03d.png -vf "scale=trunc(iw/2)*2:trunc(ih/2)*2" -c:v libx264 -pix_fmt yuv420p <dir>/link.mp4
@MainActor @Observable
final class HarnessLink {
    static let shared = HarnessLink()
    static let asked = ProcessInfo.processInfo.environment["TAPSPACE_LINK_FRAMES"] != nil

    /// Links the panel shows instead of the server's while the drive poses it (nil = live data).
    var links: [AgentLink]?
    /// The chase's phase (0..<1), pinned for offline frames (nil = the running animation).
    var phase: Double?
    /// The third column's frame in the window (points, top-left origin), reported only while asked (link or
    /// pool frames).
    @ObservationIgnored var panel: CGRect = .zero

    static func run(app: AppDelegate, dir: URL) {
        let drive = LinkDrive(app: app, dir: dir)
        Task { @MainActor in
            try? await Task.sleep(for: .seconds(120))
            drive.fail("watchdog: gave up after 120 s")
            drive.finish()
        }
        Task { @MainActor in await drive.go() }
    }

    /// A link row through the app's own decoder (the shape /api/agents serves),
    /// so no memberwise init is assumed.
    static func link(_ x: String, _ y: String, from: String, last: Double, active: Bool = true,
                     summary: String = "") -> AgentLink? {
        let row: [String: Any] = ["a": min(x, y), "b": max(x, y), "from": from, "last": last, "count": 2,
                                  "summary": summary, "active": active]
        guard let data = try? JSONSerialization.data(withJSONObject: row) else { return nil }
        return try? JSONDecoder().decode(AgentLink.self, from: data)
    }

    /// An agent row through the app's own decoder.
    static func agent(_ name: String, title: String, first: Double, now: Double, state: String = "here",
                      seen: Double? = nil, progress: [String: Any]? = nil) -> Agent? {
        var row: [String: Any] = ["name": name, "title": title, "state": state, "seen": seen ?? now,
                                  "first": first, "last": seen ?? now, "count": 1, "last_master": 0]
        if let progress { row["progress"] = progress }
        guard let data = try? JSONSerialization.data(withJSONObject: row) else { return nil }
        return try? JSONDecoder().decode(Agent.self, from: data)
    }

    /// The board's rules on synthetic rows: one line per failed expectation,
    /// empty when every one holds.
    static func checks() -> [String] {
        var failed: [String] = []
        func expect(_ ok: Bool, _ what: String) { if !ok { failed.append("check: " + what) } }
        func names(_ rows: [ChatProgressBoard.Row]) -> String { rows.map(\.agent.name).joined() }
        let now = 1_800_000_000.0
        guard let a = agent("a", title: "Alpha", first: 30, now: now),
              let b = agent("b", title: "Bravo", first: 10, now: now),
              let c = agent("c", title: "Charlie", first: 20, now: now) else {
            return ["check: agent rows do not decode"]
        }
        let all = [a, b, c]
        expect(names(ChatProgressBoard.rows(agents: all, links: [], now: now)) == "bca", "stable order by join time")
        if let d = agent("d", title: "Delta", first: 5, now: now), let e = agent("e", title: "Echo", first: 5, now: now) {
            expect(names(ChatProgressBoard.rows(agents: [e, d], links: [], now: now)) == "de", "ties by name")
        }
        if let ab = link("a", "b", from: "a", last: now - 30, summary: "Is the feed\nlive?") {
            let rows = ChatProgressBoard.rows(agents: all, links: [ab], now: now)
            expect(names(rows) == "bac", "a talking pair sits together")
            expect(rows.first?.linkBelow == ab && rows.dropFirst().allSatisfy { $0.linkBelow == nil },
                   "the link hangs under the upper card only")
            expect(ChatProgressBoard.pings(rows) == ["b": now - 30], "the receiver is pinged")
            let line = String(ChatLinkBoard.label(ab, titles: ["a": "Alpha", "b": "Bravo"]).characters)
            expect(line == "Alpha → Bravo · Is the feed live?", "the pair's line: titles, arrow, one line of words")
        } else {
            expect(false, "link rows decode")
        }
        if let idle = link("a", "b", from: "a", last: now - 30, active: false),
           let stale = link("a", "b", from: "a", last: now - 700) {
            expect(names(ChatProgressBoard.rows(agents: all, links: [idle, stale], now: now)) == "bca",
                   "inactive or stale links draw nothing")
        }
        if let ab = link("a", "b", from: "b", last: now - 100), let bc = link("b", "c", from: "c", last: now - 20) {
            let rows = ChatProgressBoard.rows(agents: all, links: [ab, bc], now: now)
            expect(names(rows) == "bca" && rows.first?.linkBelow == bc, "each card joins one pair, the newest")
        }
        if let az = link("a", "z", from: "a", last: now - 10), let aa = link("a", "a", from: "a", last: now - 10) {
            expect(ChatProgressBoard.rows(agents: all, links: [az, aa], now: now).allSatisfy { $0.linkBelow == nil },
                   "unknown ends and self links draw nothing")
        }
        let plan: [String: Any] = ["goal": "Ship the panel", "steps": ["Audit", "Spec", "Build", "Review", "Open the PR"],
                                   "current": 2, "updated": now - 60]
        if let quiet = agent("q", title: "Quiet", first: 1, now: now, state: "away", seen: now - 900),
           let planned = agent("p", title: "Planned", first: 2, now: now, state: "away", seen: now - 900, progress: plan) {
            expect(names(ChatProgressBoard.rows(agents: [quiet, planned], links: [], now: now)) == "p",
                   "a quiet master keeps a card only with a goal")
        }
        func line(_ current: Int, _ steps: [String]) -> String? {
            var p = plan
            p["current"] = current
            p["steps"] = steps
            return agent("s", title: "Steps", first: 1, now: now, progress: p)
                .flatMap { ChatProgressBoard.progress($0) }
                .flatMap { ChatProgressBoard.stepLine($0) }
                .map { $0.count + "|" + $0.step }
        }
        expect(line(2, ["Audit", "Spec", "Build", "Review", "Open the PR"]) == "3 of 5|Build", "step line")
        expect(line(5, ["Audit", "Spec", "Build", "Review", "Open the PR"]) == "Done|5 of 5", "every step done")
        expect(line(-3, ["Audit", "Spec"]) == "1 of 2|Audit", "a negative step reads as the first")
        expect(line(0, []) == nil, "no steps, no step line")
        let q = chaseStroke(at: 0.25), t = chaseStroke(at: 0.75), z = chaseStroke(at: 0)
        expect(abs(q.start) < 1e-9 && abs(q.end - 0.5) < 1e-9, "the chase head is halfway at a quarter")
        expect(abs(t.start - 0.5) < 1e-9 && abs(t.end - 1) < 1e-9, "the chase tail is halfway at three quarters")
        expect(z.start == 0 && z.end == 0, "the chase starts empty")
        return failed
    }
}

/// One link-frames run: checks, pose, frames, manifest, exit.
@MainActor
private final class LinkDrive {
    let app: AppDelegate
    let dir: URL
    private var problems: [String] = []
    private var count = 0
    private var finished = false
    private var source = ""
    private var pair: [String] = []

    init(app: AppDelegate, dir: URL) {
        self.app = app
        self.dir = dir
    }

    func go() async {
        if let why = ChatHarness.refusal() {
            Harness.log("LINKFRAMES refused: \(why). Point TAPSPACE_URL at a scratch server.")
            exit(1)
        }
        API.allowWrites = false
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let failed = HarnessLink.checks()
        Harness.log("LINKCHECK " + (failed.isEmpty ? "all passed" : "\(failed.count) failed"))
        let pooled = ChatPoolBoard.checks()
        Harness.log("POOLCHECK " + (pooled.isEmpty ? "all passed" : "\(pooled.count) failed"))
        problems += failed + pooled
        // The pill off the real menu bar; the window at the live width (offscreen under --snapshot).
        if let p = app.pill.panel { p.setFrameOrigin(NSPoint(x: -32000, y: p.frame.minY)) }
        app.window.setContentSize(NSSize(width: 1512, height: 900))
        let store = app.store
        let pool = HarnessPool.shared
        pool.talks = []
        await Harness.wait(20) { store.agentsLoaded }
        // The lead and the first face in its pool (a master when there is one).
        let members = HarnessPool.members(store)
        guard let lead = MuddiOrder.lead(store.agents),
              let face = members.first(where: { $0.kind != "tap" }) ?? members.first else {
            fail("no lead with a face in its pool on \(API.base.absoluteString)")
            return finish()
        }
        source = "pose"
        pair = [lead.title, face.title]
        store.thread = lead.key       // before the tab, so the switch reads only this thread
        store.tab = .chat
        await settle()
        await Harness.wait(5) { HarnessLink.shared.panel.width > 0 }
        if HarnessLink.shared.panel.width <= 0 { fail("the pool is not on screen (ChatView must host ChatPoolPanel)") }
        // From here the drive owns the clock: every frame steps every spring by exactly 1/30 s.
        Motion.main.link?.invalidate()
        for _ in 0..<6 { await frame() }                                  // the pool at rest
        let beat = Int((ChatPoolBeat.length * 30).rounded(.up))
        for (id, from, to, text) in [("link-1", lead.key, face.key, "Is the pool live yet?"),
                                     ("link-2", face.key, lead.key, "Live now, the faces are floating")] {
            pool.talks = [HarnessPool.talk(id, from: from, to: to, ts: Date().timeIntervalSince1970, text: text)].compactMap { $0 }
            if pool.talks?.isEmpty ?? true { fail("AgentTalk does not decode the served shape") }
            let drawn = ChatPoolBoard.lines(talks: pool.talks ?? [], lead: lead.key, members: members.map(\.key),
                                            now: Date().timeIntervalSince1970)
            if drawn.count != 1 { fail("the talk \(id) drew \(drawn.count) lines") }
            for i in 0..<beat { pool.pin = (id, Double(i) / 30); await frame() }   // draw-on, chase, ping
            pool.pin = nil
            for _ in 0..<6 { await frame() }                              // the line at rest
        }
        pool.talks = []
        for _ in 0..<15 { await frame() }                                 // the talk ends: the line leaves
        pool.talks = nil
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
        let crop = CGRect(x: r.minX * 2, y: r.minY * 2, width: r.width * 2, height: min(r.height, 560) * 2).integral
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
                                       "pair": pair, "source": source, "problems": problems]
        if let data = try? JSONSerialization.data(withJSONObject: manifest, options: [.prettyPrinted, .sortedKeys]) {
            try? data.write(to: dir.appendingPathComponent("frames.json"))
        }
        Harness.log("LINKFRAMES done: \(count) frames, \(problems.count) problems -> \(dir.path)")
        for p in problems { Harness.log("LINKFRAMES problem: \(p)") }
        exit(problems.isEmpty ? 0 : 1)
    }
}
