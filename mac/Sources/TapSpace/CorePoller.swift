import AppKit

/// One endpoint's poll loop. Never overlaps itself, skips its interval while
/// its surface is hidden (`isActive` false), and can be kicked to poll right
/// away (a tab opens, the window comes back, the server comes back).
@MainActor final class CorePoller {
    let name: String
    /// Seconds between the end of one poll and the start of the next.
    var every: Double
    /// Only poll while this holds (checked each interval; a kick re-checks at once).
    var isActive: () -> Bool
    private let body: () async -> Void
    private var loop: Task<Void, Never>?
    private var sleeper: Task<Void, Never>?
    private var inFlight = false
    private var again = false
    private var waiters: [CheckedContinuation<Void, Never>] = []
    /// Polls actually sent (verification).
    private(set) var count = 0

    init(_ name: String, every: Double, when isActive: @escaping () -> Bool = { true }, _ body: @escaping () async -> Void) {
        self.name = name
        self.every = every
        self.isActive = isActive
        self.body = body
    }

    var running: Bool { loop != nil }

    func start() {
        guard loop == nil else { return }
        loop = Task { @MainActor [weak self] in
            while let self, !Task.isCancelled {
                await self.tick()
                if self.again { self.again = false; continue }
                let s = Task<Void, Never> { [every = self.every] in _ = try? await Task.sleep(for: .seconds(every)) }
                self.sleeper = s
                await s.value
                self.sleeper = nil
            }
        }
    }

    func stop() {
        loop?.cancel()
        sleeper?.cancel()
        loop = nil
    }

    /// Poll now (if active). A kick during a poll schedules one more right after it.
    func kick() {
        if inFlight { again = true } else { sleeper?.cancel() }
    }

    /// One FRESH poll, awaited (after a send, the harness): waits out an
    /// in-flight poll (it may predate the change) and then polls again.
    func now() async {
        while inFlight { await withCheckedContinuation { waiters.append($0) } }
        await run()
    }

    private func tick() async {
        guard isActive() else { return }
        await run()
    }

    private func run() async {
        inFlight = true
        count += 1
        await body()
        inFlight = false
        let w = waiters
        waiters.removeAll()
        for c in w { c.resume() }
    }
}

/// Is the main window actually on screen? Drives which polls run: the pill
/// (counts) always needs state, agents and taps; the HUD and an open chat
/// thread only matter while the window can be seen.
@MainActor final class CoreSurfaces {
    var onChange: ((Bool) -> Void)?
    /// Verification runs render offscreen windows: treat them as visible.
    var forceVisible = false
    private(set) var windowVisible = true
    private var tokens: [NSObjectProtocol] = []

    func start() {
        let nc = NotificationCenter.default
        let names: [Notification.Name] = [
            NSWindow.didChangeOcclusionStateNotification, NSWindow.didMiniaturizeNotification,
            NSWindow.didDeminiaturizeNotification, NSWindow.willCloseNotification,
            NSWindow.didBecomeKeyNotification, NSWindow.didBecomeMainNotification,
            NSApplication.didHideNotification, NSApplication.didUnhideNotification,
            NSApplication.didChangeOcclusionStateNotification, NSApplication.didBecomeActiveNotification,
        ]
        for n in names {
            tokens.append(nc.addObserver(forName: n, object: nil, queue: .main) { [weak self] note in
                let closing = note.name == NSWindow.willCloseNotification ? note.object as? NSWindow : nil
                MainActor.assumeIsolated { self?.update(closing: closing) }
            })
        }
        update(closing: nil)
    }

    func update(closing: NSWindow? = nil) {
        let v = forceVisible || compute(closing: closing)
        guard v != windowVisible else { return }
        windowVisible = v
        onChange?(v)
    }

    private func compute(closing: NSWindow?) -> Bool {
        if NSApp.isHidden { return false }
        let mains = NSApp.windows.filter { $0 is MainWindow && $0 !== closing }
        // Before the window exists (launch), assume it will be.
        if mains.isEmpty { return closing == nil }
        return mains.contains { $0.isVisible && !$0.isMiniaturized && $0.occlusionState.contains(.visible) }
    }
}
