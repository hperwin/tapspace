import AppKit
import CoreGraphics

/// Card frames (harness lane, for update cards): an update card arriving in the lead's thread and being answered,
/// as 2x PNG frames of the thread pane at 30 per second of motion plus `frames.json`, offscreen. Run with
/// `--snapshot <dir>` and TAPSPACE_CARD_FRAMES; Harness.run then dispatches here instead of the scene run:
///
///   1       pose: the lead's newest picture card and newest question card shown again as if they just arrived,
///           every spring on a fixed 1/30 s clock, then each answer shown on its way and echoed (nothing is posted)
///   live    on a PREVIEW only: waits for new cards from the server (tools/seed-cards.sh <port> pair), records their
///           real arrival, then answers them for real through CardAnswers, the very calls the card's buttons make
///           (Approve the first picture, Not quite "bigger score" on the second, tick "MLB lineup card" and say
///           "MLB first"), and records until the server's rows are in the thread
///   checks  CARDCHECK and CARDMOTION only, then exit
///
/// Every mode first checks the card rules on synthetic rows (the package has no test target) and refuses unless
/// TAPSPACE_URL names a local scratch server. Only `live` writes, and only to that server. `frames.json` is
/// frames2mp4.py's shape: {"server", "fps": 30, "scale": 2, "frames": [{"file": "f000.png", "t": <seconds>}],
/// "beats": [{"name", "frame"}], "cards": [ids], "problems"}; in live mode `cards` are the server ids that
/// tools/check.py judges.
///
///   TAPSPACE_URL=http://127.0.0.1:4813 TAPSPACE_CARD_FRAMES=1 .build/debug/TapSpace --snapshot <dir>
///   python3 <campaign>/tools/frames2mp4.py <dir> <dir>/card.mp4
@MainActor
enum HarnessCard {
    static var asked: Bool { CardPose.asked }

    static func run(app: AppDelegate, dir: URL) {
        let drive = CardDrive(app: app, dir: dir)
        Task { @MainActor in
            // A whole window per frame takes most of a second on a real rail and progress column.
            try? await Task.sleep(for: .seconds(600))
            drive.fail("watchdog: gave up after 600 s")
            drive.finish()
        }
        Task { @MainActor in await drive.go() }
    }

    /// A served row through the app's own decoder (the shape /api/chat serves).
    static func row(_ d: [String: Any]) -> ChatMessage? { decode(ChatMessage.self, d) }

    static func decode<T: Decodable>(_ type: T.Type, _ d: Any) -> T? {
        guard let data = try? JSONSerialization.data(withJSONObject: d) else { return nil }
        return try? JSONDecoder().decode(type, from: data)
    }

    /// The card rules on synthetic rows: one line per failed expectation, empty when every one holds.
    static func checks() -> [String] {
        var failed: [String] = []
        func expect(_ ok: Bool, _ what: String) { if !ok { failed.append("check: " + what) } }

        // Decode: the stored card row (CONTRACT 1), tolerant of what it does not know.
        let card: [String: Any] = [
            "v": 1, "line": "Three taps moved", "face": "o/r#1", "later": true,
            "faces": ["o/r#1": ["kind": "tap", "title": "Lineup card", "initial": "R", "phase": "landed"],
                      "agent:M": ["kind": "master", "look": 4, "title": "Cards", "state": "here"]],
            "visual": ["kind": "progress", "done": 3, "total": 5, "from": 2, "label": "3 of 5 landed"],
            "items": [["ref": "o/r#1", "kind": "tap", "phase": "landed", "goals": [3, 3],
                       "was": ["phase": "enroute", "goals": [1, 3]]], ["note": "no ref"]],
            "images": [["label": "Lineup card", "from": ["tap": "o/r#1", "i": 0]], ["label": "Box score"]],
            "ask": ["q": "Which first?", "checks": [["id": "c1", "label": "Lineup"], ["label": "no id"]], "text": true],
            "action": ["kind": "tap_accept", "tap": "o/r#1"]]
        func cardRow(_ c: Any, id: Int = 540) -> [String: Any] {
            ["id": id, "agent": "M", "role": "master", "body": "Three taps moved", "ts": 10.0,
             "images": ["/chat-media/a.png", "/chat-media/b.png"], "card": c]
        }
        let m = row(cardRow(card))
        let c = m?.card
        expect(c?.line == "Three taps moved" && c?.face == "o/r#1", "a card row with a key it does not know keeps its card")
        expect(c?.items.map(\.ref) == ["o/r#1"] && c?.items.first?.was?.phase == "enroute" && c?.items.first?.goals == [3, 3],
               "an item without a ref is skipped, the rest keep their snapshot and was")
        expect(c?.ask?.checks.map(\.id) == ["c1"] && c?.ask?.text == true, "a check without an id is skipped")
        expect(c?.faces["o/r#1"]?.initial == "R" && c?.faces["o/r#1"]?.phase == "landed" && c?.faces["agent:M"]?.look == 4,
               "faces decode kind, look, title, initial, phase")
        expect(c?.visual?.from == 2 && c?.images.first?.from?.tap == "o/r#1", "the visual's from and an image's proof source")
        expect(c?.action == CardAction(kind: "tap_accept", tap: "o/r#1"), "the card's one action")
        var v2 = card
        v2["v"] = 2
        let other = row(cardRow(v2))
        expect(other != nil && other?.card == nil && other?.body == "Three taps moved", "another card version draws its body")
        expect(row(cardRow("x"))?.card == nil, "a card that is not an object is no card")
        let face = row(cardRow(card.merging(["face": NSNull()] as [String: Any]) { $1 }))?.card
        expect(face != nil && face?.face == "", "a card with no subject (a null face) still draws")
        func answerRow(_ a: [String: Any], nonce: String = "") -> ChatMessage? {
            row(["id": 541, "agent": "M", "role": "you", "body": "x", "ts": 11.0, "nonce": nonce, "answer": a])
        }
        let ap = answerRow(["card": 540, "image": 0, "verdict": "approve"], nonce: "540:i0:approve:ab12cd34")
        expect(ap?.answer?.image == 0 && ap?.answer?.verdict == "approve" && ap?.nonce == "540:i0:approve:ab12cd34",
               "an image answer and its nonce decode")
        let ask = answerRow(["card": 540, "checks": ["c1", "c2"], "text": "ship it"])
        expect(ask?.answer?.image == nil && ask?.answer?.checks == ["c1", "c2"] && ask?.answer?.text == "ship it",
               "an ask answer decodes its ticks and words")
        expect(answerRow(["card": 540, "action": "deny", "note": "not yet"])?.answer?.action == "deny", "an action answer decodes")

        // Payload: the wire shapes (CONTRACT 2), no body ever.
        func keys(_ a: ChatAnswer) -> [String] { a.payload.keys.sorted() }
        expect(keys(ChatAnswer(card: 5, image: 0, verdict: "approve", note: "x")) == ["card", "image", "verdict"],
               "approve sends card, image and verdict, never a note")
        expect(keys(ChatAnswer(card: 5, image: 1, verdict: "change", note: "bigger")) == ["card", "image", "note", "verdict"],
               "Not quite sends its note")
        expect(keys(ChatAnswer(card: 5, checks: ["c1"])) == ["card", "checks"], "an ask with no words sends no text")
        expect(keys(ChatAnswer(card: 5, note: "later", action: "deny")) == ["action", "card", "note"], "a deny sends its note")

        // Board: the fleet, focus and the lead's activity (CONTRACT 5, 6, 7); an older server's board has none.
        let lead: [String: Any] = ["name": "Muddi", "state": "thinking", "title": "Muddi", "role": "lead", "look": 0,
                                   "activity": ["state": "working", "word": "Checking a tap", "since": 5.0]]
        let board = decode(AgentsBoard.self, [
            "agents": [lead, ["name": "Cards", "title": "Chat update cards", "look": 4, "parent": "Muddi"]],
            "fleet": [["key": "o/r#1", "kind": "tap", "phase": "holding", "initial": "R", "needs_you": true],
                      ["kind": "master"], ["key": "agent:Cards", "look": 4, "done": 2, "total": 7, "working": true]],
            "focus": "agent:Muddi"])
        expect(board?.fleet.map(\.key) == ["o/r#1", "agent:Cards"] && board?.fleet.first?.needsYou == true
               && board?.fleet.last?.working == true, "fleet entries keep the server's order; one without a key is skipped")
        expect(board?.focus == "agent:Muddi" && board?.agents.first?.activity?.word == "Checking a tap"
               && board?.agents.last?.look == 4 && board?.agents.last?.activity == nil, "focus, looks and the lead's activity")
        let old = decode(AgentsBoard.self, ["agents": [["name": "Muddi"]]])
        expect(old?.fleet == [] && old?.focus == nil && old?.agents.first?.look == nil, "an older server's board: no fleet, focus or look")

        // Fold (CONTRACT 3): answers draw inside their card; later answers win per image and per ask.
        func you(_ id: Int, _ a: [String: Any], nonce: String = "") -> ChatMessage? {
            row(["id": id, "agent": "M", "role": "you", "body": "x", "ts": Double(id), "nonce": nonce, "answer": a])
        }
        if let cr = m, let a1 = you(541, ["card": 540, "image": 0, "verdict": "approve"]),
           let a2 = you(542, ["card": 540, "image": 0, "verdict": "change", "note": "bigger"]),
           let q1 = you(543, ["card": 540, "checks": ["c1"]]), let q2 = you(544, ["card": 540, "checks": [], "text": "later"]),
           let stray = you(545, ["card": 9, "image": 0, "verdict": "approve"]) {
            let fold = CardFold(rows: [cr, a1, a2, q1, q2, stray], pending: [])
            expect(fold.hides(a1) && fold.hides(q2) && !fold.hides(stray) && !fold.hides(cr),
                   "an answer to a card in the thread is hidden; one to a card not loaded is a bubble")
            let st = fold.state(540)
            expect(st.images[0]?.approve == false && st.images[0]?.note == "bigger", "approve then Not quite: Not quite and its note")
            expect(st.asked && st.checks.isEmpty && st.text == "later", "two ask answers: the later one")
            let p = PendingAnswer(nonce: "540:i1:approve:00000001", agent: "M", answer: ChatAnswer(card: 540, image: 1, verdict: "approve"))
            expect(CardFold(rows: [cr], pending: [p]).state(540).images[1]?.sending == true, "an answer on its way shows as sending")
            if let landed = you(546, ["card": 540, "image": 1, "verdict": "approve"], nonce: p.nonce) {
                expect(CardFold(rows: [cr, landed], pending: [p]).state(540).images[1]?.sending == false,
                       "an answer whose row has landed is no longer on its way")
            }
        } else {
            expect(false, "fold rows decode")
        }

        // Rail (CONTRACT 5, 10): with a lead everything else folds under it; no lead, or open, nothing does.
        let agents = [["name": "Muddi", "role": "lead"], ["name": "A", "parent": "Muddi"], ["name": "B"]]
            .compactMap { decode(Agent.self, $0) }
        let taps = [["key": "o/r#1"], ["key": "o/s#2"]].compactMap { decode(Tap.self, $0) }
        expect(MuddiOrder.folded(agents, taps: taps, open: false) == ["agent:A", "agent:B", "o/r#1", "o/s#2"],
               "closed: every other master and every tap folds")
        expect(MuddiOrder.folded(agents, taps: taps, open: true).isEmpty, "open: nothing folds")
        expect(MuddiOrder.folded(Array(agents.dropFirst()), taps: taps, open: false).isEmpty, "no lead: nothing folds")
        expect(ChatFleetStrip.fit(172, count: 3) == 3 && ChatFleetStrip.fit(172, count: 12) == 4
               && ChatFleetStrip.fit(600, count: 12) == ChatFleetStrip.beads && ChatFleetStrip.fit(0, count: 2) == 2,
               "the strip shows as many beads as fit beside the disclosure and +N, at most 8")
        expect(LeadActivityStrip.face("working") == "thinking" && LeadActivityStrip.face("waiting") == "here"
               && LeadActivityStrip.face("gone") == "away" && LeadActivityStrip.face("later") == "idle",
               "the lead's face wears its activity: working focused, waiting happy, gone grey, anything else sleepy")
        expect(CardItemShown.steps(0, of: 4) == "1 of 4" && CardItemShown.steps(2, of: 4) == "3 of 4"
               && CardItemShown.steps(3, of: 4) == "4 of 4" && CardItemShown.steps(4, of: 4) == "4 of 4",
               "a master's count is the step it is on: 3 of 4 at current 2, 4 of 4 at its last step and when done")

        // First view (CONTRACT 10, 12): at an open, the newest two cards newer than the read mark play.
        let cards = [(key: "a", ts: 1.0), (key: "b", ts: 5.0), (key: "c", ts: 6.0), (key: "d", ts: 7.0)]
        expect(BubbleAnimator.opening(cards, after: 4) == ["c", "d"], "the newest two unseen cards play at an open")
        expect(BubbleAnimator.opening(cards, after: 6) == ["d"] && BubbleAnimator.opening(cards, after: 9).isEmpty,
               "seen cards draw at rest")
        let anim = BubbleAnimator(), keys = cards.map(\.key)
        anim.sync(thread: "t", loaded: true, keys: keys, fly: [], cards: cards, mark: { 4 }, plays: false)
        anim.sync(thread: "u", loaded: true, keys: [], fly: [])
        anim.sync(thread: "t", loaded: true, keys: keys, fly: [], cards: cards, mark: { 4 }, plays: true)
        expect(!keys.contains(where: anim.opens), "a card drawn at rest at an open never plays at a later open")
        return failed
    }
}

/// Whether an answer reached the server (nil while it is on its way).
@MainActor
private final class Sent { var ok: Bool? }

/// One card-frames run: checks, the lead's thread, frames, manifest, exit.
@MainActor
private final class CardDrive {
    let app: AppDelegate
    let dir: URL
    private var problems: [String] = []
    private var frames: [[String: Any]] = []
    private var beats: [[String: Any]] = []
    private var cards: [Int] = []
    private var finished = false
    private var store: Store { app.store }

    init(app: AppDelegate, dir: URL) {
        self.app = app
        self.dir = dir
    }

    func go() async {
        if let why = ChatHarness.refusal() {
            Harness.log("CARDFRAMES refused: \(why). Point TAPSPACE_URL at a scratch server.")
            exit(1)
        }
        let live = CardPose.mode == "live"
        // Only the live round trip answers, and only the scratch server the refusal just allowed.
        API.allowWrites = live
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let failed = HarnessCard.checks()
        Harness.log("CARDCHECK " + (failed.isEmpty ? "all passed" : "\(failed.count) failed"))
        for f in failed { Harness.log("CARDCHECK " + f) }
        problems += failed
        let motion = CardMotion.checks()
        Harness.log("CARDMOTION " + (motion.isEmpty ? "ok" : "bad " + motion.joined(separator: ", ")))
        if !motion.isEmpty { problems.append("CARDMOTION bad " + motion.joined(separator: ", ")) }
        if CardPose.mode == "checks" { return finish() }

        // The pill off the real menu bar; a window tall enough for a whole card (offscreen under --snapshot).
        if let p = app.pill.panel { p.setFrameOrigin(NSPoint(x: -32000, y: p.frame.minY)) }
        app.window.setContentSize(NSSize(width: 1512, height: 1200))
        UserDefaults.standard.set(false, forKey: RailView.openKey)
        await Harness.wait(20) { store.agentsLoaded && MuddiOrder.lead(store.agents) != nil }
        guard let lead = MuddiOrder.lead(store.agents) else {
            fail("no lead on \(API.base.absoluteString)")
            return finish()
        }
        store.thread = lead.key           // before the tab, so the switch reads only this thread
        store.windowKey = true
        store.tab = .chat
        await Harness.wait(10) { store.chats[lead.name] != nil }
        await settle()
        if CardPose.shared.pane.width <= 0 { fail("the thread pane reported no frame (PaneView must report CardPose.pane)") }
        if let a = lead.activity { Harness.log("CARDFRAMES lead activity: \(a.state) \"\(a.word)\"") }
        // From here the drive owns the clock: every frame steps every spring by exactly 1/30 s.
        Motion.main.link?.invalidate()
        if live { await liveRun(lead) } else { await poseRun(lead) }
        finish()
    }

    // MARK: Pose: the lead's own cards, shown again as arrivals, answered on screen only

    private func poseRun(_ lead: Agent) async {
        let rows = store.chats[lead.name] ?? []
        let all = rows.filter { $0.card != nil }
        let plain = all.filter { $0.card?.action == nil }
        guard let a = plain.last(where: { $0.images.count >= 2 }) ?? plain.last(where: { !$0.images.isEmpty }) ?? all.last else {
            fail("the lead's thread has no update card")
            return
        }
        let b = all.last { $0.card?.ask != nil && $0.id != a.id }
        var next = (rows.map(\.id).max() ?? 0) + 1000
        func posed(_ m: ChatMessage) -> ChatMessage {
            next += 1
            return ChatMessage(id: next, agent: m.agent, role: "master", body: m.body, ts: Date().timeIntervalSince1970,
                               images: m.images, card: m.card)
        }
        await record(6, "rest")
        let pa = posed(a)
        CardPose.shared.rows.append(pa)
        cards.append(pa.id)
        await record(75, "arrive A")
        var pb: ChatMessage?
        if let b {
            let p = posed(b)
            CardPose.shared.rows.append(p)
            cards.append(p.id)
            pb = p
            await record(75, "arrive B")
        }
        func echo(_ ans: ChatAnswer, nonce: String, body: String) {
            next += 1
            CardPose.shared.rows.append(ChatMessage(id: next, agent: lead.name, role: "you", body: body,
                                                    ts: Date().timeIntervalSince1970, answer: ans, nonce: nonce))
        }
        let labels = pa.card?.images.map(\.label) ?? []
        func label(_ i: Int) -> String { labels.indices.contains(i) && !labels[i].isEmpty ? labels[i] : "Image \(i + 1)" }
        if pa.card?.action == nil, !pa.images.isEmpty {
            let ok = ChatAnswer(card: pa.id, image: 0, verdict: "approve")
            let n = CardAnswers.shared.pose(ok, to: lead.name)
            await record(30, "approve A 0")
            echo(ok, nonce: n, body: "Approved: \(label(0))")
            await record(15, "approved A 0")
        }
        if pa.card?.action == nil, pa.images.count >= 2 {
            let draft = CardDrafts.image(pa.id, 1)
            draft.toggleNotes()
            draft.text = "bigger score"
            await record(40, "not quite A 1")
            let change = ChatAnswer(card: pa.id, image: 1, verdict: "change", note: draft.text)
            let n = CardAnswers.shared.pose(change, to: lead.name)
            draft.toggleNotes()
            draft.text = ""
            await record(30, "sent A 1")
            echo(change, nonce: n, body: "Not quite: \(label(1)): bigger score")
            await record(15, "changed A 1")
        }
        if let card = (pa.card?.ask != nil ? pa : pb), let ask = card.card?.ask {
            let draft = CardDrafts.ask(card.id)
            let pick = ask.checks.first { $0.label == "MLB lineup card" } ?? ask.checks.first
            if let pick { draft.toggle(pick.id) }
            if ask.text { draft.text = "MLB first" }
            await record(10, "tick")
            let answer = ChatAnswer(card: card.id, checks: pick.map { [$0.id] } ?? [], text: ask.text ? draft.text : "")
            let n = CardAnswers.shared.pose(answer, to: lead.name)
            draft.ticks = []
            draft.text = ""
            await record(20, "ask sent")
            echo(answer, nonce: n, body: ask.q + " " + (pick?.label ?? "(no boxes ticked)") + ". MLB first")
            await record(25, "answered")
        }
    }

    // MARK: Live: real arrivals from the server, answered for real on that server

    private func liveRun(_ lead: Agent) async {
        let before = (store.chats[lead.name] ?? []).map(\.id).max() ?? 0
        func fresh() -> [ChatMessage] { (store.chats[lead.name] ?? []).filter { $0.id > before && $0.card != nil } }
        Harness.log("CARDFRAMES waiting for new cards on \(API.base.absoluteString)")
        guard await Harness.wait(180, { !fresh().isEmpty }) else {
            fail("no new card arrived in 180 s")
            return
        }
        await record(75, "arrive A")
        if await Harness.wait(10, { fresh().count >= 2 }) { await record(75, "arrive B") }
        let got = fresh()
        cards = got.map(\.id)
        guard let a = got.first(where: { $0.card?.action == nil && !$0.images.isEmpty }) ?? got.first else { return }
        let ask = got.last { $0.card?.ask != nil }
        func landed(_ match: @escaping (ChatAnswer) -> Bool) -> () -> Bool {
            { (self.store.chats[lead.name] ?? []).contains { $0.answer.map(match) == true } }
        }
        func send(_ ans: ChatAnswer, _ name: String) async {
            let sent = Sent()
            Task { @MainActor in sent.ok = await CardAnswers.shared.send(ans, to: lead.name) }
            let echo = landed { $0.card == ans.card && $0.image == ans.image && $0.verdict == ans.verdict && $0.action == ans.action }
            var n = 0
            while n < 90, !(sent.ok == true && echo()), sent.ok != false {
                await frame()
                n += 1
            }
            let ok = sent.ok
            beats.append(["name": name, "frame": frames.count - n])
            if ok == false { fail("\(name): the answer did not reach the server") } else if !echo() { fail("\(name): the server's row never landed") }
            await record(15, name + " done")
        }
        if a.card?.action == nil, !a.images.isEmpty {
            await send(ChatAnswer(card: a.id, image: 0, verdict: "approve"), "approve A 0")
        }
        if a.card?.action == nil, a.images.count >= 2 {
            let draft = CardDrafts.image(a.id, 1)
            draft.toggleNotes()
            draft.text = "bigger score"
            await record(40, "not quite A 1")
            draft.toggleNotes()
            await send(ChatAnswer(card: a.id, image: 1, verdict: "change", note: "bigger score"), "sent A 1")
            draft.text = ""
        }
        if let ask, let q = ask.card?.ask {
            let pick = q.checks.first { $0.label == "MLB lineup card" } ?? q.checks.first
            let draft = CardDrafts.ask(ask.id)
            if let pick { draft.toggle(pick.id) }
            if q.text { draft.text = "MLB first" }
            await record(10, "tick")
            await send(ChatAnswer(card: ask.id, checks: pick.map { [$0.id] } ?? [], text: q.text ? "MLB first" : ""), "ask sent")
        }
    }

    // MARK: Frames

    private func record(_ n: Int, _ beat: String) async {
        beats.append(["name": beat, "frame": frames.count])
        for _ in 0..<n { await frame() }
    }

    /// Step every spring by 1/30 s, let SwiftUI commit, render the window and keep the thread pane.
    private func frame() async {
        Motion.main.run(seconds: 1.0 / 30, step: 1.0 / 240)
        try? await Task.sleep(for: .milliseconds(30))
        let i = frames.count
        guard let view = app.window.contentView, let full = HarnessSnapshot.render(view) else {
            fail(String(format: "f%03d: render failed", i))
            return
        }
        let r = CardPose.shared.pane.insetBy(dx: -8, dy: -8).intersection(view.bounds)
        let image = (r.isEmpty ? nil : full.cropping(to: CGRect(x: r.minX * 2, y: r.minY * 2, width: r.width * 2,
                                                                height: r.height * 2).integral)) ?? full
        if i == 0, HarnessSnapshot.ink(image) < 0.003 { fail("f000 looks blank") }
        let name = String(format: "f%03d.png", i)
        guard let png = NSBitmapImageRep(cgImage: image).representation(using: .png, properties: [:]),
              (try? png.write(to: dir.appendingPathComponent(name))) != nil else {
            fail("could not write \(name)")
            return
        }
        frames.append(["file": name, "t": Double(i) / 30])
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
        let manifest: [String: Any] = ["server": API.base.absoluteString, "mode": CardPose.mode ?? "", "fps": 30, "scale": 2,
                                       "frames": frames, "beats": beats, "cards": cards, "problems": problems]
        if let data = try? JSONSerialization.data(withJSONObject: manifest, options: [.prettyPrinted, .sortedKeys]) {
            try? data.write(to: dir.appendingPathComponent("frames.json"))
        }
        Harness.log("CARDFRAMES done: \(frames.count) frames, \(problems.count) problems -> \(dir.path)")
        for p in problems { Harness.log("CARDFRAMES problem: \(p)") }
        exit(problems.isEmpty ? 0 : 1)
    }
}
