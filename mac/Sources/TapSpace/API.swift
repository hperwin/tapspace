import Foundation

// MARK: - Wire models (exactly what server.py serves)

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

private struct StateResponse: Decodable { let tickets: [Ticket] }

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

private struct AgentsResponse: Decodable { let agents: [Agent] }

struct ChatMessage: Decodable, Equatable, Identifiable {
    let id: Int
    let agent: String
    let role: String
    let body: String
    let ts: Double
}

private struct ChatResponse: Decodable { let messages: [ChatMessage] }

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

// MARK: - Client

enum APIError: LocalizedError {
    case http(Int, String)
    case writesDisabled

    var errorDescription: String? {
        switch self {
        case .http(_, let msg): return msg
        case .writesDisabled: return "Sending is off in this mode"
        }
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
        c.timeoutIntervalForRequest = 10
        c.httpMaximumConnectionsPerHost = 6
        return URLSession(configuration: c)
    }()

    static var isLocal: Bool { ["127.0.0.1", "localhost"].contains(base.host ?? "") }

    static func url(_ path: String, _ q: [String: String] = [:]) -> URL {
        var c = URLComponents(url: base, resolvingAgainstBaseURL: false)!
        c.path = path
        if !q.isEmpty { c.queryItems = q.map { URLQueryItem(name: $0.key, value: $0.value) } }
        return c.url!
    }

    static func get<T: Decodable>(_ type: T.Type, _ path: String, _ q: [String: String] = [:]) async throws -> T {
        let (data, resp) = try await session.data(from: url(path, q))
        let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
        guard code == 200 else { throw APIError.http(code, "HTTP \(code)") }
        return try JSONDecoder().decode(T.self, from: data)
    }

    static func data(_ path: String) async throws -> Data {
        let (data, resp) = try await session.data(from: url(path))
        let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
        guard code == 200 else { throw APIError.http(code, "HTTP \(code)") }
        return data
    }

    /// JSON POST. Always content-type application/json and never an Origin
    /// header: the server only accepts same-origin JSON for the endpoints
    /// that speak for the reviewer.
    @discardableResult
    static func post(_ path: String, _ body: [String: Any], speaksForHayden: Bool = true) async throws -> [String: Any] {
        if speaksForHayden && !allowWrites { throw APIError.writesDisabled }
        var req = URLRequest(url: url(path))
        req.httpMethod = "POST"
        req.timeoutInterval = 60
        req.setValue("application/json", forHTTPHeaderField: "content-type")
        req.httpBody = try JSONSerialization.data(withJSONObject: body)
        let (data, resp) = try await session.data(for: req)
        let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
        let obj = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] ?? [:]
        guard code == 200, obj["ok"] as? Bool == true else {
            throw APIError.http(code, (obj["error"] as? String) ?? "HTTP \(code)")
        }
        return obj
    }

    // Typed reads
    static func state() async throws -> [Ticket] { try await get(StateResponse.self, "/api/state").tickets }
    static func agents() async throws -> [Agent] { try await get(AgentsResponse.self, "/api/agents").agents }
    static func chat(_ agent: String) async throws -> [ChatMessage] {
        try await get(ChatResponse.self, "/api/chat", ["agent": agent]).messages
    }
    static func taps() async throws -> TapsResponse { try await get(TapsResponse.self, "/api/taps") }
    static func activity() async throws -> ActivityResponse { try await get(ActivityResponse.self, "/api/activity") }
}

// MARK: - Time

private let isoFull: ISO8601DateFormatter = {
    let f = ISO8601DateFormatter()
    f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
    return f
}()
private let isoPlain = ISO8601DateFormatter()

func parseISO(_ s: String) -> Double {
    (isoPlain.date(from: s) ?? isoFull.date(from: s))?.timeIntervalSince1970 ?? 0
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
