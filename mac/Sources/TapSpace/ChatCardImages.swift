import SwiftUI

// A card's pictures and its buttons (CONTRACT sections 2, 3, 4 beat 4, and A1): one picture across or two columns,
// each with its label and Approve / Not quite; a decision card's pictures larger and unlabeled; on a card with an
// action, one Accept / Not quite pair under all of them. An answer swaps its buttons for its line. Approve never
// accepts or flies a tap (R5): only the action's Accept does, through the tap's guarded accept route.

/// The card's pictures, rows of one or two, each arriving like a received bubble.
struct CardImages: View {
    let row: CardRow
    let paths: [String]
    let width: CGFloat
    /// A decision card: the pictures are the card (up to 420 pt tall alone, 300 side by side), no labels.
    let hero: Bool
    /// Approve and Not quite under each picture (a card without an action).
    let decide: Bool
    let beats: CardBeats
    let still: Bool
    let store: Store

    var body: some View {
        let cols = paths.count == 1 ? 1 : 2
        let side = cols == 1 ? width : floor((width - 10) / 2)
        VStack(alignment: .leading, spacing: 14) {
            ForEach(Array(stride(from: 0, to: paths.count, by: cols)), id: \.self) { start in
                HStack(alignment: .top, spacing: 10) {
                    ForEach(start..<min(paths.count, start + cols), id: \.self) { i in
                        CardImageCell(row: row, index: i, path: paths[i], width: side, single: cols == 1, hero: hero,
                                      decide: decide, still: still, store: store)
                            .modifier(CardIn(box: beats.images[i]))
                    }
                }
            }
        }
    }
}

/// One picture (the chat's own cell and lightbox), its label, and its Approve / Not quite or its answer.
struct CardImageCell: View {
    let row: CardRow
    let index: Int
    let path: String
    let width: CGFloat
    let single: Bool
    let hero: Bool
    let decide: Bool
    let still: Bool
    let store: Store
    /// The Visual card's check, drawn on an Approve and cleared after its hold.
    @State private var check = SpringBox(0, .draw)
    @State private var veil = SpringBox(1, .smooth)
    /// "Sent" shows at least this long, even when the server answers at once.
    @State private var sentHold = false

    /// The Visual card's hold before its check clears.
    static let checkHold = 1.45
    static let sentFor = 0.6

    var body: some View {
        let verdict = row.state.images[index]
        let label = row.card.images.indices.contains(index) ? row.card.images[index].label : ""
        VStack(alignment: .leading, spacing: 8) {
            picture
                .overlay {
                    if decide {
                        CardCheckVeil(check: check, veil: veil)
                            .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
                    }
                }
                // Two decision pictures share one height, so their buttons sit level right under them.
                .frame(width: width, height: hero && !single ? 300 : nil, alignment: hero ? .bottom : .leading)
            if !hero {
                Text(verbatim: label.isEmpty ? "Image \(index + 1)" : label)
                    .font(.system(size: 12, weight: .semibold))
                    .foregroundStyle(Color.ink)
                    .lineLimit(1)
            }
            if decide {
                // The buttons line up with the picture; CardMetrics' 12 pt insets stay as the Visual card has them.
                CardDecide(yes: "Approve", draft: CardDrafts.image(row.id, index), said: said(verdict),
                           width: width + 24, still: still, onYes: approve, onSend: change)
                    .padding(.horizontal, -12)
            }
        }
        .frame(width: width, alignment: .leading)
        .onChange(of: verdict?.approve == true) { _, on in drawCheck(on) }
    }

    @ViewBuilder private var picture: some View {
        if hero {
            ChatImageCell(path: path, single: CGSize(width: width, height: single ? 420 : 300))
        } else if single {
            ChatImageCell(path: path, single: CGSize(width: width, height: 280))
        } else {
            ChatImageCell(path: path, square: width)
        }
    }

    private func said(_ v: CardState.Verdict?) -> CardSaid? {
        guard let v else { return nil }
        if v.approve { return CardSaid(word: "Approved", color: .up, sending: v.sending) }
        if v.sending || sentHold { return CardSaid(word: "Sent", color: .dim, sending: true) }
        return CardSaid(word: "Not quite", color: .tapBlue, note: v.note)
    }

    /// Approve: the check draws at once (the answer shows before the server has it) and clears after the Visual
    /// card's hold; a refusal undraws it. Reduce Motion: no check, the line alone.
    private func drawCheck(_ on: Bool) {
        guard !still else { return }
        if on {
            let check = check, veil = veil
            check.set(1, .draw)
            veil.set(0, .smooth, delay: Self.checkHold) { check.snap(0); veil.snap(1) }
        } else {
            check.set(0, .snappy)
            veil.snap(1)
        }
    }

    private func approve() {
        let draft = CardDrafts.image(row.id, index)
        if draft.notesOpen { draft.toggleNotes() }
        Task { await store.answer(ChatAnswer(card: row.id, image: index, verdict: "approve"), to: row.agent) }
    }

    /// Not quite's Send: it needs words (CONTRACT section 12).
    private func change() {
        let draft = CardDrafts.image(row.id, index)
        let note = draft.text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !note.isEmpty else { return }
        draft.toggleNotes()
        holdSent()
        Task {
            let a = ChatAnswer(card: row.id, image: index, verdict: "change", note: note)
            if await store.answer(a, to: row.agent) { draft.text = "" }
        }
    }

    private func holdSent() {
        sentHold = true
        Task { @MainActor in
            try? await Task.sleep(for: .seconds(Self.sentFor))
            sentHold = false
        }
    }
}

/// CheckVeil on its own springs, so only it re-renders while the check draws and clears.
struct CardCheckVeil: View {
    let check: SpringBox
    let veil: SpringBox

    var body: some View { CheckVeil(progress: check.value).opacity(veil.value) }
}

// MARK: - The buttons

/// What an answer leaves where its buttons were: the label (Approved, Not quite, Accepted, Sent), a breathing dot
/// while it is on its way, and the note.
struct CardSaid: Equatable {
    var word: String
    var color: Color
    var note = ""
    var sending = false
}

/// Approve (or the card's Accept) and Not quite, with the field that spreads out of Not quite: the Visual card's
/// bottom (CardBottom) without its model. Once answered, the answer's line takes the buttons' place on
/// CardBottom's crossfade.
struct CardDecide: View {
    let yes: String
    var busy = false
    let draft: CardImageDraft
    /// The answer's line; nil while there is none.
    let said: CardSaid?
    /// The picture's width plus CardMetrics' two 12 pt insets.
    let width: CGFloat
    let still: Bool
    let onYes: () -> Void
    let onSend: () -> Void
    @State private var st: SpringBox
    @State private var saidHeight: CGFloat = CardMetrics.stateRow
    /// The draft's focus count when this mounted: only a later Not quite pulls the keyboard into its field.
    @State private var base: Int

    init(yes: String, busy: Bool = false, draft: CardImageDraft, said: CardSaid?, width: CGFloat, still: Bool,
         onYes: @escaping () -> Void, onSend: @escaping () -> Void) {
        self.yes = yes
        self.busy = busy
        self.draft = draft
        self.said = said
        self.width = width
        self.still = still
        self.onYes = onYes
        self.onSend = onSend
        _st = State(initialValue: SpringBox(said == nil ? 0 : 1, .smooth))
        _base = State(initialValue: draft.focusToken)
    }

    var body: some View {
        let nw = draft.nw.value, nh = draft.nh.value
        let open = nw > 0.002 || nh > 0.002
        let s = clamp01(st.value)
        let extra = CardMetrics.notes(width).maxY + 12 - CardMetrics.actions
        ZStack(alignment: .topLeading) {
            if open { NotesLiquid(nw: nw, nh: nh, width: width) }
            if s < 0.999 {
                HStack(spacing: 8) {
                    PillButton(title: yes, primary: true, disabled: busy, action: onYes)
                    PillButton(title: "Not quite", active: draft.notesOpen) { draft.toggleNotes() }
                }
                .padding(.horizontal, 12)
                .padding(.top, 10)
                .opacity(1 - smoothstep(0, 0.6, s))
                .blur(radius: 6 * s)
                .scaleEffect(1 - 0.03 * s)
                .allowsHitTesting(s < 0.5 && said == nil)
            }
            if open { CardNotesField(draft: draft, focus: draft.focusToken - base, width: width, onSend: onSend) }
            if let said, s > 0.001 {
                CardSaidLine(said: said, still: still)
                    .padding(.horizontal, 12)
                    .padding(.top, 10)
                    .onGeometryChange(for: CGFloat.self, of: { $0.size.height + 6 }) { saidHeight = $0 }
                    .opacity(smoothstep(0.35, 1, s))
                    .blur(radius: 6 * (1 - s))
                    .offset(y: 5 * (1 - s))
                    .allowsHitTesting(false)
            }
        }
        .frame(width: width, height: lerp(CardMetrics.actions, saidHeight, s) + extra * max(0, nh), alignment: .topLeading)
        .clipped()
        .onChange(of: said == nil) { _, none in
            if still {
                withAnimation(.easeOut(duration: 0.2)) { st.snap(none ? 0 : 1) }
            } else {
                st.set(none ? 0 : 1, .smooth)
            }
        }
    }
}

/// The note under Not quite (NotesContent's layout and words, on the draft's binding). Send needs words.
struct CardNotesField: View {
    @Bindable var draft: CardImageDraft
    let focus: Int
    let width: CGFloat
    let onSend: () -> Void

    var body: some View {
        let r = CardMetrics.notes(width)
        let detail = smoothstep(0.62, 0.97, draft.nw.value) * smoothstep(0.55, 0.97, draft.nh.value)
        let empty = draft.text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        VStack(spacing: 8) {
            GrowingTextView(text: $draft.text, placeholder: "Tell the agent what to change…",
                            fontSize: 13, returnSends: false, focusToken: focus, onSend: onSend)
                .padding(.horizontal, 10)
                .padding(.vertical, 7)
                .frame(height: 82)
                .background(RoundedRectangle(cornerRadius: 11, style: .continuous).fill(.white))
                .overlay(RoundedRectangle(cornerRadius: 11, style: .continuous).stroke(Color.black.opacity(0.07), lineWidth: 1))
            PillButton(title: "Send", primary: true, disabled: empty, action: onSend)
        }
        .padding(8)
        .frame(width: r.width, height: r.height, alignment: .top)
        .offset(x: r.minX, y: r.minY + lerp(-6, 0, detail))
        .opacity(detail)
        .allowsHitTesting(detail > 0.5)
    }
}

/// An answer's line: its label (the tap card's), the breathing dot while it is on its way, the note under it.
struct CardSaidLine: View {
    let said: CardSaid
    let still: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 6) {
                MuddiCardLabel(text: said.word, color: said.color)
                if said.sending {
                    BreathingDot(color: Palette.faint, period: 2, animated: !still)
                        .frame(width: 6, height: 6)
                }
            }
            if !said.note.isEmpty {
                Text(verbatim: said.note)
                    .font(.system(size: 12))
                    .foregroundStyle(Color.dim)
                    .lineSpacing(2)
                    .lineLimit(3)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        // The tap card's answered flip.
        .animation(.smooth(duration: 0.3), value: said)
    }
}

// MARK: - The card's one action

/// A decision card's one action (CONTRACT A1): Accept, asked first with the tap card's own confirm and sent through
/// the tap's guarded Accept with this card's id (the server then adds the answer row), or Not quite with a note,
/// which is an answer row and accepts nothing.
struct CardActionView: View {
    let row: CardRow
    let tap: String
    let width: CGFloat
    let still: Bool
    let store: Store
    @State private var asking = false
    @State private var busy = false
    @State private var sentHold = false

    var body: some View {
        let title = CardFace.title(tap, info: row.card.faces[tap], store: store)
        CardDecide(yes: busy ? "Accepting…" : "Accept", busy: busy, draft: CardDrafts.action(row.id), said: said,
                   width: width + 24, still: still, onYes: { asking = true }, onSend: deny)
            .padding(.horizontal, -12)
            .alert(title.isEmpty ? "Accept it?" : "Accept \(title)?", isPresented: $asking) {
                Button("Cancel", role: .cancel) {}
                Button("Accept", action: accept)
            } message: {
                Text("It closes on GitHub as done.")
            }
    }

    private var said: CardSaid? {
        let fresh = CardAccepts.fresh(row.id, now: Date().timeIntervalSince1970)
        guard let v = row.state.action else {
            return fresh ? CardSaid(word: "Accepted", color: .up, note: "It shows as Done in a moment.") : nil
        }
        if v.approve {
            // An Accept on its way keeps its button ("Accepting…"), as the tap card's does: it closes the tap on
            // GitHub. Right after it, the tap card's words; a card read later just says it was accepted.
            if v.sending && !fresh { return nil }
            return CardSaid(word: "Accepted", color: .up, note: fresh ? "It shows as Done in a moment." : "")
        }
        if v.sending || sentHold { return CardSaid(word: "Sent", color: .dim, sending: true) }
        return CardSaid(word: "Not quite", color: .tapBlue, note: v.note)
    }

    private func accept() {
        let draft = CardDrafts.action(row.id)
        if draft.notesOpen { draft.toggleNotes() }
        busy = true
        Task {
            if await store.accept(tap: tap, card: row.id) { CardAccepts.mark(row.id) }
            busy = false
        }
    }

    /// Not quite's Send: it needs words (CONTRACT section 12).
    private func deny() {
        let draft = CardDrafts.action(row.id)
        let note = draft.text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !note.isEmpty else { return }
        draft.toggleNotes()
        sentHold = true
        Task { @MainActor in
            try? await Task.sleep(for: .seconds(CardImageCell.sentFor))
            sentHold = false
        }
        Task {
            if await store.answer(ChatAnswer(card: row.id, note: note, action: "deny"), to: row.agent) {
                draft.text = ""
            }
        }
    }
}

/// Cards whose Accept went through on this Mac, and when: the tap card's words hold for its two minutes.
@MainActor
enum CardAccepts {
    private static var at: [Int: Double] = [:]

    static func mark(_ card: Int) { at[card] = Date().timeIntervalSince1970 }
    static func fresh(_ card: Int, now: Double) -> Bool { now - (at[card] ?? -.infinity) < MuddiTapActions.hold }
}
