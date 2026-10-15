import Foundation

extension Tap {
    /// The tap has Hayden's latest comment (the 👀 reaction says it read it)
    /// and has not answered yet: the server's `replying` (server.py `_tap`),
    /// from the same fields it is computed from (an open issue, its last
    /// comment his and seen). API.swift's Tap does not decode `replying` yet;
    /// once it does, this file goes and ChatView reads `tap.replying`. (A
    /// different name, so adding the field to Tap never collides with it.)
    var chatReplying: Bool {
        guard status != "landed", let last = messages.last else { return false }
        return last.who == "you" && (last.seen ?? false)
    }
}
