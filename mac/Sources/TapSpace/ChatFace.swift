import AppKit
import QuartzCore
import SwiftUI

// Faces in the Chat tab (R7). A master is the orb with a face (Glint's FaceArt, Glint/Design/Kit/Profile.swift, on
// its 88-unit grid): the server's `look` is one tone and one mark (FaceLook, the same table on Glint and the web
// page), its state is the expression. A tap keeps its phase ring with its repo's letter (the check once done); a
// project is a squircle letter. One resolver takes any face key: live data when the store has it, a card's
// snapshot otherwise.

enum ShellFace {
    enum Mark: Int { case plain, glasses, antenna, bowtie, cheeks, brows }

    /// A look's tone (0 accent, 1 ink, 2 tint) and mark; anything outside the table is the lead's.
    static func look(_ n: Int) -> (tone: Int, mark: Mark) {
        let f = FaceLook(n)
        return (f.tone, Mark(rawValue: f.mark) ?? .plain)
    }

    /// The asleep disc (the orb's grey).
    static let asleep = NSColor(srgbRed: 0xd1 / 255, green: 0xd1 / 255, blue: 0xd6 / 255, alpha: 1)
    /// The web page's `rgba(var(--hl), .12)` on white, opaque, so a pulse never shows the glow layer behind it.
    static let tint = NSColor(srgbRed: 0xe0 / 255, green: 0xef / 255, blue: 1, alpha: 1)

    /// The disc and its features' ink: blue and white, ink and white, the tint and blue; asleep, grey and white
    /// (the mark stays, so a sleeping master is still itself).
    static func colors(tone: Int, away: Bool) -> (fill: NSColor, ink: NSColor) {
        if away { return (asleep, .white) }
        switch tone {
        case 1: return (Palette.ink, .white)
        case 2: return (tint, Palette.blue)
        default: return (Palette.blue, .white)
        }
    }

    /// Filled, 88 units.
    static func eyes(_ e: FaceExpression) -> CGPath {
        let p = CGMutablePath()
        switch e {
        case .happy, .focused:
            p.addEllipse(in: CGRect(x: 25, y: 33, width: 8, height: 8))
            p.addEllipse(in: CGRect(x: 55, y: 33, width: 8, height: 8))
        case .wow:
            p.addEllipse(in: CGRect(x: 23, y: 32, width: 10, height: 10))
            p.addEllipse(in: CGRect(x: 55, y: 32, width: 10, height: 10))
        case .sleepy:
            p.addRoundedRect(in: CGRect(x: 23, y: 35.5, width: 10, height: 3), cornerWidth: 1.5, cornerHeight: 1.5)
            p.addRoundedRect(in: CGRect(x: 55, y: 35.5, width: 10, height: 3), cornerWidth: 1.5, cornerHeight: 1.5)
        }
        return p
    }

    /// Stroked, 88 units.
    static func mouth(_ e: FaceExpression) -> CGPath {
        let p = CGMutablePath()
        switch e {
        case .happy:
            p.move(to: CGPoint(x: 32, y: 53))
            p.addQuadCurve(to: CGPoint(x: 56, y: 53), control: CGPoint(x: 44, y: 67))
        case .focused:
            p.move(to: CGPoint(x: 35.6, y: 56))
            p.addLine(to: CGPoint(x: 52.4, y: 56))
        case .wow:
            p.addEllipse(in: CGRect(x: 39, y: 52, width: 10, height: 10))
        case .sleepy:
            p.move(to: CGPoint(x: 40.4, y: 53.5))
            p.addLine(to: CGPoint(x: 47.6, y: 53.5))
        }
        return p
    }

    /// The mark's stroked part, 88 units.
    static func lines(_ m: Mark) -> CGPath {
        let p = CGMutablePath()
        switch m {
        case .glasses:
            p.addEllipse(in: CGRect(x: 20, y: 28, width: 18, height: 18))
            p.addEllipse(in: CGRect(x: 50, y: 28, width: 18, height: 18))
            p.move(to: CGPoint(x: 37.5, y: 34))
            p.addQuadCurve(to: CGPoint(x: 50.5, y: 34), control: CGPoint(x: 44, y: 30.5))
        case .antenna:
            p.move(to: CGPoint(x: 44, y: 25))
            p.addLine(to: CGPoint(x: 44, y: 17))
        case .bowtie:
            p.addPath(bow)
        case .brows:
            p.move(to: CGPoint(x: 24, y: 25))
            p.addLine(to: CGPoint(x: 34, y: 25))
            p.move(to: CGPoint(x: 54, y: 25))
            p.addLine(to: CGPoint(x: 64, y: 25))
        case .plain, .cheeks:
            break
        }
        return p
    }

    /// The mark's filled part, 88 units.
    static func dots(_ m: Mark) -> CGPath {
        let p = CGMutablePath()
        switch m {
        case .antenna: p.addEllipse(in: CGRect(x: 39.5, y: 8.5, width: 9, height: 9))
        case .bowtie: p.addPath(bow)
        case .cheeks:
            p.addEllipse(in: CGRect(x: 15, y: 48.4, width: 8, height: 5.2))
            p.addEllipse(in: CGRect(x: 65, y: 48.4, width: 8, height: 5.2))
        case .plain, .glasses, .brows:
            break
        }
        return p
    }

    /// The bowtie's two wings: stroked and filled, so their corners round off.
    private static let bow: CGPath = {
        let p = CGMutablePath()
        p.addLines(between: [CGPoint(x: 36.5, y: 70), CGPoint(x: 44, y: 73.5), CGPoint(x: 36.5, y: 77)])
        p.closeSubpath()
        p.addLines(between: [CGPoint(x: 51.5, y: 70), CGPoint(x: 44, y: 73.5), CGPoint(x: 51.5, y: 77)])
        p.closeSubpath()
        return p
    }()

    /// An 88-unit path at `side` points.
    static func scaled(_ p: CGPath, _ side: CGFloat) -> CGPath {
        var t = CGAffineTransform(scaleX: side / 88, y: side / 88)
        return p.copy(using: &t) ?? p
    }

    /// Glint's expression swap on the Mac's pop spring: the eyes and mouth come in from 0.8 and clear (never from
    /// scale 0); a 0.2 s fade under Reduce Motion.
    static func pop() -> CAAnimation {
        if NSWorkspace.shared.accessibilityDisplayShouldReduceMotion {
            let o = CABasicAnimation(keyPath: "opacity")
            o.fromValue = 0
            o.toValue = 1
            o.duration = 0.2
            return o
        }
        func spring(_ key: String, from: Double) -> CASpringAnimation {
            let s = CASpringAnimation(keyPath: key)
            s.fromValue = from
            s.toValue = 1
            s.mass = 1
            s.stiffness = SpringPreset.pop.stiffness
            s.damping = SpringPreset.pop.damping
            s.duration = s.settlingDuration
            return s
        }
        let parts = [spring("transform.scale", from: 0.8), spring("opacity", from: 0)]
        let g = CAAnimationGroup()
        g.animations = parts
        g.duration = parts.map(\.duration).max() ?? 0
        return g
    }

    /// A repo's or project's letter, the server's rule (FaceKey.letter): "SnappStats/snapp-ios" -> "S".
    static func initial(_ name: String) -> String { FaceKey.letter(name) }
}

/// A project's face: its letter on a blue squircle (Glint's `AvatarArt(.squircle)`, radius size x 0.32).
struct ProjectFace: View {
    let initial: String
    var size: CGFloat = 38

    var body: some View {
        RoundedRectangle(cornerRadius: size * 0.32, style: .continuous)
            .fill(Color.tapBlue)
            .overlay { Text(verbatim: initial).font(.system(size: size * 0.45, weight: .heavy)).foregroundStyle(.white) }
            .frame(width: size, height: size)
    }
}

/// Any thread key's face (a card's subject, the fleet strip, the lead's activity line): a master's orb by its look
/// and state, a tap's ring with its repo letter, a project's squircle letter. Live data when the store has the
/// key; `info` (or `look` and `initial` alone) is a card's snapshot for a key it no longer holds; `state` and
/// `phase` override the store (an item's change beat). `animated: false` holds the face still (inside a card,
/// nothing loops). Hidden from VoiceOver: rows speak their titles.
struct FaceView: View {
    let key: String
    var size: CGFloat = 38
    var info: CardFaceInfo? = nil
    var look: Int? = nil
    var initial: String? = nil
    var state: String? = nil
    var phase: TapPhase? = nil
    var animated = true
    var store: Store = .shared

    var body: some View {
        Group {
            switch FaceKey.kind(key) {
            case .master:
                let a = store.agent(key)
                OrbView(state: state ?? a?.state ?? "away", look: a?.look ?? look ?? info?.look, animated: animated)
                    .frame(width: size, height: size)
            case .project:
                ProjectFace(initial: letter(served: nil), size: size)
            case .tap:
                let t = store.tap(key)
                // A tap the store no longer holds draws its snapshot's phase, else done (Glint's resolver).
                let p = phase ?? t?.tapPhase ?? info?.phase.flatMap(TapPhase.init(rawValue:)) ?? (t == nil ? .done : nil)
                // The repo's letter in the ring until the check takes its place (TapAvatar's one rule); an older
                // server's look (no phase) keeps today's avatar.
                TapAvatar(status: t?.status ?? "", working: animated && t?.replying == true, size: size, phase: p,
                          initial: p == nil ? "" : letter(served: t?.initial), still: !animated)
            }
        }
        .accessibilityHidden(true)
    }

    /// The served letter, else the snapshot's, else the key's own (the server's rule).
    private func letter(served: String?) -> String {
        [served, initial, info?.initial].compactMap { $0 }.first { !$0.isEmpty } ?? FaceKey.initial(key)
    }

    /// What Hayden sees for a face key: the live title, else the card snapshot's, else a plain name (never a
    /// NAME or a number).
    static func title(_ key: String, info: CardFaceInfo?, store: Store) -> String {
        let snap = info.flatMap { $0.title.isEmpty ? nil : $0.title }
        switch FaceKey.kind(key) {
        case .master:
            return store.agent(key)?.title ?? snap ?? "Claude"
        case .tap:
            let repo = key.split(separator: "#").first?.split(separator: "/").last.map(String.init) ?? ""
            return store.tap(key)?.name ?? snap ?? repo
        case .project:
            return snap ?? (String(key.dropFirst(8)).split(separator: "/").last.map(String.init) ?? "")
        }
    }
}
