import SwiftUI

// A card's items (CONTRACT sections 4 and 13, beat 3): one text row per thing the card is about, never a face, ring
// or tile. The title, what changed, and at the right the state word with the thin bar where there is progress. A
// row that moved since the thread's previous card shows what it was until its change beat, then what it is now.

/// The rows, each arriving on its own spring (RailArrival's mapping, the rail's 0.06 s stagger).
struct CardItemRows: View {
    let card: ChatCard
    let layout: CardLayout
    let beats: CardBeats
    let still: Bool
    let store: Store

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            ForEach(Array(layout.rows.enumerated()), id: \.element) { n, i in
                let item = card.items[i]
                CardItemRow(item: item, title: Self.title(item, info: card.faces[item.ref], store: store),
                            change: layout.changes[i].map { beats.changes[$0] }, still: still)
                    .modifier(CardArrive(box: beats.items[n]))
            }
        }
    }

    /// The sender's short name (CONTRACT section 15), else the card's own words for the item (its snapshot), then the
    /// store's live title.
    static func title(_ item: CardItem, info: CardFaceInfo?, store: Store) -> String {
        if let name = item.name { return name }
        for s in [item.title, info?.title ?? ""] where !CardLayout.plain(s).isEmpty { return CardLayout.plain(s) }
        return CardFace.title(item.ref, info: info, store: store)
    }
}

/// One item: its title (semibold), what changed (the sender's note), and its state at the right.
struct CardItemRow: View {
    let item: CardItem
    let title: String
    /// Its change spring when it moved since the previous card; nil draws it as it is now.
    let change: SpringBox?
    let still: Bool

    var body: some View {
        HStack(alignment: .center, spacing: 12) {
            VStack(alignment: .leading, spacing: 2) {
                Text(verbatim: title)
                    .font(.system(size: 13, weight: .semibold))
                    .foregroundStyle(Color.ink)
                    .lineLimit(1)
                if !item.note.isEmpty {
                    Text(verbatim: item.note)
                        .font(.system(size: 12))
                        .foregroundStyle(Color.dim)
                        .lineLimit(1)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            if let change {
                // The beat: was -> now on the change spring, with ChatNod's kick.
                CardItemFace(box: change, still: still) { now in CardItemState(item: item, now: now, box: change) }
            } else {
                CardItemState(item: item, now: true, box: CardRest.box)
            }
        }
        .accessibilityElement(children: .combine)
    }
}

/// A row's right edge: the thin bar where there is progress (a tap's goals, a master's steps), filling from the
/// previous card's value on `box`, and the plain word ("Landed", "Needs you", "3 of 7").
struct CardItemState: View {
    let item: CardItem
    /// false: the previous card's word (before the change beat).
    let now: Bool
    let box: SpringBox

    var body: some View {
        let s = CardItemShown(item, now: now)
        HStack(spacing: 8) {
            if let bar = s.bar { CardBar(box: box, from: bar.from, to: bar.to).frame(width: 64) }
            if !s.word.isEmpty {
                Text(verbatim: s.word)
                    .font(.system(size: 11.5, weight: .medium))
                    .monospacedDigit()
                    .foregroundStyle(s.color)
                    .lineLimit(1)
                    .contentTransition(.interpolate)
            }
        }
        .fixedSize()
        // RowText's word swap.
        .animation(.smooth(duration: 0.35), value: s.word)
    }
}

/// What a row's right edge says: its word and colour, and where its bar starts (the previous card) and ends.
struct CardItemShown {
    var word = ""
    var color = Color.dim
    var bar: (from: Double, to: Double)?

    init(_ it: CardItem, now: Bool) {
        if let p = it.phase.flatMap(TapPhase.init(rawValue:)) {
            let was = it.was?.phase.flatMap(TapPhase.init(rawValue:))
            let shown = now ? p : was ?? p
            // The word each card showed: the served one now, the earlier card's own before; else the table's.
            let said = now ? it.word : was == nil ? "" : it.was?.word ?? ""
            word = said.isEmpty ? shown.word : said
            color = switch shown {
            case .holding: .tapBlue
            case .landed, .done: .up
            default: .dim
            }
            if let to = Self.part(it.goals) { bar = (Self.part(it.was?.goals ?? []) ?? to, to) }
        } else if let p = it.progress, !p.steps.isEmpty {
            let n = p.steps.count
            let cur = min(max(0, p.current), n), before = min(max(0, it.was?.current ?? cur), n)
            word = Self.steps(now ? cur : before, of: n)
            bar = (Double(before) / Double(n), Double(cur) / Double(n))
        }
    }

    /// [done, total] as 0...1; nil without a total.
    static func part(_ g: [Int]) -> Double? {
        guard g.count == 2, g[1] > 0 else { return nil }
        return clamp01(Double(g[0]) / Double(g[1]))
    }

    /// A master's place in its steps (the progress card's count): "3 of 7", and "7 of 7" at its last step and once
    /// every step is, never "Done": its steps end before Hayden's merge, the note says what is left (CONTRACT 16).
    static func steps(_ cur: Int, of n: Int) -> String { "\(min(cur + 1, n)) of \(n)" }
}

/// A spring that is always at rest: the bar of a row that did not move reads it and never re-renders.
@MainActor
enum CardRest {
    static let box = SpringBox(1)
}
