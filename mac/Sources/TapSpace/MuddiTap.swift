import AppKit
import SwiftUI

// Muddi on a tap: its avatar's look per phase, and the card at the top of its
// thread (the question to answer, the proof to accept, the flight to clear).
// Existing tokens only; rings move only while the pilot works, except holding,
// which keeps the needs-you breathing.

/// How a tap's ring avatar looks: the ring, the disc behind it, and the check.
struct MuddiLook {
    let ring: RingStyle
    let fill: Color
    let glyph: Color
    /// The green check: done (with a phase), landed (legacy, which meant closed).
    let check: Bool

    init(ring: RingStyle, fill: Color, glyph: Color, check: Bool) {
        self.ring = ring
        self.fill = fill
        self.glyph = glyph
        self.check = check
    }

    init(_ phase: TapPhase, working: Bool) {
        let grey = Color(white: 0.957)
        switch phase {
        case .proposed: self.init(ring: .dotted(Palette.faint, period: working ? 6 : 0), fill: grey, glyph: .dim, check: false)
        case .enroute: self.init(ring: .spin(Palette.ink, length: 0.27, period: working ? 1.6 : 0), fill: grey, glyph: .dim, check: false)
        case .holding: self.init(ring: .breathe(Palette.blue, period: 1.5), fill: Color.tapBlue.opacity(0.08), glyph: .tapBlue, check: false)
        case .landed: self.init(ring: .solid(Palette.green), fill: Color.up.opacity(0.09), glyph: .up, check: false)
        case .done: self.init(ring: .solid(Palette.green), fill: Color.up.opacity(0.09), glyph: .up, check: true)
        case .closed: self.init(ring: .solid(Palette.faint), fill: grey, glyph: .dim, check: false)
        }
    }
}

// MARK: - The tap card at the top of the thread

/// Under the goals, inside the pane's opaque header (it never scrolls away):
/// holding shows the question and whether Hayden has answered it (he answers in
/// the composer below); landed shows the proof and Accept; proposed shows Fly.
struct MuddiTapCard: View {
    let tap: Tap
    let store: Store

    static func shows(_ tap: Tap) -> Bool { tap.cardKind != nil }

    var body: some View {
        // A fly or an accept stays acknowledged for two minutes: re-read the clock.
        TimelineView(.periodic(from: .now, by: 10)) { ctx in
            card(now: ctx.date.timeIntervalSince1970)
        }
    }

    private func card(now: Double) -> some View {
        let answered = tap.cardKind == .holding
            && tap.askAnswered(pending: store.pending.filter { $0.key == tap.key }.map(\.ts))
        let blue = tap.cardKind == .holding && !answered
        return VStack(alignment: .leading, spacing: 8) {
            switch tap.cardKind {
            case .holding: MuddiHoldingCard(ask: tap.ask, answered: answered)
            case .landed: MuddiLandedCard(tap: tap, store: store, now: now)
            case .proposed: MuddiProposedCard(tap: tap, store: store, now: now)
            case nil: EmptyView()
            }
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 14, style: .continuous)
            .fill(blue ? Color.tapBlue.opacity(0.07) : Color.black.opacity(0.03)))
        .padding(.horizontal, 20)
        .padding(.vertical, 12)
        .animation(.smooth(duration: 0.3), value: answered)
    }
}

/// The card's label line, in the goals strip's count voice: "NEEDS YOU · 5m".
struct MuddiCardLabel: View {
    let text: String
    let color: Color
    var at: Double = 0

    var body: some View {
        HStack(spacing: 5) {
            Text(text.uppercased()).foregroundStyle(color)
            if at > 0 {
                Text("·")
                TimelineView(.periodic(from: .now, by: 30)) { ctx in
                    Text(when(at, now: ctx.date.timeIntervalSince1970))
                }
            }
        }
        .font(.system(size: 10.5, weight: .semibold))
        .tracking(0.8)
        .foregroundStyle(Color.faint)
        .contentTransition(.interpolate)
    }
}

/// The pilot's question, as plain text (never markdown). Six lines, all of it on hover.
struct MuddiHoldingCard: View {
    let ask: TapAsk?
    let answered: Bool
    @State private var hover = false

    var body: some View {
        let text = (ask?.text ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        MuddiCardLabel(text: answered ? "Answered" : "Needs you", color: answered ? .dim : .tapBlue, at: ask?.date ?? 0)
        Text(verbatim: text.isEmpty ? "It’s waiting on your reply." : text)
            .font(.system(size: 12.5))
            .foregroundStyle(answered ? Color.dim : Color.ink)
            .lineSpacing(2.5)
            .lineLimit(hover ? nil : 6)
            .fixedSize(horizontal: false, vertical: true)
            .textSelection(.enabled)
            .help(text)
            .onHover { h in withAnimation(.spring(response: 0.38, dampingFraction: 0.8)) { hover = h } }
    }
}

/// Every goal is proven: the proof the pilot put in the issue, and Hayden's sign-off.
struct MuddiLandedCard: View {
    let tap: Tap
    let store: Store
    let now: Double
    @State private var asking = false

    var body: some View {
        let actions = MuddiTapActions.shared
        let items = tap.proofItems
        let busy = actions.busy.contains(tap.key)
        MuddiCardLabel(text: tap.phaseLabel, color: .up)
        if items.isEmpty {
            Text("No pictures in the issue yet.")
                .font(.system(size: 12.5))
                .foregroundStyle(Color.faint)
        } else {
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 8) {
                    ForEach(items) { MuddiProofThumb(item: $0) }
                }
            }
        }
        HStack(spacing: 10) {
            if actions.accepted(tap.key, now: now) {
                Text("Accepted. It shows as Done in a moment.")
                    .font(.system(size: 12.5))
                    .foregroundStyle(Color.dim)
            } else {
                if !tap.goals.isEmpty, tap.goals.allSatisfy(\.done) {
                    Text("Every goal is checked.")
                        .font(.system(size: 12.5))
                        .foregroundStyle(Color.dim)
                }
                Spacer(minLength: 8)
                MuddiCardButton(title: busy ? "Accepting…" : "Accept", busy: busy) { asking = true }
                    .alert("Accept \(tap.name)?", isPresented: $asking) {
                        Button("Cancel", role: .cancel) {}
                        Button("Accept") { actions.accept(tap, store: store) }
                    } message: {
                        Text("It closes on GitHub as done.")
                    }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

/// Filed, not flying: clear it to fly (the server adds the fly label as Hayden).
struct MuddiProposedCard: View {
    let tap: Tap
    let store: Store
    let now: Double

    var body: some View {
        let actions = MuddiTapActions.shared
        let waiting = tap.flyRequested || actions.flew(tap.key, now: now)
        MuddiCardLabel(text: "Proposed", color: .faint)
        HStack(spacing: 10) {
            Text(waiting ? "Waiting to fly." : "Not flying yet.")
                .font(.system(size: 12.5))
                .foregroundStyle(Color.ink)
            if !waiting {
                Spacer(minLength: 8)
                let busy = actions.busy.contains(tap.key)
                MuddiCardButton(title: "Fly", busy: busy) { actions.fly(tap, store: store) }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

/// Fly and Accept: an ink button in the rail's press.
struct MuddiCardButton: View {
    let title: String
    let busy: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Text(title)
                .font(.system(size: 12, weight: .semibold))
                .foregroundStyle(.white)
                .lineLimit(1)
                .padding(.horizontal, 11)
                .padding(.vertical, 7)
                .background(RoundedRectangle(cornerRadius: 10, style: .continuous).fill(Color.ink))
                .contentShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
        }
        .buttonStyle(ChatPress(scale: 0.94))
        .disabled(busy)
        .opacity(busy ? 0.55 : 1)
        .animation(.smooth(duration: 0.2), value: busy)
    }
}

/// One proof picture: 76 pt, the top of the screenshot, through the chat image
/// cache (TapSpace's proxy). A click opens the chat lightbox; one that does not
/// load hides itself.
struct MuddiProofThumb: View {
    let item: MuddiProofItem
    @State private var image: NSImage?
    @State private var failed = false
    @State private var frame: CGRect = .zero

    var body: some View {
        if !failed {
            let img = image ?? ChatMedia.shared.cached(item.path)
            let shape = RoundedRectangle(cornerRadius: 10, style: .continuous)
            ZStack(alignment: .top) {
                shape.fill(Color.well)
                if let img {
                    Image(nsImage: img)
                        .resizable()
                        .interpolation(.high)
                        .aspectRatio(contentMode: .fill)
                        .frame(width: 76, height: 76, alignment: .top)
                }
            }
            .frame(width: 76, height: 76)
            .clipShape(shape)
            .contentShape(shape)
            .onGeometryChange(for: CGRect.self, of: { $0.frame(in: .named("chat")) }) { frame = $0 }
            .pointerStyle(.zoomIn)
            .onTapGesture {
                if let img { ChatMedia.shared.lightbox.open(item.path, image: img, from: frame) }
            }
            .help(item.alt)
            .accessibilityLabel(item.alt.isEmpty ? "Proof" : item.alt)
            .task(id: item.path) {
                guard image == nil else { return }
                if let hit = ChatMedia.shared.cached(item.path) { image = hit; return }
                if let loaded = await ChatMedia.shared.load(item.path) {
                    withAnimation(.smooth(duration: 0.25)) { image = loaded }
                } else {
                    failed = true
                }
            }
        }
    }
}
