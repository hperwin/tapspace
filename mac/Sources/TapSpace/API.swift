import Foundation

// MARK: - Wire models (exactly what server.py serves)
//
// Every type keeps its memberwise init (decoders live in extensions), and every
// list decodes LOSSY: one malformed row is skipped instead of failing the whole
// poll, so a bad ticket on disk never reads as "the server is down".

struct TicketVersion: Decodable, Equatable {
    let v: Int
    let note: String?
    let ts: Double
    let media: String?
    let ext: String?
    let video_deleted: Bool?

    var isVideo: Bool { media == "video" }
}

struct Ticket: Decodable, Equatable, Identifiable {
    let id: String
    let agent: String
    let title: String
    let campaign: String?
    let version: Int
    let versions: [TicketVersion]
    var status: String
    /// The master agent (NAME) that owns this card, when the server knows it.
    /// A hidden key: views show that agent's title, never this.
    var owner: String? = nil

    func entry(_ v: Int) -> TicketVersion? { versions.first { $0.v == v } }
    var current: TicketVersion? { entry(version) }
    var isVideo: Bool { current?.isVideo ?? false }
    var videoDeleted: Bool { isVideo && (current?.video_deleted ?? false) }
    var lastTs: Double { versions.last?.ts ?? 0 }

    func mediaPath(_ v: Int) -> String {
        let e = entry(v)
        return "/shots/\(id)/\(v).\(e?.ext ?? "png")"
    }
    /// "id#version": a decision is about one version, never the next.
    var versionKey: String { "\(id)#\(version)" }
}

private struct StateResponse: Decodable { let tickets: Lossy<Ticket> }

struct Agent: Decodable, Equatable, Identifiable {
    let name: String
    var state: String
    let seen: Double
    let first: Double?
    let `where`: String?
    let last: Double
    let count: Int
    let last_master: Double?
    /// what Hayden sees: the plain name the agent gave its work
    let title: String
    /// The north star and steps this master published (tap.py goal/step); nil until it sets one.
    var progress: AgentProgress? = nil
    /// Change notes Hayden sent on this master's cards that it has not answered
    /// with a new version yet, newer than the server read mark. Newest first.
    var notes: [AgentNote] = []
    /// The server's read mark for those notes (epoch), nil when never read.
    var notesRead: Double? = nil
    /// "lead" on Muddi's row (the master over all masters); nil on every other row.
    var role: String? = nil
    /// NAME of the lead this master reports to (server-derived). A hidden key: never shown.
    var parent: String? = nil

    var id: String { name }
    var key: String { "agent:" + name }
    var isLead: Bool { role == "lead" }
}

/// A master's published plan: one goal and its steps.
struct AgentProgress: Decodable, Equatable {
    var goal = ""
    var steps: [String] = []
    /// 0-based; == steps.count means every step is done.
    var current = 0
    var note: String? = nil
    var updated: Double = 0

    var isDone: Bool { !steps.isEmpty && current >= steps.count }
    /// The step it is on now (nil when done or out of range).
    var currentText: String? { steps.indices.contains(current) ? steps[current] : nil }
}

/// One change note Hayden sent on a card this master owns.
struct AgentNote: Decodable, Equatable, Identifiable {
    let ticket: String
    let version: Int
    /// the card's title
    let title: String
    /// Hayden's note, as he wrote it
    let note: String
    /// when he sent it (server epoch)
    let ts: Double
    /// that version's image path (/shots/...), "" for a video or none: show the title
    let thumb: String

    var id: String { "\(ticket)#\(version)" }
}

/// Two masters messaging each other (server-detected). `a < b`; names are hidden keys.
struct AgentLink: Decodable, Equatable, Identifiable {
    let a: String
    let b: String
    /// the latest message between them (epoch)
    let last: Double
    let count: Int
    /// plain words from the latest message (no paths, ids or numbers)
    let summary: String
    /// the latest message is under 10 minutes old
    let active: Bool
    /// who sent the latest message (a or b)
    let from: String

    var id: String { a + "|" + b }
    /// who received the latest message
    var to: String { from == a ? b : a }
    func touches(_ name: String) -> Bool { a == name || b == name }
    func other(_ name: String) -> String { a == name ? b : a }
}

/// GET /api/agents: the rail's rows plus who is talking to whom
/// (`links` is absent on older servers and decodes as []).
struct AgentsBoard: Decodable, Equatable {
    var agents: [Agent] = []
    var links: [AgentLink] = []
}

struct ChatMessage: Decodable, Equatable, Identifiable {
    let id: Int
    let agent: String
    let role: String
    let body: String
    let ts: Double
    /// Attached image paths (`/chat-media/<file>`), [] when none.
    var images: [String] = []
}

private struct ChatResponse: Decodable { let messages: Lossy<ChatMessage> }
private struct ChatPostResponse: Decodable { let message: ChatMessage? }

struct TapGoal: Decodable, Equatable {
    let text: String
    let done: Bool
}

struct TapMessage: Decodable, Equatable {
    let who: String
    let body: String
    let ts: String
    let seen: Bool?
    let url: String?

    var date: Double { parseISO(ts) }
}

/// What a holding tap asks Hayden: the newest non-hperwin comment that mentions him. Plain text.
struct TapAsk: Decodable, Equatable {
    let text: String
    let url: String
    let at: String

    /// 0 when `at` is missing or unparseable.
    var date: Double { parseISO(at) }
}

/// One proof image from the issue body, served by TapSpace's proxy, never by GitHub.
struct TapProof: Decodable, Equatable {
    let url: String
    let alt: String
}

struct Tap: Decodable, Equatable, Identifiable {
    let key: String
    let repo: String
    let number: Int
    let title: String
    let url: String
    let status: String
    let goals: [TapGoal]
    let messages: [TapMessage]
    let created: String
    let closed: String?
    let last: String
    /// the pilot is working on a reply right now
    let replying: Bool?
    /// proposed | enroute | holding | landed | done | closed; nil from a server older than Muddi.
    var phase: String? = nil
    /// The one word the server says to show (phase_word); nil from an older server.
    var phaseWord: String? = nil
    /// Set only while holding (null otherwise).
    var ask: TapAsk? = nil
    /// The fly label is on: a proposed tap waiting to fly.
    var flyRequested = false
    var proof: [TapProof] = []

    var id: String { key }
    /// "[tap] Fix the thing" -> "Fix the thing"
    var name: String {
        title.replacingOccurrences(of: #"^\[[^\]]*\]\s*"#, with: "", options: .regularExpression)
    }
    var repoName: String { repo.split(separator: "/").last.map(String.init) ?? repo }
}

struct TapsResponse: Decodable, Equatable {
    var me: String = ""
    var items: [Tap] = []
    var fetched: Double = 0
    var error: String = ""
}

struct ActivityRow: Decodable, Equatable {
    let campaign: String
    let agent: String
    let verb: String
    let detail: String
    let kind: String
    let age: Double
    let state: String

    var key: String { campaign + "|" + agent }
}

struct Sessions: Decodable, Equatable {
    var working: Int = 0
    var recent: Int = 0
}

struct ActivityResponse: Decodable {
    let rows: [ActivityRow]
    let sessions: Sessions
}

// MARK: - Tolerant decoding

/// Decodes an array, skipping elements that fail instead of failing the array.
struct Lossy<T: Decodable>: Decodable {
    let items: [T]
    init(from decoder: Decoder) throws {
        var c = try decoder.unkeyedContainer()
        var out: [T] = []
        if let n = c.count { out.reserveCapacity(n) }
        while !c.isAtEnd {
            if let v = try? c.decode(T.self) { out.append(v) } else { _ = try? c.decode(SkipValue.self) }
        }
        items = out
    }
}

/// Consumes one JSON value of any shape (lets Lossy step past a bad element).
private struct SkipValue: Decodable { init(from decoder: Decoder) throws {} }

private extension KeyedDecodingContainer {
    func or<T: Decodable>(_ key: Key, _ fallback: T) -> T { ((try? decodeIfPresent(T.self, forKey: key)) ?? nil) ?? fallback }
    func opt<T: Decodable>(_ key: Key) -> T? { (try? decodeIfPresent(T.self, forKey: key)) ?? nil }
    func lossy<T: Decodable>(_ key: Key) -> [T] { ((try? decodeIfPresent(Lossy<T>.self, forKey: key)) ?? nil)?.items ?? [] }
}

private struct Keys: CodingKey {
    let stringValue: String
    var intValue: Int? { nil }
    init(stringValue s: String) { stringValue = s }
    init?(intValue: Int) { nil }
    init(_ s: String) { stringValue = s }
}

extension TicketVersion {
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: Keys.self)
        v = try c.decode(Int.self, forKey: Keys("v"))
        note = c.opt(Keys("note"))
        ts = c.or(Keys("ts"), 0)
        media = c.opt(Keys("media"))
        ext = c.opt(Keys("ext"))
        video_deleted = c.opt(Keys("video_deleted"))
    }
}

extension Ticket {
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: Keys.self)
        id = try c.decode(String.self, forKey: Keys("id"))
        agent = c.or(Keys("agent"), "agent")
        title = c.or(Keys("title"), "")
        campaign = c.opt(Keys("campaign"))
        version = try c.decode(Int.self, forKey: Keys("version"))
        versions = c.lossy(Keys("versions"))
        status = c.or(Keys("status"), "waiting")
        owner = c.opt(Keys("owner"))
    }
}

extension Agent {
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: Keys.self)
        name = try c.decode(String.self, forKey: Keys("name"))
        state = c.or(Keys("state"), "away")
        seen = c.or(Keys("seen"), 0)
        first = c.opt(Keys("first"))
        `where` = c.opt(Keys("where"))
        last = c.or(Keys("last"), 0)
        count = c.or(Keys("count"), 0)
        last_master = c.opt(Keys("last_master"))
        title = c.or(Keys("title"), "Claude")
        progress = c.opt(Keys("progress"))
        notes = c.lossy(Keys("notes"))
        notesRead = c.opt(Keys("notes_read"))
        role = c.opt(Keys("role"))
        parent = (c.opt(Keys("parent")) as String?).flatMap { $0.isEmpty ? nil : $0 }
    }
}

extension AgentProgress {
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: Keys.self)
        goal = c.or(Keys("goal"), "")
        steps = c.lossy(Keys("steps"))
        current = c.or(Keys("current"), 0)
        note = c.opt(Keys("note"))
        updated = c.or(Keys("updated"), 0)
    }
}

extension AgentNote {
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: Keys.self)
        ticket = try c.decode(String.self, forKey: Keys("ticket"))
        version = c.or(Keys("version"), 0)
        title = c.or(Keys("title"), "")
        note = c.or(Keys("note"), "")
        ts = c.or(Keys("ts"), 0)
        thumb = c.or(Keys("thumb"), "")
    }
}

extension AgentLink {
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: Keys.self)
        a = try c.decode(String.self, forKey: Keys("a"))
        b = try c.decode(String.self, forKey: Keys("b"))
        last = c.or(Keys("last"), 0)
        count = c.or(Keys("count"), 0)
        summary = c.or(Keys("summary"), "")
        active = c.or(Keys("active"), false)
        from = c.or(Keys("from"), a)
    }
}

extension AgentsBoard {
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: Keys.self)
        agents = try c.decode(Lossy<Agent>.self, forKey: Keys("agents")).items
        links = c.lossy(Keys("links"))
    }
}

extension ChatMessage {
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: Keys.self)
        id = try c.decode(Int.self, forKey: Keys("id"))
        agent = c.or(Keys("agent"), "")
        role = c.or(Keys("role"), "master")
        body = c.or(Keys("body"), "")
        ts = c.or(Keys("ts"), 0)
        images = c.lossy(Keys("images"))
    }
}

extension TapMessage {
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: Keys.self)
        who = c.or(Keys("who"), "tap")
        body = c.or(Keys("body"), "")
        ts = c.or(Keys("ts"), "")
        seen = c.opt(Keys("seen"))
        url = c.opt(Keys("url"))
    }
}

extension TapAsk {
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: Keys.self)
        text = c.or(Keys("text"), "")
        url = c.or(Keys("url"), "")
        at = c.or(Keys("at"), "")
    }
}

extension TapProof {
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: Keys.self)
        url = try c.decode(String.self, forKey: Keys("url"))
        alt = c.or(Keys("alt"), "")
    }
}

extension Tap {
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: Keys.self)
        key = try c.decode(String.self, forKey: Keys("key"))
        repo = c.or(Keys("repo"), "")
        number = c.or(Keys("number"), 0)
        title = c.or(Keys("title"), "")
        url = c.or(Keys("url"), "")
        status = c.or(Keys("status"), "boarding")
        goals = c.lossy(Keys("goals"))
        messages = c.lossy(Keys("messages"))
        created = c.or(Keys("created"), "")
        closed = c.opt(Keys("closed"))
        last = c.or(Keys("last"), created)
        replying = c.opt(Keys("replying"))
        phase = (c.opt(Keys("phase")) as String?).flatMap { $0.isEmpty ? nil : $0 }
        phaseWord = (c.opt(Keys("phase_word")) as String?).flatMap { $0.isEmpty ? nil : $0 }
        ask = c.opt(Keys("ask"))
        flyRequested = c.or(Keys("fly_requested"), false)
        proof = c.lossy(Keys("proof"))
    }
}

extension TapsResponse {
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: Keys.self)
        me = c.or(Keys("me"), "")
        items = c.lossy(Keys("items"))
        fetched = c.or(Keys("fetched"), 0)
        error = c.or(Keys("error"), "")
    }
}

extension ActivityResponse {
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: Keys.self)
        rows = c.lossy(Keys("rows"))
        sessions = c.or(Keys("sessions"), Sessions())
    }
}

extension Sessions {
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: Keys.self)
        working = c.or(Keys("working"), 0)
        recent = c.or(Keys("recent"), 0)
    }
}

// MARK: - Client

enum APIError: LocalizedError {
    case http(Int, String)
    case writesDisabled
    /// The server answered, but not in a shape we can read (it is up).
    case decode(String)

    var errorDescription: String? {
        switch self {
        case .http(_, let msg): return msg
        case .writesDisabled: return "Sending is off in this mode"
        case .decode(let what): return "Couldn’t read \(what)"
        }
    }

    /// True when the request never reached a live server.
    static func isOffline(_ e: Error) -> Bool {
        if e is URLError { return true }
        if case APIError.http(let code, _) = e { return code == 0 || code >= 500 }
        return false
    }
}

enum API {
    nonisolated(unsafe) static var base: URL = {
        let raw = ProcessInfo.processInfo.environment["TAPSPACE_URL"] ?? "http://127.0.0.1:4747"
        return URL(string: raw.hasSuffix("/") ? String(raw.dropLast()) : raw) ?? URL(string: "http://127.0.0.1:4747")!
    }()

    /// Verification modes (--demo, --snapshot) never speak for Hayden.
    nonisolated(unsafe) static var allowWrites = true

    static let session: URLSession = {
        let c = URLSessionConfiguration.ephemeral
        c.requestCachePolicy = .reloadIgnoringLocalAndRemoteCacheData
        c.urlCache = nil
        c.httpCookieStorage = nil
        c.timeoutIntervalForRequest = 10
        c.httpMaximumConnectionsPerHost = 6
        c.waitsForConnectivity = false
        return URLSession(configuration: c)
    }()

    static var isLocal: Bool { ["127.0.0.1", "localhost"].contains(base.host ?? "") }

    static func url(_ path: String, _ q: [String: String] = [:]) -> URL {
        var c = URLComponents(url: base, resolvingAgainstBaseURL: false)!
        // A served path may carry its own query (a tap's proof image: /api/taps/proof?key=...&i=0).
        let parts = path.split(separator: "?", maxSplits: 1, omittingEmptySubsequences: false)
        c.path = String(parts[0])
        var items: [URLQueryItem] = []
        if parts.count > 1 {
            let raw = String(parts[1]).replacingOccurrences(of: "#", with: "%23")
            items = URLComponents(string: "?" + raw)?.queryItems ?? []
        }
        items += q.sorted { $0.key < $1.key }.map { URLQueryItem(name: $0.key, value: $0.value) }
        if !items.isEmpty { c.queryItems = items }
        return c.url!
    }

    static func get<T: Decodable>(_ type: T.Type, _ path: String, _ q: [String: String] = [:]) async throws -> T {
        let data = try await fetch(url(path, q))
        do { return try JSONDecoder().decode(T.self, from: data) }
        catch { throw APIError.decode(path) }
    }

    static func data(_ path: String) async throws -> Data { try await fetch(url(path)) }

    private static func fetch(_ u: URL) async throws -> Data {
        var req = URLRequest(url: u)
        req.setValue("application/json", forHTTPHeaderField: "accept")
        let (data, resp) = try await session.data(for: req)
        let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
        guard code == 200 else { throw APIError.http(code, "HTTP \(code)") }
        return data
    }

    /// JSON POST. Always content-type application/json and never an Origin
    /// header (URLSession sends none): the server only accepts same-origin
    /// JSON for the endpoints that speak for the reviewer.
    @discardableResult
    static func post(_ path: String, _ body: [String: Any], speaksForHayden: Bool = true) async throws -> [String: Any] {
        let data = try await postData(path, body, speaksForHayden: speaksForHayden)
        return (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] ?? [:]
    }

    /// The raw body of a successful POST (for typed replies like /api/chat's message row).
    static func postData(_ path: String, _ body: [String: Any], speaksForHayden: Bool = true) async throws -> Data {
        if speaksForHayden && !allowWrites { throw APIError.writesDisabled }
        var req = URLRequest(url: url(path))
        req.httpMethod = "POST"
        req.timeoutInterval = 60
        req.setValue("application/json", forHTTPHeaderField: "content-type")
        req.setValue(nil, forHTTPHeaderField: "Origin")
        req.httpBody = try JSONSerialization.data(withJSONObject: body)
        let (data, resp) = try await session.data(for: req)
        let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
        let obj = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] ?? [:]
        guard code == 200, obj["ok"] as? Bool == true else {
            throw APIError.http(code, (obj["error"] as? String) ?? "HTTP \(code)")
        }
        return data
    }

    // Typed reads
    static func state() async throws -> [Ticket] { try await get(StateResponse.self, "/api/state").tickets.items }
    static func board() async throws -> AgentsBoard { try await get(AgentsBoard.self, "/api/agents") }
    static func agents() async throws -> [Agent] { try await board().agents }
    static func chat(_ agent: String) async throws -> [ChatMessage] {
        try await get(ChatResponse.self, "/api/chat", ["agent": agent]).messages.items
    }
    static func taps() async throws -> TapsResponse { try await get(TapsResponse.self, "/api/taps") }
    static func activity() async throws -> ActivityResponse { try await get(ActivityResponse.self, "/api/activity") }

    // Typed writes
    /// POST /api/chat: Hayden says something to an agent. Returns the stored row.
    @discardableResult
    static func say(_ body: String, toAgent name: String) async throws -> ChatMessage? {
        let data = try await postData("/api/chat", ["agent": name, "body": body])
        return (try? JSONDecoder().decode(ChatPostResponse.self, from: data))?.message
    }

    /// POST /api/agents/new: start a master agent in the home folder (it picks its
    /// project itself). Returns its name.
    static func newAgent() async throws -> String {
        try await post("/api/agents/new", ["folder": "~"])["agent"] as? String ?? ""
    }

    /// POST /api/archive {all}: every card leaves the space (kept in the archive).
    static func archiveAll() async throws {
        try await post("/api/archive", ["all": true])
    }

    /// POST /api/agents/read {name}: Hayden opened this master's thread, so its
    /// card notes are read on every surface (web, Mac, phone). Speaks for him:
    /// refused under --snapshot/--demo.
    static func markAgentRead(_ name: String) async throws {
        try await post("/api/agents/read", ["name": name])
    }

    /// POST /api/taps/fly {key}: Hayden clears a proposed tap to fly (the server adds the fly label as him).
    /// Speaks for Hayden: refused under --snapshot/--demo.
    static func flyTap(_ key: String) async throws {
        try await post("/api/taps/fly", ["key": key])
    }

    /// POST /api/taps/accept {key}: Hayden's sign-off on a landed tap (the server closes it as completed).
    /// Speaks for Hayden: refused under --snapshot/--demo. Only his click calls it, never an agent.
    static func acceptTap(_ key: String) async throws {
        try await post("/api/taps/accept", ["key": key])
    }
}

// MARK: - Time

private let isoFull: ISO8601DateFormatter = {
    let f = ISO8601DateFormatter()
    f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
    return f
}()
private let isoPlain = ISO8601DateFormatter()
private let isoLock = NSLock()
nonisolated(unsafe) private var isoCache: [String: Double] = [:]

/// GitHub ISO stamps to epoch seconds (memoized: views re-read them every render).
func parseISO(_ s: String) -> Double {
    isoLock.lock(); defer { isoLock.unlock() }
    if let hit = isoCache[s] { return hit }
    let v = (isoPlain.date(from: s) ?? isoFull.date(from: s))?.timeIntervalSince1970 ?? 0
    if isoCache.count > 4096 { isoCache.removeAll(keepingCapacity: true) }
    isoCache[s] = v
    return v
}

private let shortDate: DateFormatter = {
    let f = DateFormatter()
    f.dateFormat = "MMM d"
    f.timeZone = TimeZone(identifier: "America/Los_Angeles")
    return f
}()

/// Relative stamps the way the web page writes them: now, 4m, 3h, Oct 9.
func when(_ ts: Double, now: Double = Date().timeIntervalSince1970) -> String {
    let s = max(0, now - ts)
    if s < 50 { return "now" }
    if s < 3600 { return "\(Int((s / 60).rounded()))m" }
    if s < 86400 { return "\(Int((s / 3600).rounded()))h" }
    return shortDate.string(from: Date(timeIntervalSince1970: ts))
}

/// Card ages: now under 90s, then minutes, then hours.
func timeago(_ ts: Double, now: Double = Date().timeIntervalSince1970) -> String {
    let s = max(0, now - ts)
    if s < 90 { return "now" }
    if s < 3600 { return "\(Int((s / 60).rounded()))m" }
    return "\(Int((s / 3600).rounded()))h"
}
