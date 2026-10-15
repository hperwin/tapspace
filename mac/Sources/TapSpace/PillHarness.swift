import AppKit

/// Verification helpers for the pill (the harness lane's --snapshot and
/// --demo call these). A pose pins a fake cursor and settles the liquid
/// where that cursor would leave it; the snapshot renders the real view
/// hierarchy of the panel at backing scale.
extension PillController {
    enum Pose: String, CaseIterable {
        case collapsed, bulging, expanded
    }

    /// The window-local cursor that produces a pose (nil = no cursor).
    func cursor(for pose: Pose, hoverRow: Int? = nil) -> CGPoint? {
        let top = liquid.top
        let pill = CGRect(x: liquid.anchorLeft, y: top, width: liquid.shapeRect.width, height: PillLiquid.pillH)
        switch pose {
        case .collapsed:
            return nil
        case .bulging:
            // Below and a little right of the pill: the droplet reaches down toward it.
            return CGPoint(x: pill.minX + min(pill.width - 14, 40), y: pill.maxY + 26)
        case .expanded:
            if let i = hoverRow, let r = highlight.rows[i] { return CGPoint(x: r.midX, y: r.midY) }
            return CGPoint(x: pill.minX + 20, y: top + 12)
        }
    }

    /// Pins the cursor for `pose` and jumps the liquid (and the row
    /// highlight) straight to rest there. Call twice across a layout pass
    /// for `.expanded` with a hovered row, since row frames come from layout.
    func pose(_ pose: Pose, hoverRow: Int? = nil) {
        let m = cursor(for: pose, hoverRow: hoverRow)
        simMouse = { m }
        liquid.settle(mouse: m, expanded: pose == .expanded)
        highlight.track(mouse: m, liquid: liquid)
        highlight.finish()
        motion.activate(self)
    }

    /// Lets go of a pinned cursor; the real one drives the pill again.
    func releasePose() {
        simMouse = nil
        motion.activate(self)
    }

    /// The panel's view hierarchy as PNG data at backing scale (2x on Retina).
    /// `backdrop` paints a menu-bar-like color behind the transparent panel.
    func snapshotPNG(backdrop: NSColor? = nil) -> Data? {
        guard let view = panel?.contentView else { return nil }
        view.layoutSubtreeIfNeeded()
        let bounds = view.bounds
        guard let rep = view.bitmapImageRepForCachingDisplay(in: bounds) else { return nil }
        view.cacheDisplay(in: bounds, to: rep)
        guard let backdrop else { return rep.representation(using: .png, properties: [:]) }
        guard let cg = rep.cgImage,
              let ctx = CGContext(data: nil, width: cg.width, height: cg.height, bitsPerComponent: 8, bytesPerRow: 0,
                                  space: CGColorSpace(name: CGColorSpace.sRGB)!,
                                  bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else { return nil }
        let full = CGRect(x: 0, y: 0, width: cg.width, height: cg.height)
        ctx.setFillColor(backdrop.cgColor)
        ctx.fill(full)
        ctx.draw(cg, in: full)
        guard let flat = ctx.makeImage() else { return nil }
        return NSBitmapImageRep(cgImage: flat).representation(using: .png, properties: [:])
    }

    /// Poses, waits for SwiftUI to lay the pose out, settles again, and
    /// writes `pill-<pose>.png` into `dir`.
    func snapshot(_ pose: Pose, hoverRow: Int? = nil, to dir: URL, backdrop: NSColor? = nil) async throws {
        self.pose(pose, hoverRow: hoverRow)
        try? await Task.sleep(for: .milliseconds(250))
        self.pose(pose, hoverRow: hoverRow)
        try? await Task.sleep(for: .milliseconds(150))
        guard let png = snapshotPNG(backdrop: backdrop) else { return }
        let name = hoverRow == nil ? "pill-\(pose.rawValue).png" : "pill-\(pose.rawValue)-hover.png"
        try png.write(to: dir.appending(path: name))
    }
}
