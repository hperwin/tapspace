import Foundation

/// Which TapSpace server the app may start when its base URL is down, and how.
/// The data directory decides it, never the port alone:
/// - the default live URL with no TAPSPACE_DATA: `tap.py up`, Hayden's shared rig
///   (it serves the live data and outlives the app, like any agent's `tap.py up`);
/// - TAPSPACE_DATA set: a server on that data dir and the base's port, owned by the
///   app (it exits with the app, even on a crash or kill);
/// - anything else (a scratch port with no data dir named): nothing. Starting a server
///   there would serve the LIVE data dir on a port meant to be isolated.
enum CoreServer {
    enum Plan: Equatable {
        case shared(tap: String, env: [String: String])
        case owned(server: String, env: [String: String])
    }

    static let livePort = 4747

    static func plan(base: URL = API.base,
                     env: [String: String] = ProcessInfo.processInfo.environment,
                     exists: (String) -> Bool = { FileManager.default.fileExists(atPath: $0) }) -> Plan? {
        guard ["127.0.0.1", "localhost"].contains(base.host ?? ""), base.scheme == "http", let port = base.port else { return nil }
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        let roots = ["\(home)/orchestration/tapspace", "\(home)/Developer/tapspace"]
        var e = env
        e["TAPSPACE_URL"] = base.absoluteString
        e["TAPSPACE_PORT"] = String(port)
        if let data = env["TAPSPACE_DATA"], !data.isEmpty {
            guard let root = roots.first(where: { exists("\($0)/server.py") }) else { return nil }
            return .owned(server: "\(root)/server.py", env: e)
        }
        guard port == livePort, let root = roots.first(where: { exists("\($0)/tap.py") }) else { return nil }
        e["TAPSPACE_DATA"] = nil
        return .shared(tap: "\(root)/tap.py", env: e)
    }

    /// True when a down server at `API.base` gets started by the app.
    static var canStart: Bool { plan() != nil }

    /// The owned server's supervisor, kept while it runs. Its stdin is a pipe only
    /// this process holds: when the app exits for any reason the pipe closes and the
    /// supervisor stops the server.
    nonisolated(unsafe) private static var owned: (process: Process, life: Pipe)?

    /// Start the server per `plan()`. Returns false when there is nothing to start
    /// (or the owned one is still running).
    @discardableResult
    static func start() -> Bool {
        guard let plan = plan() else { return false }
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/bin/env")
        p.standardOutput = FileHandle.nullDevice
        p.standardError = FileHandle.nullDevice
        switch plan {
        case .shared(let tap, let env):
            p.arguments = ["python3", tap, "up"]
            p.environment = env
            p.standardInput = FileHandle.nullDevice
        case .owned(let server, let env):
            if let o = owned, o.process.isRunning { return false }
            let life = Pipe()
            p.arguments = ["python3", "-c", supervisor, server]
            p.environment = env
            p.standardInput = life
            owned = (p, life)
        }
        do { try p.run() } catch { return false }
        return true
    }

    /// Runs the server as a child and stops it when stdin closes (the app is gone).
    private static let supervisor = """
    import subprocess, sys, threading
    p = subprocess.Popen([sys.executable] + sys.argv[1:], stdin=subprocess.DEVNULL)
    def watch():
        sys.stdin.buffer.read()
        p.terminate()
    threading.Thread(target=watch, daemon=True).start()
    sys.exit(p.wait())
    """
}
