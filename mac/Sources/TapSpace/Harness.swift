import AppKit

/// Verification entry point (harness lane). Called once from
/// AppDelegate.applicationDidFinishLaunching after the window, the pill and
/// `store.start()` are up.
///
///   --snapshot <dir>  render every scene to a 2x PNG from the real view
///                     hierarchy (HarnessSnapshot), write scenes.json, exit
///   --demo            a scripted ~10 s loop that never stops (HarnessDemo):
///                     tabs, notes liquid, rail selection, a bubble flying in,
///                     the pill bulging, spreading and retracting
///   --fps             App.swift logs frames per second; under --demo the
///                     harness also prints a summary every loop (HarnessFPS)
///
/// Both modes run with `API.allowWrites == false`: nothing here can decide a
/// card, post a chat or comment on a tap.
@MainActor
enum Harness {
    static func run(flags: Flags, app: AppDelegate) {
        if let dir = flags.snapshot {
            API.allowWrites = false
            HarnessSnapshot(app: app, dir: URL(fileURLWithPath: (dir as NSString).expandingTildeInPath)).start()
        } else if flags.demo {
            API.allowWrites = false
            HarnessDemo.shared.start(app: app, fps: flags.fps)
        }
    }

    /// Polls `cond` on the main actor until it holds or `timeout` passes.
    /// Returns whether it held.
    @discardableResult
    static func wait(_ timeout: Double, every ms: Int = 50, _ cond: () -> Bool) async -> Bool {
        let end = CACurrentMediaTime() + timeout
        while !cond() {
            if CACurrentMediaTime() > end { return false }
            try? await Task.sleep(for: .milliseconds(ms))
        }
        return true
    }

    static func log(_ s: String) {
        print(s)
        fflush(stdout)
    }
}
