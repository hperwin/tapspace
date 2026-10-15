import AppKit
import CoreGraphics
import Observation

/// Link frames (harness lane, for the progress column's connector): motion
/// evidence without an on-screen recording. Run with `--snapshot <dir>` and
/// `TAPSPACE_LINK_FRAMES=1`; Harness.run then dispatches here instead of the
/// scene run, so the window is offscreen and nothing activates. It first
/// checks the board's rules on synthetic rows (the package has no test
/// target), then poses the REAL panel against a scratch server and renders
/// the link between two cards forming, chasing, turning around and ending as
/// 2x PNG frames (`<dir>/f000.png`...), 30 per second of motion, plus
/// `frames.json`. Springs step offline on a fixed 1/30 s clock; the chase is
/// scrubbed through model values (cacheDisplay draws models, not running
/// animations). Refuses unless TAPSPACE_URL names a local scratch server;
/// never writes (allowWrites is off).
///
///   TAPSPACE_URL=http://127.0.0.1:4748 TAPSPACE_LINK_FRAMES=1 .build/debug/TapSpace --snapshot <dir>
///   ffmpeg -framerate 30 -i <dir>/f%03d.png -vf "scale=trunc(iw/2)*2:trunc(ih/2)*2" -c:v libx264 -pix_fmt yuv420p <dir>/link.mp4
@MainActor @Observable
final class HarnessLink {
    static let shared = HarnessLink()
    static let asked = ProcessInfo.processInfo.environment["TAPSPACE_LINK_FRAMES"] != nil

    /// Links the panel shows instead of the server's while the drive poses it (nil = live data).
    var links: [AgentLink]?
    /// The chase's phase (0..<1), pinned for offline frames (nil = the running animation).
    var phase: Double?
    /// The progress column's frame in the window (points, top-left origin), reported only while asked.
    @ObservationIgnored var panel: CGRect = .zero
}
