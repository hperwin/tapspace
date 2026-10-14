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
    var windowKey = false
    var toast: Toast?
    /// Bumped when Hayden asks to see a particular card (pill row click).
    var focusCard: String?

    var tab: Tab = .visual {
        didSet {
            guard tab != oldValue else { return }
            UserDefaults.standard.set(tab.rawValue, forKey: "tab")
            tabProgress.set(tab == .chat ? 1 : 0, SpringPreset(stiffness: 220, damping: 26))
            if tab == .chat { markRead(thread) }
        }
    }

    var thread: String = "" {
        didSet {
            guard thread != oldValue else { return }
            UserDefaults.standard.set(thread, forKey: "thread")
            if !thread.hasPrefix("agent:"), !thread.isEmpty, !refreshedTaps.contains(thread) {
                refreshedTaps.insert(thread)
                Task { try? await API.post("/api/taps/refresh", [:], speaksForHayden: false) }
            }
            Task { await pollChat() }
        }
    }

    @ObservationIgnored let tabProgress = SpringBox(0, SpringPreset(stiffness: 220, damping: 26))
    @ObservationIgnored var onTickets: (([Ticket]) -> Void)?
    /// Fired after any poll lands, so the pill wakes its display link.
    @ObservationIgnored var onChange: (() -> Void)?
    @ObservationIgnored private var failures = 0
    @ObservationIgnored private var booted = false
    @ObservationIgnored private var refreshedTaps: Set<String> = []
    @ObservationIgnored private var started = false

    init() {
        let d = UserDefaults.standard
        if let t = d.string(forKey: "tab").flatMap(Tab.init(rawValue:)) { tab = t }
        thread = d.string(forKey: "thread") ?? ""
        tabProgress.snap(tab == .chat ? 1 : 0)
        if let saved = d.dictionary(forKey: "reads") as? [String: Double] { reads = saved }
    }

    // MARK: Polling

    func start() {
        guard !started else { return }
        started = true
        loop(1.5) { await $0.pollState() }
        loop(2.0) { await $0.pollAgents() }
        loop(1.5) { await $0.pollChat() }
        loop(5.0) { await $0.pollTaps() }
        loop(2.0) { await $0.pollActivity() }
    }

    private func loop(_ every: Double, _ body: @escaping @MainActor (Store) async -> Void) {
        Task { @MainActor [weak self] in
            while let self {
                await body(self)
                try? await Task.sleep(for: .seconds(every))
            }
        }
    }

    func pollState() async {
        do {
            let t = try await API.state()
            failures = 0
            if connection != .live { connection = .live }
            if !ticketsLoaded { ticketsLoaded = true }
            if t != tickets {
                tickets = t
                // Forget local decisions the server has caught up with.
                decided = decided.filter { key in t.contains { $0.versionKey == key && $0.status == "waiting" } }
            }
            onTickets?(t)
        } catch {
            failures += 1
            if failures >= 2 {
                connection = ticketsLoaded ? .down : .connecting
                bootServerIfNeeded()
            }
        }
        onChange?()
    }

    func pollAgents() async {
        guard let a = try? await API.agents() else { return }
        agentsLoaded = true
        if a != agents { agents = a }
        if thread.isEmpty || threadMissing { pickFallbackThread() }
        onChange?()
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
        if t != taps { taps = t }
        reconcilePending()
        if thread.isEmpty || threadMissing { pickFallbackThread() }
        onChange?()
    }

    func pollActivity() async {
        guard let a = try? await API.activity() else { return }
        if a.rows != activity { activity = a.rows }
        if a.sessions != sessions { sessions = a.sessions }
    }

    /// The server is down at launch: start it the way agents do (tap.py up).
    private func bootServerIfNeeded() {
        guard !booted, API.isLocal else { return }
        booted = true
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        let candidates = ["\(home)/orchestration/tapspace/tap.py", "\(home)/Developer/tapspace/tap.py"]
        guard let tap = candidates.first(where: { FileManager.default.fileExists(atPath: $0) }) else { return }
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/bin/env")
        p.arguments = ["python3", tap, "up"]
        var env = ProcessInfo.processInfo.environment
        env["TAPSPACE_URL"] = API.base.absoluteString
        if let port = API.base.port, port != 4747 { env["TAPSPACE_PORT"] = String(port) }
        p.environment = env
        p.standardOutput = FileHandle.nullDevice
        p.standardError = FileHandle.nullDevice
        try? p.run()
    }

    // MARK: Threads

    var threadMissing: Bool {
        if thread.hasPrefix("agent:") { return !agents.contains { $0.key == thread } }
        return !taps.items.contains { $0.key == thread } && taps.fetched > 0
    }

    private func pickFallbackThread() {
        let fall = agents.first?.key ?? taps.items.first?.key ?? ""
        if !fall.isEmpty, fall != thread, agentsLoaded { thread = fall }
    }

    var threadKeys: [String] { agents.map(\.key) + taps.items.map(\.key) }

    func agent(_ key: String) -> Agent? { agents.first { $0.key == key } }
    func tap(_ key: String) -> Tap? { taps.items.first { $0.key == key } }

    // MARK: Read state (persisted per thread, like the web page's localStorage)

    func readAt(_ key: String) -> Double { reads[key] ?? 0 }

    func markRead(_ key: String) {
        guard !key.isEmpty else { return }
        let now = Date().timeIntervalSince1970
        guard lastTheirs(key) > readAt(key) || reads[key] == nil else { return }
        reads[key] = now
        UserDefaults.standard.set(reads, forKey: "reads")
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

    func decide(_ t: Ticket, approve: Bool, message: String = "") async -> Bool {
        do {
            try await API.post("/api/decide", ["id": t.id, "action": approve ? "approve" : "change", "message": message])
            decided.insert(t.versionKey)
            onChange?()
            return true
        } catch {
            show("Didn’t reach TapSpace: \(error.localizedDescription)")
            return false
        }
    }

    /// Returns false (and restores nothing itself) when the send failed.
    func send(_ body: String, to key: String) async -> Bool {
        let p = PendingMessage(id: "p:" + UUID().uuidString, key: key, body: body, ts: Date().timeIntervalSince1970)
        pending.append(p)
        if key.hasPrefix("agent:") {
            if let i = agents.firstIndex(where: { $0.key == key }) { agents[i].state = "thinking" }
            do {
                try await API.post("/api/chat", ["agent": String(key.dropFirst(6)), "body": body])
                await pollChat()
                await pollAgents()
                return true
            } catch {
                pending.removeAll { $0.id == p.id }
                show(error.localizedDescription.isEmpty ? "Didn’t send. Is TapSpace still running?" : "Didn’t send: \(error.localizedDescription)")
                return false
            }
        }
        do {
            try await API.post("/api/taps/say", ["key": key, "body": body])
            // Keep the bubble until GitHub shows the comment (or two minutes pass).
            Task { @MainActor [weak self] in
                for delay in [2.5, 6.0] {
                    try? await Task.sleep(for: .seconds(delay))
                    await self?.pollTaps()
                }
                try? await Task.sleep(for: .seconds(120))
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

    private func reconcilePending() {
        guard !pending.isEmpty else { return }
        var keep: [PendingMessage] = []
        for p in pending {
            if p.demo { keep.append(p); continue }
            if p.key.hasPrefix("agent:") {
                let name = String(p.key.dropFirst(6))
                if let m = chats[name]?.last(where: { $0.role == "you" && $0.body.trimmingCharacters(in: .whitespacesAndNewlines) == p.body && $0.ts >= p.ts - 5 }) {
                    confirmed["\(p.key)#\(m.id)"] = p.id
                    continue
                }
            } else if let t = tap(p.key),
                      let i = t.messages.lastIndex(where: { $0.who == "you" && $0.body.trimmingCharacters(in: .whitespacesAndNewlines) == p.body }) {
                confirmed["\(p.key)#\(i)"] = p.id
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
    private var tasks: [String: Task<NSImage?, Never>] = [:]

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
        if let img { cache[key] = img }
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
