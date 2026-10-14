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

    var id: String { name }
    var key: String { "agent:" + name }
}

private struct AgentsResponse: Decodable { let agents: Lossy<Agent> }

struct ChatMessage: Decodable, Equatable, Identifiable {
    let id: Int
    let agent: String
    let role: String
    let body: String
    let ts: Double
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
        c.path = path
        if !q.isEmpty { c.queryItems = q.sorted { $0.key < $1.key }.map { URLQueryItem(name: $0.key, value: $0.value) } }
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
    static func agents() async throws -> [Agent] { try await get(AgentsResponse.self, "/api/agents").agents.items }
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
