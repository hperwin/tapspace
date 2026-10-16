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
