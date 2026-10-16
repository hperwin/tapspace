import SwiftUI

// Spin down from the rail: a master's Claude Code session ends and it leaves the
// list (POST /api/agents/stop). The row's right-click menu and a small trailing
// control on hover both ask first, by the master's title (never its NAME).

extension API {
    /// POST /api/agents/stop {name}: ends the Claude sessions registered to that
    /// master and takes it off the list. Speaks for Hayden: refused under
    /// --snapshot/--demo.
    static func stopAgent(_ name: String) async throws {
        try await post("/api/agents/stop", ["name": name])
    }
}

extension Store {
    /// Spins a master down, then reads the list again (the server has already
    /// dropped it, so the row leaves now and an open thread falls back).
    func spinDown(_ key: String) async -> Bool {
        guard let a = agent(key) else { return false }
        do {
            try await API.stopAgent(a.name)
            show("Spun down \(a.title).")
            await agentsPoller.now()
            return true
        } catch {
            show("Couldn’t spin it down: \(error.localizedDescription)")
            return false
        }
    }
}

/// A master's rail row gets Spin down: a right-click menu item and a small
/// control at its trailing edge on hover, both behind one confirm. Takes the
/// key and title only, so a heartbeat poll does not re-render it.
struct SpinDownRow: ViewModifier {
    let key: String
    let title: String
    let store: Store
    @State private var hover = false
    @State private var asking = false
    @State private var working = false

    func body(content: Content) -> some View {
        let shown = hover || asking || working
        content
            .overlay(alignment: .topTrailing) {
                SpinDownButton(active: asking || working) { asking = true }
                    // centred on the 62 pt row, left of its unread dot
                    .padding(.top, 19)
                    .padding(.trailing, 30)
                    .opacity(shown ? 1 : 0)
                    .allowsHitTesting(shown && !working)
                    .animation(.easeOut(duration: 0.2), value: shown)
            }
            .onHover { hover = $0 }
            .contextMenu {
                Button("Spin down…") { asking = true }
                    .disabled(working)
            }
            .alert("Spin down \(title)?", isPresented: $asking) {
                Button("Spin down", role: .destructive, action: stop)
                Button("Cancel", role: .cancel) {}
            } message: {
                Text("Its session ends and it leaves the list.")
            }
    }

    private func stop() {
        working = true
        Task {
            _ = await store.spinDown(key)
            working = false
        }
    }
}

/// The small power control on a master's row: grey, red under the pointer or
/// while its confirm is up.
struct SpinDownButton: View {
    let active: Bool
    let action: () -> Void
    @State private var hover = false

    private static let red = Color(red: 1, green: 59 / 255, blue: 48 / 255)

    var body: some View {
        let shape = RoundedRectangle(cornerRadius: 8, style: .continuous)
        let hot = hover || active
        Button(action: action) {
            Image(systemName: "power")
                .font(.system(size: 11, weight: .semibold))
                .foregroundStyle(hot ? Self.red : Color.dim)
                .frame(width: 24, height: 24)
                .background {
                    ZStack {
                        shape.fill(Color.white)
                        shape.fill(hot ? Self.red.opacity(0.1) : Color.black.opacity(0.05))
                    }
                }
                .contentShape(shape)
        }
        .buttonStyle(ChatPress(scale: 0.9))
        .help("Spin down")
        .accessibilityLabel("Spin down")
        .onHover { h in withAnimation(.easeOut(duration: 0.2)) { hover = h } }
    }
}
