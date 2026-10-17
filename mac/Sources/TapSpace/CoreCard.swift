import Foundation
import Observation

// Hayden's answers to update cards, the drafts the card's fields keep, and the card-frames pose.

/// Answers to cards: posted once each (POST /api/chat with `answer` and a nonce; a card's Accept through the
/// tap's own Accept), shown at once, and kept until a fresh read of the thread has the server's row. A refusal
/// rolls the answer back and says why.
@MainActor @Observable
final class CardAnswers {
    static let shared = CardAnswers()
    private(set) var pending: [PendingAnswer] = []

    func pending(for agent: String) -> [PendingAnswer] { pending.filter { $0.agent == agent } }

    /// Approve, Not quite with its note, the ask's ticks and words, or Not quite on the card's Accept (deny).
    /// False when it did not reach TapSpace.
    @discardableResult
    func send(_ a: ChatAnswer, to agent: String, store: Store? = nil) async -> Bool {
        await post(a, to: agent, store: store ?? .shared, failure: "Didn’t send") { nonce in
            try await API.answer(a, toAgent: agent, nonce: nonce)
        }
    }

    /// Accept on a card whose action is a landed tap's Accept (CONTRACT A1): the tap's own guarded route with the
    /// card's id, so the server signs the tap off and writes the card's answer row. An image's Approve never
    /// comes here (R5).
    @discardableResult
    func accept(tap key: String, card: Int, in agent: String, store: Store? = nil) async -> Bool {
        let store = store ?? .shared
        let ok = await post(ChatAnswer(card: card, action: "accept"), to: agent, store: store, failure: "Couldn’t accept it") { _ in
            try await API.acceptTap(key, card: card)
        }
        guard ok else { return false }
        // The tap card's two re-reads (MuddiTapActions), so its new phase shows; they end if the tap leaves the list.
        Task { @MainActor in
            for delay in [2.5, 3.5] {
                try? await Task.sleep(for: .seconds(delay))
                guard store.tap(key) != nil else { return }
                await store.tapsPoller.now()
            }
        }
        return true
    }

    /// Card frames only: an answer shown as on its way that never posts (the harness echoes its row itself).
    func pose(_ a: ChatAnswer, to agent: String) -> String {
        let p = PendingAnswer(nonce: a.makeNonce(), agent: agent, answer: a)
        pending.append(p)
        return p.nonce
    }

    /// A fresh read of a thread (Store.pollChat): the answers whose rows it has are done.
    func settle(_ agent: String, _ rows: [ChatMessage]) {
        let keep = pending.filter { $0.agent != agent || !$0.landed(in: rows) }
        if keep.count != pending.count { pending = keep }
    }

    private func post(_ a: ChatAnswer, to agent: String, store: Store, failure: String,
                      _ call: (_ nonce: String) async throws -> ChatMessage?) async -> Bool {
        // One in flight per image, per ask and per action.
        guard !pending.contains(where: { $0.agent == agent && $0.answer.sameSlot(a) }) else { return false }
        let p = PendingAnswer(nonce: a.makeNonce(), agent: agent, answer: a)
        pending.append(p)
        // His answer is his word: the master thinks until it replies, as after any message (R6).
        let key = "agent:" + agent
        let before = store.agent(key)?.state
        if let i = store.agents.firstIndex(where: { $0.key == key }) { store.agents[i].state = "thinking" }
        do {
            if let row = try await call(p.nonce) {
                // In the thread now; the pending answer stays until a fresh read has the row too (a poll already
                // on its way may predate it, and would draw the buttons again for a moment).
                store.insertRow(row)
            } else {
                await store.chatPoller.now()
                pending.removeAll { $0.nonce == p.nonce }
            }
            store.chatPoller.kick()
            store.agentsPoller.kick()
            return true
        } catch {
            pending.removeAll { $0.nonce == p.nonce }
            if let before, let i = store.agents.firstIndex(where: { $0.key == key }) { store.agents[i].state = before }
            store.show("\(failure): \(error.localizedDescription)")
            return false
        }
    }
}

extension Store {
    /// Hayden's answer to a card: an image's verdict, the ask's ticks and words, or Not quite on the card's Accept
    /// (`ChatAnswer(card:, note:, action: "deny")`). False when it did not reach TapSpace.
    @discardableResult
    func answer(_ a: ChatAnswer, to agent: String) async -> Bool {
        await CardAnswers.shared.send(a, to: agent, store: self)
    }

    /// Accept pressed on a card's action (after the Accept confirm): that tap's own Accept, with the card's id.
    @discardableResult
    func accept(tap key: String, card: Int) async -> Bool {
        let agent = chats.first { $0.value.contains { $0.id == card && $0.role == "master" } }?.key ?? ""
        return await CardAnswers.shared.accept(tap: key, card: card, in: agent, store: self)
    }
}

/// One image's answer in progress on a card (or the card's Accept / Not quite pair): the notes field and its
/// liquid, the typed note, the check. Kept for the app's life (a thread switch keeps a half-typed note).
@MainActor @Observable
final class CardImageDraft {
    var notesOpen = false
    var text = ""
    var focusToken = 0
    let nw = SpringBox(0, .spreadW)
    let nh = SpringBox(0, .spreadH)
    let check = SpringBox(0, .draw)

    /// VisualModel.toggleNotes (VisualView.swift:354-368) without the grid: width leads going out, height
    /// retracts first coming back.
    func toggleNotes() {
        notesOpen.toggle()
        if notesOpen {
            nw.set(1, .spreadW)
            nh.set(1, .spreadH)
            focusToken += 1
        } else {
            nw.set(0, .retractW)
            nh.set(0, .retractH)
        }
    }
}

/// The ask's ticks and words before Send.
@MainActor @Observable
final class CardAskDraft {
    var ticks: Set<String> = []
    var text = ""
    var focusToken = 0
    func toggle(_ id: String) { if ticks.contains(id) { ticks.remove(id) } else { ticks.insert(id) } }
}

@MainActor
enum CardDrafts {
    private static var images: [String: CardImageDraft] = [:]
    private static var asks: [Int: CardAskDraft] = [:]

    static func image(_ card: Int, _ i: Int) -> CardImageDraft { imageDraft("\(card):\(i)") }
    /// The Accept / Not quite pair of a card whose action is a tap's Accept.
    static func action(_ card: Int) -> CardImageDraft { imageDraft("\(card):action") }

    static func ask(_ card: Int) -> CardAskDraft {
        if let d = asks[card] { return d }
        let d = CardAskDraft()
        asks[card] = d
        return d
    }

    private static func imageDraft(_ key: String) -> CardImageDraft {
        if let d = images[key] { return d }
        let d = CardImageDraft()
        images[key] = d
        return d
    }
}

/// Card frames (HarnessCard): rows the open thread shows on top of the server's while the drive poses them, and
/// where the thread pane sits. Unused outside TAPSPACE_CARD_FRAMES.
@MainActor @Observable
final class CardPose {
    static let shared = CardPose()
    /// "1" pose, "live" a real round trip on a preview, "checks" the rule checks only.
    static let mode = ProcessInfo.processInfo.environment["TAPSPACE_CARD_FRAMES"]
    static var asked: Bool { mode != nil }
    var rows: [ChatMessage] = []
    @ObservationIgnored var pane: CGRect = .zero
    func rows(for agent: String) -> [ChatMessage] { rows.filter { $0.agent == agent } }
}
