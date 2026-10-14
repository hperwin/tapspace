import AppKit
import QuartzCore

/// `--demo`: a scripted 10 s loop through every moving part, repeated until
/// the app quits, so `--fps` can be measured without a human at the mouse.
/// Writes are off (`API.allowWrites == false`); the bubble it sends is a
/// local demo bubble that never leaves the app.
///
///   0.0  Visual tab                     5.6  a bubble flies in from the composer
///   0.8  "Not quite" notes spread       6.5  a cursor drifts up to the pill: the
///   2.2  notes retract                       droplet bulges, sweeps, the pill
///   3.0  Chat tab                            spreads into its card, rows reveal,
///   3.9  rail selection to the next          the cursor leaves, it retracts
///   4.8  and the next                   9.6  back to Visual
@MainActor
final class HarnessDemo {
    static let shared = HarnessDemo()

    private var loop = 0
    private var cycleStart: CFTimeInterval = 0
    private var started = false

    func start(app: AppDelegate, fps: Bool) {
        guard !started else { return }
        started = true
        Task { @MainActor in
            await Harness.wait(20) { app.store.ticketsLoaded }
            await Harness.wait(6) { app.visual.mediaReady }
            Harness.log("DEMO start (\(app.visual.cards.count) cards, \(app.store.threadKeys.count) threads)")
            while true {
                await cycle(app)
                loop += 1
            }
        }
    }

    private func at(_ t: Double, _ label: String) async {
        let target = cycleStart + t
        while true {
            let left = target - CACurrentMediaTime()
            if left <= 0 { break }
            try? await Task.sleep(for: .milliseconds(max(1, Int(left * 1000))))
        }
        Harness.log(String(format: "DEMO loop=%d t=%.1f %@", loop, t, label))
    }

    private func cycle(_ app: AppDelegate) async {
        let store = app.store, visual = app.visual
        cycleStart = CACurrentMediaTime()

        await at(0.0, "visual")
        store.tab = .visual

        let card = visual.cards.first { !$0.ticket.isVideo && $0.ticket.status == "waiting" && !$0.notesOpen }
        await at(0.8, "notes open")
        if let card { visual.toggleNotes(card) }
        await at(2.2, "notes close")
        if let card, card.notesOpen { visual.toggleNotes(card) }

        await at(3.0, "chat")
        store.tab = .chat
        await at(3.9, "rail next")
        stepRail(store)
        await at(4.8, "rail next")
        stepRail(store)

        await at(5.6, "bubble")
        if !store.thread.isEmpty {
            store.demoBubble(["Looks great. Ship it when the pill lands.",
                              "Can you make the droplet a touch slower?",
                              "Love the wobble. Keep it."][loop % 3], to: store.thread)
        }

        await at(6.5, "pill hover")
        let pill = app.pill!
        let path = Self.pillPath(pill.liquid)
        let t0 = CACurrentMediaTime()
        pill.simMouse = { path(CACurrentMediaTime() - t0) }
        pill.wake(onlyIfNear: false)
        await at(9.4, "pill release")
        pill.releasePose()

        await at(9.6, "visual")
        store.tab = .visual
        await at(10.0, "loop")
    }

    private func stepRail(_ store: Store) {
        let keys = store.threadKeys
        guard !keys.isEmpty else { return }
        let i = keys.firstIndex(of: store.thread) ?? -1
        store.thread = keys[(i + 1) % keys.count]
    }

    /// A window-local cursor path (like SnappWAU's --sim-path): approach from
    /// below so the droplet reaches, sweep along the pill, dwell on it so it
    /// spreads, drift through the card's rows, then leave so it retracts.
    static func pillPath(_ liquid: PillLiquid) -> (Double) -> CGPoint {
        let x0 = liquid.anchorLeft, top = liquid.top
        let pw = max(liquid.shapeRect.width, 48), below = top + PillLiquid.pillH
        let keys: [(Double, CGPoint)] = [
            (0.0, CGPoint(x: x0 + 70, y: top + 200)),
            (0.5, CGPoint(x: x0 + 40, y: below + 40)),
            (0.9, CGPoint(x: x0 + 26, y: below + 14)),
            (1.25, CGPoint(x: x0 + pw - 4, y: below + 16)),
            (1.55, CGPoint(x: x0 + pw / 2, y: top + 12)),
            (2.0, CGPoint(x: x0 + 150, y: top + 70)),
            (2.35, CGPoint(x: x0 + 120, y: top + 130)),
            (2.7, CGPoint(x: x0 + 320, y: top + 430)),
        ]
        return { t in
            for (a, b) in zip(keys, keys.dropFirst()) where t <= b.0 {
                let f = smoothstep(0, 1, (t - a.0) / (b.0 - a.0))
                return CGPoint(x: lerp(a.1.x, b.1.x, f), y: lerp(a.1.y, b.1.y, f))
            }
            return keys[keys.count - 1].1
        }
    }
}
