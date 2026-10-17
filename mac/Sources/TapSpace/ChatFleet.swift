import AppKit
import SwiftUI

// Muddi on top (CONTRACT 5, 7): under the lead's rail row, everything running under it as small live faces and the
// one disclosure that folds every other row away; above the lead's chat, one slim line saying what it is doing now.
// The server computes both (`fleet`, the lead row's `activity`); these views only draw them.

/// Under the lead: the fleet's faces (a master's bead rings its progress, a tap's bead is its phase ring) and the
/// disclosure. Working faces move with their own loops (the thinking orb, the flying arc, the needs-you breathing).
/// A bead opens its thread and leaves the group as it is; the selection then parks on this strip.
struct ChatFleetStrip: View {
    let fleet: [FleetEntry]
    /// The lead's title, for the disclosure's help.
    let lead: String
    let open: Bool
    /// A reply or a note waits in a row folded away.
    let unread: Bool
    let store: Store
    let toggle: () -> Void
    /// The beads' room: what the rail gives them beside the disclosure (never what they ask for).
    @State private var room: CGFloat = 0

    /// At most this many faces; fewer when they do not fit beside the disclosure.
    static let beads = 8
    static let bead: CGFloat = 24, gap: CGFloat = 6, disclosure: CGFloat = 36, more: CGFloat = 24

    /// How many of `count` beads fit in `room` (with "+N" when some are left out), at most `beads`. 0 room (not
    /// measured yet): as many as `beads` allows.
    static func fit(_ room: CGFloat, count: Int) -> Int {
        guard room > 0 else { return min(count, beads) }
        let n = { (w: CGFloat) in max(0, Int((w + gap) / (bead + gap))) }
        if count <= min(beads, n(room)) { return count }
        return max(1, min(beads, n(room - more - gap)))
    }

    var body: some View {
        let n = min(fleet.count, Self.fit(room, count: fleet.count))
        HStack(spacing: Self.gap) {
            HStack(spacing: Self.gap) {
                if fleet.isEmpty {
                    Text("Nothing running")
                        .font(.system(size: 11.5))
                        .foregroundStyle(Color.faint)
                        .transition(.opacity)
                }
                ForEach(fleet.prefix(n)) { e in
                    ChatFleetBead(entry: e) { store.open(e.key) }
                        .transition(.scale(scale: 0.8).combined(with: .opacity))
                }
                if fleet.count > n {
                    Text("+\(fleet.count - n)")
                        .font(.system(size: 11))
                        .foregroundStyle(Color.faint)
                        .monospacedDigit()
                        .contentTransition(.numericText(value: Double(fleet.count - n)))
                        .frame(minWidth: Self.more, alignment: .leading)
                }
            }
            // As wide as the rail allows, whatever the beads ask for: measured, so the count fits it.
            .frame(minWidth: 0, maxWidth: .infinity, alignment: .leading)
            .clipped()
            .onGeometryChange(for: CGFloat.self, of: { $0.size.width }) { room = $0 }
            Button(action: toggle) {
                HStack(spacing: 6) {
                    UnreadDot(on: unread && !open)
                    Image(systemName: "chevron.right")
                        .font(.system(size: 10, weight: .bold))
                        .foregroundStyle(Color.dim)
                        .rotationEffect(.degrees(open ? 90 : 0))
                }
                .frame(width: Self.disclosure, height: 28)
                .contentShape(Rectangle())
            }
            .buttonStyle(ChatPress(scale: 0.9))
            .help(open ? "Fold everything under \(lead)" : "Show every agent and tap")
            .accessibilityLabel(open ? "Fold everything under \(lead)" : "Show every agent and tap")
        }
        // The orb column of the lead's row (its padding 12), so 6 beads plus "+N" fit the 300 pt rail (CONTRACT 17).
        .padding(.horizontal, 12)
        .padding(.vertical, 4)
        .frame(maxWidth: .infinity, minHeight: 36, alignment: .leading)
        .contentShape(Rectangle())
        // The rail's glide spring: the chevron turns, beads come and go.
        .animation(.spring(response: 0.5, dampingFraction: 0.82), value: fleet.prefix(n).map(\.key) + ["\(open)"])
    }
}

/// One face in the strip: a master's orb inside a thin ring of its steps done (a dot when it waits on Hayden), or a
/// tap's phase ring with its repo letter. The server's fleet row carries everything it draws.
struct ChatFleetBead: View {
    let entry: FleetEntry
    let open: () -> Void

    var body: some View {
        let e = entry
        let tap = e.kind == "tap"
        Button(action: open) {
            ZStack {
                if tap {
                    TapAvatar(status: "", working: e.working, size: ChatFleetStrip.bead, phase: TapPhase(rawValue: e.phase),
                              initial: e.initial)
                } else {
                    if e.total > 0 {
                        Circle().stroke(Color.black.opacity(0.06), lineWidth: 1.5)
                        Circle()
                            .trim(from: 0, to: min(1, Double(e.done) / Double(e.total)))
                            .stroke(e.done >= e.total ? Color.tapBlue : Color.ink,
                                    style: StrokeStyle(lineWidth: 1.5, lineCap: .round))
                            .rotationEffect(.degrees(-90))
                            .animation(SpringPreset.smooth.animation, value: e.done)
                    }
                    OrbView(state: e.state, look: e.look).frame(width: 18, height: 18)
                }
            }
            .frame(width: ChatFleetStrip.bead, height: ChatFleetStrip.bead)
            .overlay(alignment: .topTrailing) {
                if e.needsYou && !tap {
                    Circle().fill(Color.tapBlue).frame(width: 6, height: 6).transition(.scale(scale: 0.5).combined(with: .opacity))
                }
            }
            .contentShape(Circle())
        }
        .buttonStyle(ChatPress(scale: 0.9))
        .help(e.word.isEmpty ? e.title : e.title + " · " + e.word)
        .accessibilityLabel(e.word.isEmpty ? e.title : e.title + ", " + e.word)
    }
}

/// What the lead is doing right now (CONTRACT 7), one slim line at the top of its thread: its face with the
/// expression of that state, the server's word (swapped in place when it changes), and a breathing dot while it
/// works. Calm when idle, greyed when its session is gone. Hidden while the server does not know (`activity` nil).
struct LeadActivityStrip: View {
    let activity: AgentActivity
    /// The lead's face look.
    let look: Int?
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    /// The orb state whose expression each activity state wears: working focused, waiting happy, switching wow,
    /// idle sleepy, gone sleepy on grey. An unknown state keeps its word on the idle face.
    static func face(_ state: String) -> String {
        switch state {
        case "working": "thinking"
        case "waiting": "here"
        case "switching": "switching"
        case "gone": "away"
        default: "idle"
        }
    }

    var body: some View {
        let a = activity
        HStack(spacing: 10) {
            OrbView(state: Self.face(a.state), look: look)
                .frame(width: 22, height: 22)
            HStack(spacing: 6) {
                Text(a.word)
                    .font(.system(size: 12.5, weight: .medium))
                    .foregroundStyle(Color.ink)
                    .contentTransition(.interpolate)
                if !a.detail.isEmpty {
                    Text(a.detail)
                        .font(.system(size: 12))
                        .foregroundStyle(Color.dim)
                        .contentTransition(.interpolate)
                }
            }
            .lineLimit(1)
            .truncationMode(.tail)
            Spacer(minLength: 8)
            if a.state == "working" {
                BreathingDot(color: Palette.blue, period: 1.7, low: 0.45, animated: !reduceMotion)
                    .frame(width: 6, height: 6)
                    .transition(.opacity)
            }
        }
        .padding(.horizontal, 20)
        .padding(.vertical, 7)
        .opacity(a.state == "gone" ? 0.55 : 1)
        // RowText's swap: the old word blurs into the new one.
        .animation(.smooth(duration: 0.35), value: a.word + "|" + a.detail + "|" + a.state)
        .accessibilityElement(children: .combine)
    }
}
