import AppKit
import SwiftUI

// The rail's lines under a master: who it is talking with right now, and the
// change notes Hayden sent on its cards that it has not answered with a new
// version yet. They sit BELOW the master's RailRow as siblings (never inside
// it), so the row frame the liquid selection follows is unchanged.

/// What shows under one master in the rail. One value per render, read by
/// both the rows and the rail's glide signature, so they never disagree.
struct RailDetail: Equatable {
    var talk: RailTalk?
    /// The newest notes, at most `maxNotes`.
    var notes: [AgentNote] = []
    /// Notes beyond those ("+N more").
    var more = 0

    static let maxNotes = 4

    @MainActor static func of(_ a: Agent, store: Store) -> RailDetail {
        var d = RailDetail()
        let all = store.notes(for: a)
        d.notes = Array(all.prefix(maxNotes))
        d.more = all.count - d.notes.count
        let links = store.talking(a.key)
        let titles = links.compactMap { store.agentTitle($0.other(a.name)) }
        if let top = links.first, !titles.isEmpty {
            d.talk = RailTalk(titles: titles, last: top.last, summary: top.summary)
        }
        return d
    }

    var isEmpty: Bool { talk == nil && notes.isEmpty }

    /// Changes that move the rows below this master (lines coming or going), not text.
    var sig: String {
        (talk == nil ? "" : "t") + "|" + notes.map(\.id).joined(separator: ",") + (more > 0 ? "|+" : "")
    }
}

/// A master's live links, as the rail words them (titles only).
struct RailTalk: Equatable {
    /// the other masters' titles, newest link first
    let titles: [String]
    /// the newest link's latest message (epoch)
    let last: Double
    let summary: String

    /// "A", "A and B", "A and 2 more"
    var who: String {
        switch titles.count {
        case 0, 1: titles.first ?? ""
        case 2: "\(titles[0]) and \(titles[1])"
        default: "\(titles[0]) and \(titles.count - 1) more"
        }
    }
}

struct RailAgentDetail: View {
    let detail: RailDetail
    /// Opens this master's thread (which reads its notes).
    let open: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            if let talk = detail.talk {
                RailTalkLine(talk: talk)
                    .modifier(RailArrival(index: 0))
                    .transition(RailNoteRow.exit)
            }
            ForEach(Array(detail.notes.enumerated()), id: \.element.id) { i, n in
                RailNoteRow(note: n, open: open)
                    .modifier(RailArrival(index: i))
                    .transition(RailNoteRow.exit)
            }
            if detail.more > 0 {
                Text("+\(detail.more) more")
                    .font(.system(size: 11))
                    .foregroundStyle(Color.faint)
                    .contentTransition(.numericText(value: Double(detail.more)))
                    .padding(.horizontal, 8)
                    .padding(.top, 1)
                    .transition(.opacity)
            }
        }
        // Text column of the row above: row padding 12 + orb 38 + spacing 12 = 62,
        // less each line's own 8 pt inset (its hover wash).
        .padding(.leading, 54)
        .padding(.trailing, 6)
        .padding(.bottom, detail.isEmpty ? 0 : 4)
    }
}

/// One note: the card's thumbnail and the note's first words, or (a video or
/// no image) the card's title and the words. Clicking opens the master.
struct RailNoteRow: View {
    let note: AgentNote
    let open: () -> Void
    @State private var hover = false

    /// A leaving line shrinks away in place, quickly, before the rows below
    /// slide over its slot (the pill's row exit). Entrances are RailArrival.
    static let exit = AnyTransition.asymmetric(
        insertion: .identity,
        removal: .opacity.combined(with: .scale(scale: 0.94, anchor: .leading))
            .animation(.easeOut(duration: 0.12)))

    var body: some View {
        let words = pillPlain(note.note)
        let shape = RoundedRectangle(cornerRadius: 10, style: .continuous)
        Button(action: open) {
            HStack(spacing: 8) {
                if !note.thumb.isEmpty {
                    PillThumb(path: note.thumb, isVideo: false)
                    Text(words.isEmpty ? note.title : words)
                        .foregroundStyle(Color.dim)
                } else if words.isEmpty {
                    Text(note.title)
                        .fontWeight(.semibold)
                        .foregroundStyle(Color.ink)
                } else {
                    // A long card title is cut short so the note's own words still show.
                    let title = note.title.count > 24
                        ? note.title.prefix(22).trimmingCharacters(in: .whitespaces) + "…" : note.title
                    Text("\(Text(title).fontWeight(.semibold).foregroundStyle(Color.ink)) · \(Text(words).foregroundStyle(Color.dim))")
                }
                Spacer(minLength: 0)
            }
            .font(.system(size: 12))
            .lineLimit(1)
            .truncationMode(.tail)
            .padding(.horizontal, 8)
            .frame(height: 28)
            .background(shape.fill(Color.black.opacity(hover ? 0.03 : 0)))
            .contentShape(shape)
        }
        .buttonStyle(ChatPress(scale: 0.98))
        .help(note.title.isEmpty ? note.note : note.title + ": " + note.note)
        .onHover { h in withAnimation(.easeOut(duration: 0.2)) { hover = h } }
    }
}

/// "Talking with <title>" while this master and another are messaging each
/// other (the server's live window). Its dot breathes on Core Animation.
struct RailTalkLine: View {
    let talk: RailTalk

    var body: some View {
        TimelineView(.periodic(from: .now, by: 30)) { ctx in
            HStack(spacing: 6) {
                BreathingDot(color: Palette.blue, period: 1.4, low: 0.35)
                    .frame(width: 5, height: 5)
                Text("Talking with \(talk.who)")
                    .foregroundStyle(Color.tapBlue)
                Text("· " + when(talk.last, now: ctx.date.timeIntervalSince1970))
                    .foregroundStyle(Color.faint)
                Spacer(minLength: 0)
            }
            .font(.system(size: 11))
            .lineLimit(1)
            .truncationMode(.tail)
        }
        .padding(.horizontal, 8)
        .frame(height: 22)
        .help(talk.summary)
    }
}
