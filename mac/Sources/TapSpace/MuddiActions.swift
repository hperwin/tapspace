import Foundation
import Observation

/// Hayden's two clicks on a tap card: Fly (a proposed tap) and Accept (a landed
/// one, his sign-off). Each posts once, then reads the taps again at 2.5 s and
/// 6 s so the new phase shows. Both speak for Hayden, so --snapshot and --demo
/// refuse them. Agents never call these.
@MainActor @Observable
final class MuddiTapActions {
    static let shared = MuddiTapActions()

    /// How long a click stays acknowledged on the card while GitHub catches up.
    static let hold: Double = 120

    private(set) var flewAt: [String: Double] = [:]
    private(set) var acceptedAt: [String: Double] = [:]
    private(set) var busy: Set<String> = []

    func flew(_ key: String, now: Double) -> Bool { now - (flewAt[key] ?? -.infinity) < Self.hold }
    func accepted(_ key: String, now: Double) -> Bool { now - (acceptedAt[key] ?? -.infinity) < Self.hold }

    func fly(_ tap: Tap, store: Store) {
        run(tap.key, store: store, failure: "Couldn’t fly it") {
            try await API.flyTap(tap.key)
        } done: { [weak self] in
            self?.flewAt[tap.key] = Date().timeIntervalSince1970
        }
    }

    func accept(_ tap: Tap, store: Store) {
        run(tap.key, store: store, failure: "Couldn’t accept it") {
            try await API.acceptTap(tap.key)
        } done: { [weak self] in
            self?.acceptedAt[tap.key] = Date().timeIntervalSince1970
        }
    }

    private func run(_ key: String, store: Store, failure: String,
                     _ post: @escaping () async throws -> Void, done: @escaping () -> Void) {
        guard !busy.contains(key) else { return }
        busy.insert(key)
        Task { @MainActor in
            do {
                try await post()
                done()
                busy.remove(key)
            } catch {
                busy.remove(key)
                store.show("\(failure): \(error.localizedDescription)")
                return
            }
            // The web page's two re-reads; they end if the tap leaves the list.
            for delay in [2.5, 3.5] {
                try? await Task.sleep(for: .seconds(delay))
                guard store.tap(key) != nil else { return }
                await store.tapsPoller.now()
            }
        }
    }
}
