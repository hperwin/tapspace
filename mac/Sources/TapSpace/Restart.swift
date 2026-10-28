import SwiftUI

// Restart from the rail: after a crash or reboot a master whose sessions all
// ended is served "offline". Its row shows a Restart button that starts it again
// under the same NAME (Muddi through muddi.sh, any other master as a fresh one in
// its folder) via POST /api/agents/restart. No confirm: nothing is lost. When a
// launch fails after the server already answered, the row is offline again and
// carries the launcher's reason (`launch`), shown under it until a launch works.

extension API {
    /// POST /api/agents/restart {name}: starts an offline (or stopped) master
    /// again; the row reads "starting" until it listens. Speaks for Hayden:
    /// refused under --snapshot/--demo.
    static func restartAgent(_ name: String) async throws {
        try await post("/api/agents/restart", ["name": name])
    }
}

extension Store {
    /// Restarts a master, then reads the list again so the row shows starting now.
    @discardableResult
    func restart(_ key: String) async -> Bool {
        guard let a = agent(key) else { return false }
        do {
            try await API.restartAgent(a.name)
            show("Restarting \(a.title)")
            await agentsPoller.now()
            return true
        } catch {
            show("Couldn’t restart it: \(error.localizedDescription)")
            return false
        }
    }
}

/// The offline row's Restart: always visible (not on hover), at the row's
/// trailing edge left of its unread dot. SpinDownRow owns the busy flag and the
/// call, so this button and the row's menu item never both post.
struct RestartButton: View {
    let working: Bool
    let action: () -> Void
    @State private var hover = false

    /// Trailing room an offline row's text keeps clear of this capsule.
    static let room: CGFloat = 66

    var body: some View {
        let shape = Capsule(style: .continuous)
        Button(action: action) {
            Text("Restart")
                .font(.system(size: 11, weight: .semibold))
                .foregroundStyle(Color.white)
                .padding(.horizontal, 10)
                .frame(height: 24)
                .background(shape.fill(Color.ink.opacity(hover ? 0.85 : 1)))
                .contentShape(shape)
                .opacity(working ? 0.5 : 1)
        }
        .buttonStyle(ChatPress(scale: 0.92))
        .disabled(working)
        .help("Restart")
        .accessibilityLabel("Restart")
        .onHover { h in withAnimation(.easeOut(duration: 0.2)) { hover = h } }
    }
}

/// Under an offline row whose last launch failed (the server's `launch`): "Couldn’t start: claude missing from
/// PATH", dim, on the row's text column, so the next Restart is an informed one. A sibling of the row, like
/// the rail's note lines, so the selection's frame is unchanged.
struct RestartReason: View {
    let error: String

    /// The reason to show under this row: offline with a failed launch on record; nil otherwise.
    static func error(_ a: Agent) -> String? {
        guard a.state == "offline", let why = a.launch?.error.trimmingCharacters(in: .whitespacesAndNewlines),
              !why.isEmpty else { return nil }
        return why
    }

    var body: some View {
        Text("Couldn’t start: " + error)
            .font(.system(size: 11))
            .foregroundStyle(Color.dim)
            .lineLimit(2)
            .truncationMode(.tail)
            .fixedSize(horizontal: false, vertical: true)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, 8)
            // The text column of the row above (RailAgentDetail's inset).
            .padding(.leading, 54)
            .padding(.trailing, 6)
            .padding(.bottom, 4)
            .help(error)
            .modifier(RailArrival(index: 0))
            .transition(RailNoteRow.exit)
    }
}
