import AppKit
import Observation
import SwiftUI

/// A newer TapSpace installed under the running one. install.sh never quits a
/// running copy, so the new build waits on disk until the next launch; this
/// notices it and the top bar offers "Update ready". Only that click restarts
/// TapSpace.
@MainActor @Observable
final class ShellUpdate {
    static let shared = ShellUpdate()

    /// The executable this process was launched from has been replaced.
    private(set) var ready = false
    @ObservationIgnored private var relaunching = false
    @ObservationIgnored private let exe = Bundle.main.executableURL?.path
    @ObservationIgnored private var launched: Stamp?
    @ObservationIgnored private lazy var poller = CorePoller("update", every: 5) { [unowned self] in self.check() }

    /// An install renames a new file into place, so its file number (and its
    /// date) differ from the one this process runs.
    private struct Stamp: Equatable {
        let file: Int
        let date: Date
    }

    private init() {
        // Only an app bundle can be opened again (a bare `swift build` binary cannot).
        guard Bundle.main.bundleURL.pathExtension == "app", let exe else { return }
        launched = Self.stamp(exe)
        if launched != nil { poller.start() }
    }

    private static func stamp(_ path: String) -> Stamp? {
        guard let a = try? FileManager.default.attributesOfItem(atPath: path),
              let file = a[.systemFileNumber] as? Int, let date = a[.modificationDate] as? Date else { return nil }
        return Stamp(file: file, date: date)
    }

    private func check() {
        // Missing (mid-install or moved) is not ready: the next check sees the new file.
        guard let exe, let now = Self.stamp(exe), now != launched else { return }
        withAnimation(.smooth(duration: 0.3)) { ready = true }
        poller.stop()
    }

    /// Opens the new build as a second instance, then quits this one once it is up.
    func relaunch() {
        guard ready, !relaunching else { return }
        relaunching = true
        let c = NSWorkspace.OpenConfiguration()
        c.createsNewApplicationInstance = true
        c.arguments = Array(CommandLine.arguments.dropFirst())
        // The server and data it was opened on carry over; a background launch does not (this one was asked for).
        c.environment = ProcessInfo.processInfo.environment.filter { $0.key.hasPrefix("TAPSPACE_") && $0.key != "TAPSPACE_BACKGROUND" }
        NSWorkspace.shared.openApplication(at: Bundle.main.bundleURL, configuration: c) { app, _ in
            Task { @MainActor in
                if app != nil { NSApp.terminate(nil) } else { self.relaunching = false }
            }
        }
    }
}

/// "Update ready", beside the wordmark in Clear all's style once armed.
struct UpdateButton: View {
    let update: ShellUpdate

    var body: some View {
        Button { update.relaunch() } label: {
            Text("Update ready")
                .modifier(RailButton(fg: .tapBlue, bg: Color.tapBlue.opacity(0.12)))
                .frame(height: ClearSpaceButton.height)
        }
        .buttonStyle(ChatPress(scale: 0.96))
        .help("Restart TapSpace on the new version")
    }
}
