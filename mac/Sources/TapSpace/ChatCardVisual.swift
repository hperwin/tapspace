import SwiftUI

// A card's one visual (CONTRACT sections 1 and 4, beat 2): how far (a bar that fills from the previous card's
// count), where in its steps (goal checks and the step it is on), how it went (a sparkline that draws itself), or
// one big word. Each piece reads only its own spring.

/// The one visual under the head. Draws only what CardLayout.draws accepts.
struct CardVisualView: View {
    let visual: CardVisual
    let beats: CardBeats

    var body: some View {
        switch visual.kind {
        case "progress": CardProgressBar(visual: visual, beats: beats)
        case "timeline": CardTimeline(visual: visual, beats: beats)
        case "graph": CardSparkline(visual: visual, beats: beats)
        case "word": CardWord(text: visual.text, box: beats.visual)
        default: EmptyView()
        }
    }
}

/// The step track's long bar filling from `from` to done/total, and under it the sender's label or "3 of 5".
struct CardProgressBar: View {
    let visual: CardVisual
    let beats: CardBeats

    var body: some View {
        let f = CardLayout.fill(visual)
        VStack(alignment: .leading, spacing: 7) {
            CardBar(box: beats.visual, from: f.from, to: f.to)
            CardVisualLabel(text: visual.label, count: "\(min(visual.done, visual.total)) of \(visual.total)")
                .modifier(CardRise(box: beats.visual))
        }
    }
}

/// The goal checks, one per step (done, the one it is on, the rest), each popping in 40 ms after the one before,
/// and only the current step's words with "N of M" (CONTRACT section 10).
struct CardTimeline: View {
    let visual: CardVisual
    let beats: CardBeats

    var body: some View {
        let steps = Array(visual.steps.prefix(7))
        let at = min(max(0, visual.at), steps.count)
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 6) {
                ForEach(steps.indices, id: \.self) { i in
                    CardStepCheck(done: i < at, current: i == at)
                        .modifier(CardPop(box: beats.pills[i]))
                }
            }
            ChatStepLine(progress: AgentProgress(steps: steps, current: at))
                .modifier(CardRise(box: beats.visual))
        }
        .accessibilityElement(children: .combine)
    }
}

/// One step: GoalCheck done or still to come, the step it is on ringed in the accent.
struct CardStepCheck: View {
    let done: Bool
    let current: Bool

    var body: some View {
        if current {
            Circle()
                .strokeBorder(Color.tapBlue, lineWidth: 1.5)
                .background(Circle().fill(Color.tapBlue.opacity(0.1)))
                .frame(width: 15, height: 15)
                .padding(.top, 1)
        } else {
            GoalCheck(done: done)
        }
    }
}

/// The series drawing itself (CardSpark), its label under it.
struct CardSparkline: View {
    let visual: CardVisual
    let beats: CardBeats

    var body: some View {
        VStack(alignment: .leading, spacing: 7) {
            CardSpark(box: beats.visual, values: visual.points)
            if !visual.label.isEmpty {
                CardVisualLabel(text: visual.label, count: "").modifier(CardRise(box: beats.visual))
            }
        }
    }
}

/// One word, in the Hello screen's big type; a long one shrinks to fit its line.
struct CardWord: View {
    let text: String
    let box: SpringBox

    var body: some View {
        Text(verbatim: text)
            .font(.system(size: 22, weight: .semibold))
            .tracking(-0.4)
            .foregroundStyle(Color.ink)
            .lineLimit(1)
            .minimumScaleFactor(0.6)
            .modifier(CardRise(box: box))
    }
}

/// A visual's words: the sender's label in the secondary voice, or the count in ink when there is none.
struct CardVisualLabel: View {
    let text: String
    let count: String

    var body: some View {
        Text(verbatim: text.isEmpty ? count : text)
            .font(.system(size: 12, weight: text.isEmpty ? .semibold : .regular))
            .monospacedDigit()
            .foregroundStyle(text.isEmpty ? Color.ink : Color.dim)
            .lineLimit(1)
    }
}
