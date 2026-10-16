import Foundation

// Update cards in the Chat tab: the pure rules (no views). How answers fold into their card, what a face key
// names, the look table, and the small sums the card's parts draw from. The beat times are CardTimes
// (ChatCardMotion.swift).

/// A card row as the thread draws it: the card, its row id and NAME (answers name them), what Hayden answered.
struct CardRow: Equatable {
    let card: ChatCard
    let id: Int
    let agent: String
    var state = CardState()
}

/// What Hayden has answered on one card: the latest verdict per image, the latest ask answer, and which of them
/// are still on their way (shown at once, rolled back on a refusal).
struct CardState: Equatable {
    struct Verdict: Equatable {
        var approve: Bool
        var note = ""
        var sending = false
    }
    var images: [Int: Verdict] = [:]
    var asked = false
    var checks: Set<String> = []
    var text = ""
    var askSending = false

    mutating func apply(_ a: ChatAnswer, sending: Bool) {
        if let i = a.image {
            images[i] = Verdict(approve: a.verdict == "approve", note: a.note, sending: sending)
        } else {
            asked = true
            checks = Set(a.checks)
            text = a.text
            askSending = sending
        }
    }

    /// Changes that move the card (an answer landing or rolling back), for the list's signature.
    var sig: String {
        images.keys.sorted().map { k in
            let v = images[k]!
            return "\(k)\(v.approve ? "a" : "c")\(v.sending ? "~" : "")"
        }.joined() + (asked ? "q" + (askSending ? "~" : "") : "")
    }
}
