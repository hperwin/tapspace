import SwiftUI

// A card's question (CONTRACT sections 2 and 4, beat 5): its boxes to tick, a field when it wants words, and Send,
// which needs a tick or words (section 10). Once sent: Sent with a breathing dot until the server has the row (and
// at least 0.6 s), then Answered, with what was ticked and said. Nothing to answer again.

/// The question as one block, arriving on the card's ask spring.
struct CardAskView: View {
    let row: CardRow
    let ask: CardAsk
    let still: Bool
    @State private var sentHold = false

    var body: some View {
        let draft = CardDrafts.ask(row.id)
        let st = row.state
        let answered = st.asked
        let sending = answered && (st.askSending || sentHold)
        VStack(alignment: .leading, spacing: 8) {
            Text(verbatim: ask.q)
                .font(.system(size: 13, weight: .semibold))
                .foregroundStyle(Color.ink)
                .lineSpacing(2)
                .fixedSize(horizontal: false, vertical: true)
            if !ask.checks.isEmpty {
                VStack(alignment: .leading, spacing: 0) {
                    ForEach(ask.checks) { c in
                        CardAskCheck(label: c.label, on: answered ? st.checks.contains(c.id) : draft.ticks.contains(c.id),
                                     answered: answered, still: still) { draft.toggle(c.id) }
                    }
                }
            }
            if ask.text {
                if answered {
                    if !st.text.isEmpty {
                        Text(verbatim: st.text)
                            .font(.system(size: 12.5))
                            .foregroundStyle(Color.dim)
                            .lineSpacing(2)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                } else {
                    CardAskField(draft: draft, onSend: send)
                }
            }
            HStack(spacing: 6) {
                if answered {
                    MuddiCardLabel(text: sending ? "Sent" : "Answered", color: .dim)
                    if sending {
                        BreathingDot(color: Palette.faint, period: 2, animated: !still)
                            .frame(width: 6, height: 6)
                    }
                } else {
                    Spacer(minLength: 0)
                    MuddiCardButton(title: "Send", busy: !ready(draft), action: send)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        // The tap card's answered flip.
        .animation(.smooth(duration: 0.3), value: answered)
        .animation(.smooth(duration: 0.3), value: sending)
    }

    private func ready(_ d: CardAskDraft) -> Bool {
        !d.ticks.isEmpty || (ask.text && !d.text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
    }

    private func send() {
        let draft = CardDrafts.ask(row.id)
        guard !row.state.asked, ready(draft) else { return }
        let a = ChatAnswer(card: row.id, checks: ask.checks.map(\.id).filter(draft.ticks.contains),
                           text: ask.text ? draft.text.trimmingCharacters(in: .whitespacesAndNewlines) : "")
        sentHold = true
        Task { @MainActor in
            try? await Task.sleep(for: .seconds(CardImageCell.sentFor))
            sentHold = false
        }
        Task {
            if await CardSend.answer(a, to: row.agent) {
                draft.ticks = []
                draft.text = ""
            }
        }
    }
}

/// One box to tick: GoalCheck's ring with its disc popping in, the check drawing on, and the label.
struct CardAskCheck: View {
    let label: String
    let on: Bool
    let answered: Bool
    let still: Bool
    let toggle: () -> Void

    var body: some View {
        Button(action: toggle) {
            HStack(alignment: .top, spacing: 8) {
                CardTick(on: on, still: still)
                Text(verbatim: label)
                    .font(.system(size: 12.5))
                    .foregroundStyle(on ? Color.ink : Color.dim)
                    .lineLimit(1)
            }
            .padding(.vertical, 4)
            .frame(maxWidth: .infinity, alignment: .leading)
            .contentShape(Rectangle())
        }
        .buttonStyle(ChatPress(scale: 0.98))
        .disabled(answered)
        .accessibilityAddTraits(on ? .isSelected : [])
    }
}

/// The tick (P7 2.2.4): GoalCheck's ring, the green disc on UnreadDot's pop, the check drawing on.
struct CardTick: View {
    let on: Bool
    let still: Bool
    @State private var disc: SpringBox

    init(on: Bool, still: Bool) {
        self.on = on
        self.still = still
        _disc = State(initialValue: SpringBox(on ? 1 : 0, .pop))
    }

    var body: some View {
        ZStack {
            Circle().strokeBorder(on ? Color.up : Color.faint, lineWidth: 1.5)
            Circle().fill(Color.up).scaleEffect(max(0, disc.value))
            if on {
                let mark = Image(systemName: "checkmark").font(.system(size: 7.5, weight: .heavy)).foregroundStyle(.white)
                if still { mark.transition(.opacity) } else { mark.transition(.symbolEffect(.drawOn)) }
            }
        }
        .frame(width: 15, height: 15)
        .padding(.top, 1)
        .animation(.smooth, value: on)
        .onChange(of: on) { _, v in
            if still { disc.snap(v ? 1 : 0) } else { disc.set(v ? 1 : 0, .pop) }
        }
    }
}

/// The ask's words: Cmd-Return sends, Return is a new line (the composer keeps its Return). Only a click focuses it.
struct CardAskField: View {
    @Bindable var draft: CardAskDraft
    let onSend: () -> Void

    var body: some View {
        GrowingTextView(text: $draft.text, placeholder: "Your answer…", fontSize: 13, returnSends: false, onSend: onSend)
            .padding(.horizontal, 10)
            .padding(.vertical, 7)
            .frame(height: 64)
            .background(RoundedRectangle(cornerRadius: 11, style: .continuous).fill(Color.black.opacity(0.025)))
            .overlay(RoundedRectangle(cornerRadius: 11, style: .continuous).stroke(Color.hairline, lineWidth: 1))
    }
}
