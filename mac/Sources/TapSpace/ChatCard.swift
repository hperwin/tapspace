import SwiftUI

/// An update card in the thread (CONTRACT section 4).
struct ChatCardView: View, Equatable {
    static let maxWidth: CGFloat = 460
    let row: CardRow
    /// The row's served image paths, aligned with card.images.
    let images: [String]
    /// The row arrived after a live sync (it springs in): the card plays its beats, once per session.
    let play: Bool
    let width: CGFloat
    var store: Store = .shared

    static func == (a: Self, b: Self) -> Bool { a.row == b.row && a.images == b.images && a.play == b.play && a.width == b.width }

    var body: some View { Text(verbatim: row.card.line).frame(width: width, alignment: .leading) }
}
