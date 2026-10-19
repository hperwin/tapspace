import SwiftUI

// Restart from the rail: after a crash or reboot a master whose sessions all
// ended is served "offline". Its row shows a Restart button that starts it again
// under the same NAME (Muddi through muddi.sh, any other master as a fresh one in
// its folder) via POST /api/agents/restart. No confirm: nothing is lost.

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
/// trailing edge left of its unread dot.
struct RestartButton: View {
    let key: String
    let store: Store
    @State private var hover = false
    @State private var working = false

    var body: some View {
        let shape = Capsule(style: .continuous)
        Button(action: restart) {
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

    private func restart() {
        working = true
        Task {
            await store.restart(key)
            working = false
        }
    }
}
