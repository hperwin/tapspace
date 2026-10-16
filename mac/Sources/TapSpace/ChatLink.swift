import AppKit
import QuartzCore
import SwiftUI

// Two masters messaging each other: their cards sit together in the progress
// column (ChatProgressBoard pairs them) and the opened gap between them holds
// a line in the face column, "A → B · 2m" and, under it, what was said (two
// lines at most). The line draws in from
// the sender toward the receiver, then TapSpace's chasing draw runs along it,
// on the render server, while anyone can see it. A new message restarts the
// chase from its sender, and the receiving card nods (ChatNod).

/// What the link's chase may do right now.
enum ChatLinkMotion: Equatable {
    /// The chase runs (the Chat tab up, the window on screen, no lightbox over it).
    case run
    /// Reduce Motion: the whole line in accent, still.
    case still
    /// Paused (off the page, window hidden, under an overlay): the faint base line only.
    case off
}

@MainActor
enum ChatLinkBoard {
    /// The whole pair in one line, "Glint app → TapSpace · Is the feed live?":
    /// titles in ink, the rest dim. The gap draws it on two lines instead
    /// (`ChatLinkWords`); the link-frames check reads this one.
    /// Titles only; a missing title (never expected: both ends hold cards) is blank.
    static func label(_ l: AgentLink, titles: [String: String]) -> AttributedString {
        var line = AttributedString(short(titles[l.from] ?? ""))
        line.foregroundColor = Color.ink
        line.font = .system(size: 11, weight: .semibold)
        var arrow = AttributedString(" → ")
        arrow.foregroundColor = Color.dim
        var to = AttributedString(short(titles[l.to] ?? ""))
        to.foregroundColor = Color.ink
        to.font = .system(size: 11, weight: .semibold)
        line.append(arrow)
        line.append(to)
        let said = words(l)
        if !said.isEmpty {
            var rest = AttributedString(" · " + said)
            rest.foregroundColor = Color.dim
            line.append(rest)
        }
        return line
    }

    /// What was said, as plain words on one line (the gap wraps it to two).
    static func words(_ l: AgentLink) -> String { ChatProgressBoard.oneLine(l.summary) }

    /// A long title is cut short in the line so the message's own words still show
    /// (both cards, with their full titles, sit right beside it).
    static func short(_ title: String) -> String {
        title.count > 14 ? title.prefix(13).trimmingCharacters(in: .whitespaces) + "\u{2026}" : title
    }

    /// VoiceOver: "Glint app is talking to TapSpace: Is the feed live?".
    static func spoken(_ l: AgentLink, titles: [String: String]) -> String {
        let said = words(l)
        return "\(titles[l.from] ?? "") is talking to \(titles[l.to] ?? "")" + (said.isEmpty ? "" : ": " + said)
    }
}

/// The gap's two lines: "Glint app → TapSpace · 2m" (titles in ink, the rest
/// dim; a long title gives up its end first, the time always shows), then what
/// was said in dim, wrapping to two lines.
struct ChatLinkWords: View {
    let link: AgentLink
    let titles: [String: String]
    let now: Double

    var body: some View {
        let said = ChatLinkBoard.words(link)
        VStack(alignment: .leading, spacing: 2) {
            HStack(spacing: 0) {
                title(link.from)
                Text(" → ").foregroundStyle(Color.dim).fixedSize()
                title(link.to)
                Text(" · " + when(link.last, now: now))
                    .foregroundStyle(Color.faint)
                    .monospacedDigit()
                    .fixedSize()
            }
            if !said.isEmpty {
                Text(said)
                    .foregroundStyle(Color.dim)
                    .lineLimit(2)
                    .truncationMode(.tail)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .font(.system(size: 11))
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private func title(_ name: String) -> some View {
        Text(titles[name] ?? "")
            .font(.system(size: 11, weight: .semibold))
            .foregroundStyle(Color.ink)
            .lineLimit(1)
            .truncationMode(.tail)
    }
}

/// The space under a card: plain (10 pt) between cards that are not talking.
/// Under the upper card of a talking pair it opens (after the cards' glide
/// lands) to fit its two lines and holds the connector and the pair's words. It
/// belongs to the upper card's element, so it moves with the layout and never
/// tracks frames.
struct ChatProgressGap: View {
    let link: AgentLink?
    /// The upper card's agent NAME (a hidden key): the line runs down when it sent the newest message.
    let upper: String
    let titles: [String: String]
    let motion: ChatLinkMotion
    /// The column's clock (its 15 s tick), for "· 2m".
    var now: Double = Date().timeIntervalSince1970
    @State private var open = SpringBox(0, .smooth)
    /// The last link shown, kept while the gap closes so its words fade instead of vanishing.
    @State private var shown: AgentLink?
    /// The words' own height at this width (a second summary line makes it taller).
    /// Read from the words, which never size to the gap, so it cannot feed back.
    @State private var wordsHeight: CGFloat = 0

    static let plain: CGFloat = 10
    /// An open gap never gets shorter than this (room for the connector).
    static let joined: CGFloat = 44
    /// Room above and below the words in an open gap.
    static let pad: CGFloat = 9
    /// The face column's center: card padding 14 + half the 26 pt face.
    static let lineX: CGFloat = 27
    /// The words start on the card's text column: padding 14 + face 26 + spacing 10.
    static let textX: CGFloat = 50

    var body: some View {
        let p = open.value
        let h = lerp(Self.plain, max(Self.joined, wordsHeight + 2 * Self.pad), p)
        ZStack(alignment: .topLeading) {
            if let l = link ?? shown, p > 0.001 {
                let draw = smoothstep(0.35, 1, p)
                let words = smoothstep(0.5, 1, p)
                ChatLinkLine(downward: l.from == upper, draw: draw,
                             motion: link != nil && draw > 0.95 ? motion : .off, beat: l.last)
                    .frame(width: 6, height: h)
                    .offset(x: Self.lineX - 3)
                ChatLinkWords(link: l, titles: titles, now: now)
                    .padding(.leading, Self.textX)
                    .padding(.trailing, 14)
                    .fixedSize(horizontal: false, vertical: true)
                    .onGeometryChange(for: CGFloat.self, of: { $0.size.height }) { wordsHeight = $0 }
                    .padding(.top, Self.pad)
                    .opacity(words)
                    .offset(y: 4 * (1 - words))
                    .help(ChatLinkBoard.spoken(l, titles: titles))
                    .accessibilityElement(children: .ignore)
                    .accessibilityLabel(ChatLinkBoard.spoken(l, titles: titles))
            }
        }
        .frame(maxWidth: .infinity, alignment: .topLeading)
        .frame(height: h, alignment: .top)
        .onChange(of: link, initial: true) { _, l in
            if let l {
                shown = l
                // A new pair opens once the cards' glide has landed; an open pair stays put.
                open.set(1, .smooth, delay: open.target >= 1 ? 0 : 0.35)
            } else if open.target != 0 {
                // Words and line fade first (their smoothsteps), then the gap closes.
                open.set(0, .snappy) { shown = nil }
            }
        }
    }
}

/// The connector on Core Animation: a faint accent base that draws in from
/// the sender, and on top TapSpace's chase (`chaseDraw`, inOutSine on each
/// half, 1.9 s) while `motion` is `.run`. `beat` (the newest message's time)
/// restarts the chase from the sender. The link-frames harness can pin the
/// chase's phase: offline renders draw model values, not running animations.
struct ChatLinkLine: View {
    let downward: Bool
    let draw: Double
    let motion: ChatLinkMotion
    let beat: Double

    var body: some View {
        ChatLinkLayer(downward: downward, draw: draw, motion: motion, beat: beat, phase: HarnessLink.shared.phase)
            .allowsHitTesting(false)
            .accessibilityHidden(true)
    }
}

private struct ChatLinkLayer: NSViewRepresentable {
    let downward: Bool
    let draw: Double
    let motion: ChatLinkMotion
    let beat: Double
    let phase: Double?

    final class Coordinator {
        let base = CAShapeLayer()
        let chase = CAShapeLayer()
        var downward = true
        var sig = ""

        /// A vertical line through the middle, from the sender's end to the
        /// receiver's (the host is flipped: +y is down).
        func place(_ b: CGRect) {
            let path = CGMutablePath()
            if b.height > 2 {
                let top = CGPoint(x: b.midX, y: 1), bottom = CGPoint(x: b.midX, y: b.height - 1)
                path.move(to: downward ? top : bottom)
                path.addLine(to: downward ? bottom : top)
            }
            for l in [base, chase] {
                l.frame = b
                l.path = path
            }
        }
    }

    func makeCoordinator() -> Coordinator { Coordinator() }

    func makeNSView(context: Context) -> LayerHost {
        let v = LayerHost()
        let c = context.coordinator
        for l in [c.base, c.chase] {
            l.fillColor = nil
            l.lineWidth = 2
            l.lineCap = .round
            v.layer?.addSublayer(l)
        }
        c.base.strokeColor = Palette.blue.withAlphaComponent(0.25).cgColor
        c.base.strokeEnd = 0
        c.chase.strokeColor = Palette.blue.cgColor
        c.chase.strokeEnd = 0
        c.downward = downward
        v.onLayout = { host in c.place(host.bounds) }
        return v
    }

    func updateNSView(_ v: LayerHost, context: Context) {
        let c = context.coordinator
        CATransaction.begin()
        CATransaction.setDisableActions(true)
        if c.downward != downward {
            c.downward = downward
            c.place(v.bounds)
        }
        c.base.strokeEnd = CGFloat(clamp01(draw))
        let sig = "\(downward)|\(motion)|\(beat)|\(phase.map { "\($0)" } ?? "-")"
        if sig != c.sig {
            c.sig = sig
            c.chase.removeAnimation(forKey: "chase")
            if let phase, motion != .off {
                let s = chaseStroke(at: phase)
                c.chase.strokeStart = CGFloat(s.start)
                c.chase.strokeEnd = CGFloat(s.end)
            } else {
                c.chase.strokeStart = 0
                c.chase.strokeEnd = motion == .still ? 1 : 0
                if motion == .run { c.chase.add(chaseDraw(ease: .eachHalf(.inOutSine)), forKey: "chase") }
            }
        }
        CATransaction.commit()
    }
}
