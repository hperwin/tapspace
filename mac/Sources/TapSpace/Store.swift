import AppKit
import ImageIO
import Observation
import SwiftUI

enum Tab: String { case visual, chat }
enum Connection { case connecting, live, down }

/// A message Hayden just sent, shown before the server (or GitHub) has it.
struct PendingMessage: Identifiable, Equatable {
    let id: String
    let key: String          // thread key
    let body: String
    let ts: Double
    var demo = false
}

struct Toast: Equatable {
    let id = UUID()
    let text: String
}

/// Polls the TapSpace server and owns everything the views and the pill read.
///
/// Cadence (SPEC): state 1.5 s, activity 2 s, agents 2 s, the open agent chat
/// 1.5 s, taps 5 s. State, agents and taps feed the menu-bar pill, so they run
/// whenever the server is up; activity (the HUD) and the open chat only run
/// while the main window is on screen (and, for chat, the Chat tab is up).
/// A poll that lands unchanged writes nothing, so views stay still.
@MainActor @Observable
final class Store {
    static let shared = Store()

    private(set) var connection: Connection = .connecting
    private(set) var tickets: [Ticket] = []
    private(set) var ticketsLoaded = false
    var agents: [Agent] = []
    private(set) var agentsLoaded = false
    private(set) var chats: [String: [ChatMessage]] = [:]
    private(set) var taps = TapsResponse()
    private(set) var activity: [ActivityRow] = []
    private(set) var sessions = Sessions()
    private(set) var pending: [PendingMessage] = []
    /// Server message keys that replaced an optimistic bubble (no second entrance).
    private(set) var confirmed: [String: String] = [:]
    /// Ticket versions Hayden decided here, ahead of the next poll.
    private(set) var decided: Set<String> = []
    private(set) var reads: [String: Double] = [:]
    /// The main window is on screen (not hidden, minimized or fully covered).
    private(set) var windowVisible = true
    var windowKey = false
    var toast: Toast?
    /// Bumped when Hayden asks to see a particular card (pill row click).
    var focusCard: String?

    var tab: Tab = .visual {
        didSet {
            guard tab != oldValue else { return }
            Self.defaults.set(tab.rawValue, forKey: Self.tabKey)
            tabProgress.set(tab == .chat ? 1 : 0, .tab)
            if tab == .chat {
                markRead(thread)
                chatPoller.kick()
            }
        }
    }

    var thread: String = "" {
        didSet {
            guard thread != oldValue else { return }
            Self.defaults.set(thread, forKey: Self.threadKey)
            threadOpened()
        }
    }

    @ObservationIgnored let tabProgress = SpringBox(0, .tab)
    @ObservationIgnored var onTickets: (([Ticket]) -> Void)?
    /// Fired when anything the pill shows changed, so it wakes its display link.
    @ObservationIgnored var onChange: (() -> Void)?
    @ObservationIgnored private var failures = 0
    @ObservationIgnored private var booted = false
    @ObservationIgnored private var started = false
    /// No read state was ever saved: the first full load marks everything read.
    @ObservationIgnored private var seedReads = false
    @ObservationIgnored private var saveReadsTask: Task<Void, Never>?
    @ObservationIgnored private var fetchingChats: Set<String> = []
    @ObservationIgnored let surfaces = CoreSurfaces()

    @ObservationIgnored private(set) lazy var statePoller = CorePoller("state", every: 1.5) { [unowned self] in await self.pollState() }
    @ObservationIgnored private(set) lazy var agentsPoller = CorePoller("agents", every: 2.0, when: { [unowned self] in self.connection == .live }) { [unowned self] in await self.pollAgents() }
    @ObservationIgnored private(set) lazy var chatPoller = CorePoller("chat", every: 1.5, when: { [unowned self] in self.chatVisible }) { [unowned self] in await self.pollChat() }
    @ObservationIgnored private(set) lazy var tapsPoller = CorePoller("taps", every: 5.0, when: { [unowned self] in self.connection == .live }) { [unowned self] in await self.pollTaps() }
    @ObservationIgnored private(set) lazy var activityPoller = CorePoller("activity", every: 2.0, when: { [unowned self] in self.connection == .live && self.windowVisible }) { [unowned self] in await self.pollActivity() }

    var pollers: [CorePoller] { [statePoller, agentsPoller, chatPoller, tapsPoller, activityPoller] }

    private static let defaults = UserDefaults.standard
    private static let tabKey = "tab", threadKey = "thread", readsKey = "reads"

    init() {
        let d = Self.defaults
        if let t = d.string(forKey: Self.tabKey).flatMap(Tab.init(rawValue:)) { tab = t }
        thread = d.string(forKey: Self.threadKey) ?? ""
        tabProgress.snap(tab == .chat ? 1 : 0)
        if let saved = d.dictionary(forKey: Self.readsKey) {
            for (k, v) in saved { if let n = (v as? NSNumber)?.doubleValue { reads[k] = n } }
        } else {
            seedReads = true
        }
    }

    // MARK: Polling

    func start() {
        guard !started else { return }
        started = true
        let flags = Flags(Array(CommandLine.arguments.dropFirst()))
        surfaces.forceVisible = flags.snapshot != nil || flags.demo
        surfaces.onChange = { [weak self] visible in
            guard let self else { return }
            self.windowVisible = visible
            if visible { self.activityPoller.kick(); self.chatPoller.kick() }
        }
        surfaces.start()
        windowVisible = surfaces.windowVisible
        // A tap thread restored from last launch asks GitHub for fresh comments too.
        threadOpened(poll: false)
        for p in pollers { p.start() }
    }

    /// Poll every endpoint once, now, and wait for all of them (harness, reconnect).
    func refreshAll() async {
        await statePoller.now()
        async let a: Void = agentsPoller.now()
        async let t: Void = tapsPoller.now()
        async let c: Void = chatPoller.now()
        async let h: Void = activityPoller.now()
        _ = await (a, t, c, h)
    }

    private var chatVisible: Bool {
        connection == .live && windowVisible && tab == .chat && thread.hasPrefix("agent:")
    }

    private func threadOpened(poll: Bool = true) {
        // A thread chosen before start() (launch flag, restored default) opens once, from start().
        guard started else { return }
        // Every tap open marks that tap hot, as the web page does: the server re-reads
        // it from GitHub now and every few seconds while someone is looking at it.
        if !thread.hasPrefix("agent:"), !thread.isEmpty {
            let key = thread
            Task { try? await API.post("/api/taps/refresh", ["key": key], speaksForHayden: false) }
        }
        guard poll, thread.hasPrefix("agent:") else { return }
        // On the Chat tab the loop picks it up now; elsewhere fetch it once,
        // so switching tabs lands on a thread that is already there.
        if chatVisible { chatPoller.kick() } else if connection == .live { Task { await chatPoller.now() } }
    }

    func pollState() async {
        do {
            let t = try await API.state()
            failures = 0
            setLive()
            let first = !ticketsLoaded
            if first { ticketsLoaded = true }
            if first || t != tickets {
                tickets = t
                // Forget local decisions the server has caught up with.
                let still = decided.filter { key in t.contains { $0.versionKey == key && $0.status == "waiting" } }
                if still != decided { decided = still }
                onTickets?(t)
                onChange?()
            }
        } catch {
            if case APIError.decode = error { setLive(); return }   // up, but a bad payload
            failures += 1
            if failures >= 2 {
                let next: Connection = ticketsLoaded ? .down : .connecting
                if connection != next { connection = next; onChange?() }
                bootServerIfNeeded()
            }
        }
    }

    private func setLive() {
        guard connection != .live else { return }
        connection = .live
        booted = false
        // Everything gated on the connection catches up at once.
        agentsPoller.kick(); tapsPoller.kick(); chatPoller.kick(); activityPoller.kick()
        if thread.hasPrefix("agent:"), !chatVisible { Task { await chatPoller.now() } }
        onChange?()
    }

    func pollAgents() async {
        guard let a = try? await API.agents() else { return }
        var changed = false
        if !agentsLoaded { agentsLoaded = true; changed = true }
        if a != agents { agents = a; changed = true }
        afterThreadsLoaded()
        if changed { onChange?(); fetchFreshReplies() }
    }

    /// An agent that replied in a thread that isn't open: fetch that thread
    /// once, so the pill and the rail can show what it said. Never on a loop.
    private func fetchFreshReplies() {
        for a in agents where (a.last_master ?? 0) > 0 && a.key != thread {
            let have = chats[a.name]?.last { $0.role == "master" }?.ts ?? 0
            guard (a.last_master ?? 0) > have + 0.001, isUnread(a.key), !fetchingChats.contains(a.name) else { continue }
            fetchingChats.insert(a.name)
            Task { @MainActor [weak self] in
                let rows = try? await API.chat(a.name)
                guard let self else { return }
                self.fetchingChats.remove(a.name)
                if let rows, self.chats[a.name] != rows { self.chats[a.name] = rows; self.onChange?() }
            }
        }
    }

    func pollChat() async {
        guard thread.hasPrefix("agent:") else { return }
        let name = String(thread.dropFirst(6))
        guard let rows = try? await API.chat(name) else { return }
        if chats[name] != rows { chats[name] = rows }
        reconcilePending()
    }

    func pollTaps() async {
        guard let t = try? await API.taps() else { return }
        if t != taps {
            taps = t
            reconcilePending()
            onChange?()
        }
        afterThreadsLoaded()
    }

    func pollActivity() async {
        guard let a = try? await API.activity() else { return }
        if a.rows != activity { activity = a.rows }
        if a.sessions != sessions { sessions = a.sessions }
    }

    private func afterThreadsLoaded() {
        if thread.isEmpty || threadMissing { pickFallbackThread() }
        // First launch ever: what already happened is not "unread".
        if seedReads, agentsLoaded, taps.fetched > 0 {
            seedReads = false
            for k in threadKeys { reads[k] = max(readAt(k), lastTheirs(k)) }
            saveReads()
        }
    }

    /// The server is down: start the one `CoreServer.plan()` allows (the live rig via
    /// tap.py up, or the app's own server on TAPSPACE_DATA; never one on a scratch
    /// port that would serve the live data). Retries every so often while down.
    private func bootServerIfNeeded() {
        guard !booted, CoreServer.canStart else { return }
        booted = true
        Task { @MainActor [weak self] in
            try? await Task.sleep(for: .seconds(20))
            if self?.connection != .live { self?.booted = false }
        }
        CoreServer.start()
    }

    // MARK: Threads

    var threadMissing: Bool {
        if thread.hasPrefix("agent:") { return agentsLoaded && !agents.contains { $0.key == thread } }
        return !taps.items.contains { $0.key == thread } && taps.fetched > 0
    }

    private func pickFallbackThread() {
        guard agentsLoaded else { return }
        let fall = agents.first?.key ?? taps.items.first?.key ?? ""
        if !fall.isEmpty, fall != thread { thread = fall }
    }

    var threadKeys: [String] { agents.map(\.key) + taps.items.map(\.key) }

    func agent(_ key: String) -> Agent? { agents.first { $0.key == key } }
    func tap(_ key: String) -> Tap? { taps.items.first { $0.key == key } }

    // MARK: Read state (persisted per thread in UserDefaults, like the web page's localStorage)

    func readAt(_ key: String) -> Double { reads[key] ?? 0 }

    func markRead(_ key: String) {
        guard !key.isEmpty else { return }
        guard lastTheirs(key) > readAt(key) || reads[key] == nil else { return }
        reads[key] = Date().timeIntervalSince1970
        saveReads()
        onChange?()
    }

    /// Coalesced write: a burst of reads costs one defaults write.
    private func saveReads() {
        saveReadsTask?.cancel()
        saveReadsTask = Task { @MainActor [weak self] in
            try? await Task.sleep(for: .milliseconds(400))
            guard let self, !Task.isCancelled else { return }
            // Keep live threads plus the most recent others (bounded).
            let live = Set(self.threadKeys)
            var keep = self.reads.filter { live.contains($0.key) }
            for (k, v) in self.reads.filter({ !live.contains($0.key) }).sorted(by: { $0.value > $1.value }).prefix(200) { keep[k] = v }
            Self.defaults.set(keep, forKey: Self.readsKey)
        }
    }

    func lastTheirs(_ key: String) -> Double {
        if key.hasPrefix("agent:") { return agent(key)?.last_master ?? 0 }
        return tap(key)?.messages.last { $0.who == "tap" }?.date ?? 0
    }

    func isViewing(_ key: String) -> Bool { tab == .chat && thread == key && windowKey }

    func isUnread(_ key: String) -> Bool {
        lastTheirs(key) > readAt(key) && !isViewing(key)
    }

    // MARK: Counts

    var waitingCards: [Ticket] {
        tickets.filter { $0.status == "waiting" && !decided.contains($0.versionKey) }
    }
    var needsYouTaps: [Tap] { taps.items.filter { $0.status == "needs-you" } }
    var unreadAgents: [Agent] { agents.filter { isUnread($0.key) } }

    var visualBadge: Int { waitingCards.count }
    var chatBadge: Int { needsYouTaps.count + unreadAgents.count }
    var waitingTotal: Int { visualBadge + chatBadge }

    // MARK: Acting for Hayden

    /// Optimistic: the card leaves the counts at once and comes back if the
    /// server refuses.
    func decide(_ t: Ticket, approve: Bool, message: String = "") async -> Bool {
        let key = t.versionKey
        let had = decided.contains(key)
        decided.insert(key)
        onChange?()
        do {
            try await API.post("/api/decide", ["id": t.id, "action": approve ? "approve" : "change", "message": message])
            statePoller.kick()
            return true
        } catch {
            if !had { decided.remove(key) }
            onChange?()
            show("Didn’t reach TapSpace: \(error.localizedDescription)")
            return false
        }
    }

    /// Returns false (and restores nothing itself) when the send failed.
    func send(_ body: String, to key: String) async -> Bool {
        let text = body.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return false }
        let p = PendingMessage(id: "p:" + UUID().uuidString, key: key, body: text, ts: Date().timeIntervalSince1970)
        pending.append(p)
        if key.hasPrefix("agent:") {
            let name = String(key.dropFirst(6))
            let before = agents.first { $0.key == key }?.state
            if let i = agents.firstIndex(where: { $0.key == key }) { agents[i].state = "thinking" }
            do {
                // The server hands back the stored row: swap it in now, no poll gap.
                if let row = try await API.say(text, toAgent: name) {
                    var rows = chats[name] ?? []
                    if !rows.contains(where: { $0.id == row.id }) {
                        rows.append(row)
                        chats[name] = rows
                    }
                    reconcilePending()
                }
                chatPoller.kick()
                agentsPoller.kick()
                return true
            } catch {
                pending.removeAll { $0.id == p.id }
                if let i = agents.firstIndex(where: { $0.key == key }), let before { agents[i].state = before }
                show(error.localizedDescription.isEmpty ? "Didn’t send. Is TapSpace still running?" : "Didn’t send: \(error.localizedDescription)")
                return false
            }
        }
        do {
            try await API.post("/api/taps/say", ["key": key, "body": text])
            // Keep the bubble until GitHub shows the comment (or two minutes pass).
            Task { @MainActor [weak self] in
                for delay in [2.5, 3.5] {
                    try? await Task.sleep(for: .seconds(delay))
                    guard let self, self.pending.contains(where: { $0.id == p.id }) else { return }
                    await self.tapsPoller.now()
                }
                try? await Task.sleep(for: .seconds(114))
                self?.pending.removeAll { $0.id == p.id }
            }
            return true
        } catch {
            pending.removeAll { $0.id == p.id }
            show("Didn’t send: \(error.localizedDescription)")
            return false
        }
    }

    /// --demo only: a bubble that is never sent anywhere.
    func demoBubble(_ body: String, to key: String) {
        let p = PendingMessage(id: "p:" + UUID().uuidString, key: key, body: body, ts: Date().timeIntervalSince1970, demo: true)
        pending.append(p)
        Task { @MainActor [weak self] in
            try? await Task.sleep(for: .seconds(2.6))
            self?.pending.removeAll { $0.id == p.id }
        }
    }

    /// Swap optimistic bubbles for the server's rows once they exist. Each
    /// server row confirms at most one bubble, and only rows written after the
    /// bubble (so an earlier identical message never swallows a new one).
    private func reconcilePending() {
        guard !pending.isEmpty else { return }
        var keep: [PendingMessage] = []
        var claimed = Set(confirmed.keys)
        for p in pending {
            if p.demo { keep.append(p); continue }
            if p.key.hasPrefix("agent:") {
                let name = String(p.key.dropFirst(6))
                if let m = chats[name]?.first(where: {
                    $0.role == "you" && $0.ts >= p.ts - 5 && !claimed.contains("\(p.key)#\($0.id)")
                        && $0.body.trimmingCharacters(in: .whitespacesAndNewlines) == p.body
                }) {
                    let k = "\(p.key)#\(m.id)"
                    confirmed[k] = p.id
                    claimed.insert(k)
                    continue
                }
            } else if let t = tap(p.key),
                      let i = t.messages.indices.first(where: {
                          let m = t.messages[$0]
                          return m.who == "you" && m.date >= p.ts - 60 && !claimed.contains("\(p.key)#\($0)")
                              && m.body.trimmingCharacters(in: .whitespacesAndNewlines) == p.body
                      }) {
                let k = "\(p.key)#\(i)"
                confirmed[k] = p.id
                claimed.insert(k)
                continue
            }
            keep.append(p)
        }
        if keep != pending { pending = keep }
    }

    func show(_ text: String) { toast = Toast(text: text) }
}

// MARK: - Images (downsampled once, off the main thread)

@MainActor
final class Images {
    static let shared = Images()
    private var cache: [String: NSImage] = [:]
    private var order: [String] = []
    private var tasks: [String: Task<NSImage?, Never>] = [:]
    /// Decoded bitmaps kept around (cards, lightbox, diffs); oldest go first.
    var limit = 160

    func cached(_ path: String, maxPixel: Int) -> NSImage? { cache["\(path)@\(maxPixel)"] }

    func load(_ path: String, maxPixel: Int) async -> NSImage? {
        let key = "\(path)@\(maxPixel)"
        if let hit = cache[key] { return hit }
        if let t = tasks[key] { return await t.value }
        let t = Task<NSImage?, Never> {
            guard let data = try? await API.data(path) else { return nil }
            let cg = await Task.detached(priority: .userInitiated) { downsample(data, maxPixel) }.value
            guard let cg else { return nil }
            return NSImage(cgImage: cg, size: NSSize(width: cg.width, height: cg.height))
        }
        tasks[key] = t
        let img = await t.value
        tasks[key] = nil
        if let img {
            cache[key] = img
            order.append(key)
            if order.count > limit {
                for k in order.prefix(order.count - limit) { cache[k] = nil }
                order.removeFirst(order.count - limit)
            }
        }
        return img
    }
}

nonisolated func downsample(_ data: Data, _ maxPixel: Int) -> CGImage? {
    guard let src = CGImageSourceCreateWithData(data as CFData, nil) else { return nil }
    let opts: [CFString: Any] = [
        kCGImageSourceCreateThumbnailFromImageAlways: true,
        kCGImageSourceThumbnailMaxPixelSize: maxPixel,
        kCGImageSourceCreateThumbnailWithTransform: true,
        kCGImageSourceShouldCacheImmediately: true,
    ]
    return CGImageSourceCreateThumbnailAtIndex(src, 0, opts as CFDictionary)
}
