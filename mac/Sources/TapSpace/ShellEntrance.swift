import AppKit
import SwiftUI

/// The launch entrance, as one white layer over both tabs: opaque while the
/// first state is in flight, it opens as `ready` springs to 1 and leaves the
/// tree once open. The same trade as ShellTabVeil: on the white window this
/// looks exactly like the space fading in, for one solid blend a frame,
/// where group opacity, blur or scale over the tab trees renders the whole
/// window offscreen every frame, stacked on each card's own entrance blur
/// (the cards bring the focus pull themselves).
struct ShellEntranceVeil: View {
    let ready: SpringBox

    var body: some View {
        let r = clamp01(ready.value)
        if r < 0.999 {
            Color.white
                .opacity(1 - r)
                .allowsHitTesting(false)
        }
    }
}

/// The connecting mark dissolving as the space arrives: it fades and swells
/// a touch. The effects sit on the 280 x 120 mark itself, never on a
/// window-sized frame, so the fade composites only the mark.
struct ShellEntranceMark: View {
    let ready: SpringBox
    let down: Bool

    var body: some View {
        let r = clamp01(ready.value)
        if r < 0.999 {
            ConnectingView(down: down)
                .opacity(1 - r)
                .scaleEffect(1 + 0.05 * r)
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .allowsHitTesting(false)
        }
    }
}

/// Under `--fps`, times the launch entrance frame by frame (the per-second
/// FPS line mixes it with window setup and the first data landing) and logs
/// `ENTRANCE frames=N dur=<ms> rate=<fps> worst=<ms> hitches=<gaps over 12 ms>`
/// when it settles. Without the flag it only plays the spring.
@MainActor enum ShellEntranceProbe {
    static let on = CommandLine.arguments.contains("--fps")

    static func play(_ ready: SpringBox) {
        guard on else { ready.set(1); return }
        var stamps: [CFTimeInterval] = []
        ready.onStep = { _ in stamps.append(CACurrentMediaTime()) }
        ready.set(1, then: { [weak ready] in
            ready?.onStep = nil
            guard let first = stamps.first, let last = stamps.last, stamps.count > 1 else { return }
            let gaps = zip(stamps.dropFirst(), stamps).map { ($0 - $1) * 1000 }
            let dur = (last - first) * 1000
            print("ENTRANCE frames=\(stamps.count) dur=\(Int(dur.rounded()))ms"
                + " rate=\(Int((Double(gaps.count) / (dur / 1000)).rounded()))"
                + " worst=\(Int((gaps.max() ?? 0).rounded()))ms hitches=\(gaps.filter { $0 > 12 }.count)")
            fflush(stdout)
        })
    }
}
