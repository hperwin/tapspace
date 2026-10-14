import Accelerate
import CoreGraphics
import Foundation

/// The web page's change highlighter, natively: diff the previous version's
/// real pixels against the new version's on downsampled bitmaps, then return
/// soft boxes (unit coordinates) over the regions that changed. Skips when
/// the change is most of the frame (a redesign, not a tweak).
enum PixelDiff {
    static let width = 168
    static let grid = 28              // cells across
    static let cellThreshold: Float = 14   // mean abs channel diff to call a cell changed
    static let skipFraction = 0.55

    struct Result: Equatable {
        var boxes: [CGRect]
        var aspect: CGFloat
    }

    nonisolated static func regions(_ a: CGImage, _ b: CGImage) -> Result? {
        let W = width
        let H = max(1, Int((Double(W) * Double(a.height) / Double(a.width)).rounded()))
        guard let pa = rgba(a, W, H), let pb = rgba(b, W, H) else { return nil }

        // |a - b| per channel with Accelerate.
        var fa = [Float](repeating: 0, count: pa.count)
        var fb = [Float](repeating: 0, count: pb.count)
        vDSP.convertElements(of: pa, to: &fa)
        vDSP.convertElements(of: pb, to: &fb)
        let diff = vDSP.absolute(vDSP.subtract(fa, fb))

        let cw = Int((Double(W) / Double(grid)).rounded(.up))
        let rows = Int((Double(H) / Double(cw)).rounded(.up))
        var changed = Array(repeating: Array(repeating: false, count: grid), count: rows)
        var nChanged = 0
        for gy in 0..<rows {
            for gx in 0..<grid {
                var sum: Float = 0
                var n = 0
                let y1 = min((gy + 1) * cw, H), x1 = min((gx + 1) * cw, W)
                var y = gy * cw
                while y < y1 {
                    var x = gx * cw
                    while x < x1 {
                        let i = (y * W + x) * 4
                        sum += diff[i] + diff[i + 1] + diff[i + 2]
                        n += 1
                        x += 1
                    }
                    y += 1
                }
                if n > 0 && sum / Float(n * 3) > cellThreshold {
                    changed[gy][gx] = true
                    nChanged += 1
                }
            }
        }
        guard nChanged > 0, Double(nChanged) / Double(rows * grid) <= skipFraction else { return nil }

        // Connected components (8-way) -> bounding boxes in unit coordinates.
        var seen = changed.map { $0.map { _ in false } }
        var boxes: [CGRect] = []
        for gy in 0..<rows {
            for gx in 0..<grid where changed[gy][gx] && !seen[gy][gx] {
                var minx = gx, maxx = gx, miny = gy, maxy = gy
                var stack = [(gx, gy)]
                seen[gy][gx] = true
                while let (x, y) = stack.popLast() {
                    minx = min(minx, x); maxx = max(maxx, x)
                    miny = min(miny, y); maxy = max(maxy, y)
                    for (dx, dy) in [(1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (-1, -1), (1, -1), (-1, 1)] {
                        let nx = x + dx, ny = y + dy
                        if ny >= 0, ny < rows, nx >= 0, nx < grid, changed[ny][nx], !seen[ny][nx] {
                            seen[ny][nx] = true
                            stack.append((nx, ny))
                        }
                    }
                }
                let x0 = Double(minx * cw) / Double(W)
                let y0 = Double(miny * cw) / Double(H)
                let x1 = Double(min((maxx + 1) * cw, W)) / Double(W)
                let y1 = Double(min((maxy + 1) * cw, H)) / Double(H)
                boxes.append(CGRect(x: x0, y: y0, width: x1 - x0, height: y1 - y0))
            }
        }
        return Result(boxes: Array(boxes.prefix(12)), aspect: CGFloat(a.width) / CGFloat(a.height))
    }

    /// Top row first, RGBA8.
    nonisolated private static func rgba(_ img: CGImage, _ w: Int, _ h: Int) -> [UInt8]? {
        var buf = [UInt8](repeating: 0, count: w * h * 4)
        let ok = buf.withUnsafeMutableBytes { raw -> Bool in
            guard let ctx = CGContext(data: raw.baseAddress, width: w, height: h, bitsPerComponent: 8,
                                      bytesPerRow: w * 4, space: CGColorSpaceCreateDeviceRGB(),
                                      bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else { return false }
            ctx.interpolationQuality = .medium
            ctx.draw(img, in: CGRect(x: 0, y: 0, width: w, height: h))
            return true
        }
        return ok ? buf : nil
    }
}
