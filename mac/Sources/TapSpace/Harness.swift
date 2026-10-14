import AppKit

/// Verification entry point (harness lane). Called once from
/// AppDelegate.applicationDidFinishLaunching after the window, pill and
/// store are up. Stub: the harness lane fills in --snapshot and --demo.
@MainActor
enum Harness {
    static func run(flags: Flags, app: AppDelegate) {
        if flags.demo { /* harness lane: scripted ~10 s demo loop */ }
        if flags.snapshot != nil { /* harness lane: render scenes to PNG, then exit */ }
    }
}
