import AppKit
import QuartzCore
import SwiftUI

/// The pill's white liquid body (pill, bulge droplet, card) and the hovered
/// row's highlight, as Core Animation shape layers. The pill's display link
/// hands them their paths each frame (`render`), so a frame of the spread
/// costs two path assignments: the shadow is drawn by the render server from
/// `shadowPath`, and no full-window bitmap is filled or blurred on the main
/// thread the way a SwiftUI Canvas would.
@MainActor
final class PillSurfaceView: NSView {
    /// Top-left-origin space, matching PillLiquid's window-local coordinates.
    private let root = CALayer()
    private let body = CAShapeLayer()
    private let rim = CAShapeLayer()
    private let glow = CAShapeLayer()
    private let glowClip = CAShapeLayer()
    private var lastExpansion = -1.0

    override init(frame: NSRect) {
        super.init(frame: frame)
        wantsLayer = true
        layer?.masksToBounds = false
        root.isGeometryFlipped = true
        root.masksToBounds = false
        layer?.addSublayer(root)

        body.fillColor = NSColor.white.cgColor
        body.shadowColor = NSColor.black.cgColor
        rim.fillColor = nil
        rim.strokeColor = NSColor.black.withAlphaComponent(0.07).cgColor
        rim.lineWidth = 0.5
        glow.fillColor = NSColor.black.withAlphaComponent(0.05).cgColor
        glow.mask = glowClip
        for l in [body, glow, rim] {
            l.actions = Self.still
            root.addSublayer(l)
        }
        glowClip.actions = Self.still
        root.actions = Self.still
    }

    required init?(coder: NSCoder) { fatalError() }

    private static let still: [String: CAAction] = [
        "path": NSNull(), "shadowPath": NSNull(), "shadowOpacity": NSNull(), "shadowRadius": NSNull(),
        "shadowOffset": NSNull(), "opacity": NSNull(), "hidden": NSNull(), "bounds": NSNull(), "position": NSNull(),
        "fillColor": NSNull(),
    ]

    override func hitTest(_ point: NSPoint) -> NSView? { nil }

    override func layout() {
        super.layout()
        CATransaction.begin()
        CATransaction.setDisableActions(true)
        for l in [root, body, rim, glow, glowClip] { l.frame = l === root ? bounds : root.bounds }
        CATransaction.commit()
    }

    /// One frame: the body where the liquid is, the highlight where the row
    /// track is (clipped to the body, faded with the card's contents).
    func render(liquid: PillLiquid, highlight: PillHighlight) {
        CATransaction.begin()
        CATransaction.setDisableActions(true)
        let path = liquid.bodyPath.cgPath
        body.path = path
        body.shadowPath = path
        rim.path = path

        // The shadow deepens and lifts as the pill becomes a card.
        let e = liquid.expansion
        if e != lastExpansion {
            lastExpansion = e
            body.shadowOpacity = Float(lerp(0.16, 0.26, e))
            body.shadowRadius = lerp(1.5, 18, e) / 2
            // shadowOffset ignores the flipped geometry: negative is down.
            body.shadowOffset = CGSize(width: 0, height: -lerp(0.5, 9, e))
        }

        let a = highlight.alpha.value * liquid.detailOpacity
        if a > 0.01 {
            glow.isHidden = false
            glow.opacity = Float(a)
            glow.path = highlight.body.path.cgPath
            glowClip.path = CGPath(roundedRect: liquid.shapeRect, cornerWidth: liquid.cornerRadius,
                                   cornerHeight: liquid.cornerRadius, transform: nil)
        } else if !glow.isHidden {
            glow.isHidden = true
        }
        CATransaction.commit()
    }
}

/// Puts the controller-owned surface view into the SwiftUI tree. It reads
/// nothing observable, so the pill's frames never re-run SwiftUI for it.
struct PillSurfaceHost: NSViewRepresentable {
    let view: PillSurfaceView
    func makeNSView(context: Context) -> PillSurfaceView { view }
    func updateNSView(_ v: PillSurfaceView, context: Context) {}
}

// MARK: - Row thumbnails

/// Card thumbnails at the size the row draws them (22 pt, cropped square),
/// made once off the main thread. Rows exist (hidden) while the pill is
/// collapsed, so these are ready before any spread starts, and a spread
/// frame never resamples a full-size screenshot into a 22 pt square.
@MainActor
final class PillThumbs {
    static let shared = PillThumbs()
    private var cache: [String: NSImage] = [:]
    private var order: [String] = []
    static let points: CGFloat = 22
    static let pixels = 66   // 3x: sharp on 2x screens with room for the fill crop

    func cached(_ path: String) -> NSImage? { cache[path] }

    func thumb(_ path: String, isVideo: Bool) async -> NSImage? {
        if let hit = cache[path] { return hit }
        let source: NSImage?
        if isVideo {
            source = await PillPoster.shared.frame(path)
        } else if let hit = Images.shared.cached(path, maxPixel: 1600) {
            // Same size key as the board, so the board's decode is reused.
            source = hit
        } else {
            source = await Images.shared.load(path, maxPixel: 1600)
        }
        guard let cg = source?.cgImage(forProposedRect: nil, context: nil, hints: nil) else { return nil }
        let px = Self.pixels
        let small = await Task.detached(priority: .utility) { pillSquareFill(cg, px) }.value
        guard let small else { return nil }
        let img = NSImage(cgImage: small, size: NSSize(width: Self.points, height: Self.points))
        cache[path] = img
        order.append(path)
        if order.count > 48 { cache[order.removeFirst()] = nil }
        return img
    }
}

/// Center-crops `src` to a square and scales it to `px` pixels.
nonisolated func pillSquareFill(_ src: CGImage, _ px: Int) -> CGImage? {
    let w = CGFloat(src.width), h = CGFloat(src.height)
    guard w > 0, h > 0,
          let ctx = CGContext(data: nil, width: px, height: px, bitsPerComponent: 8, bytesPerRow: 0,
                              space: CGColorSpace(name: CGColorSpace.sRGB)!,
                              bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else { return nil }
    ctx.interpolationQuality = .high
    let k = CGFloat(px) / min(w, h)
    let dw = w * k, dh = h * k
    ctx.draw(src, in: CGRect(x: (CGFloat(px) - dw) / 2, y: (CGFloat(px) - dh) / 2, width: dw, height: dh))
    return ctx.makeImage()
}
