// Renders the TapSpace app icon: a glossy white liquid drop landing on a soft
// blue gradient, its tap rippling out in two glassy rings, with a small
// droplet pulled off it on a metaball neck (the same liquid as the pill).
//
//   swift Scripts/make-icon.swift <out.png> [size]
//
// install.sh turns the 1024 PNG into Resources/TapSpace.icns with sips + iconutil.
import AppKit
import CoreGraphics

let args = CommandLine.arguments
guard args.count >= 2 else {
    FileHandle.standardError.write("usage: make-icon.swift <out.png> [size]\n".data(using: .utf8)!)
    exit(2)
}
let out = URL(fileURLWithPath: args[1])
let S = CGFloat(Double(args.count > 2 ? args[2] : "1024") ?? 1024)
let k = S / 1024   // everything below is authored on a 1024 grid

func rgb(_ hex: UInt32, _ a: CGFloat = 1) -> CGColor {
    CGColor(srgbRed: CGFloat((hex >> 16) & 0xff) / 255, green: CGFloat((hex >> 8) & 0xff) / 255,
            blue: CGFloat(hex & 0xff) / 255, alpha: a)
}

let space = CGColorSpace(name: CGColorSpace.sRGB)!
guard let ctx = CGContext(data: nil, width: Int(S), height: Int(S), bitsPerComponent: 8, bytesPerRow: 0,
                          space: space, bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else { exit(1) }
ctx.scaleBy(x: k, y: k)
ctx.interpolationQuality = .high
ctx.setShouldAntialias(true)

func gradient(_ stops: [(CGFloat, CGColor)]) -> CGGradient {
    CGGradient(colorsSpace: space, colors: stops.map(\.1) as CFArray, locations: stops.map(\.0))!
}

// MARK: Squircle body (Apple's macOS grid: 824 pt body, 100 pt margin)

/// A continuous-curvature superellipse, like the system's app icon shape.
func squircle(_ r: CGRect, n: CGFloat = 4.4) -> CGPath {
    let p = CGMutablePath()
    let a = r.width / 2, b = r.height / 2
    let cx = r.midX, cy = r.midY
    let steps = 720
    for i in 0...steps {
        let t = CGFloat(i) / CGFloat(steps) * 2 * .pi
        let c = cos(t), s = sin(t)
        let x = cx + a * (c < 0 ? -1 : 1) * pow(abs(c), 2 / n)
        let y = cy + b * (s < 0 ? -1 : 1) * pow(abs(s), 2 / n)
        if i == 0 { p.move(to: CGPoint(x: x, y: y)) } else { p.addLine(to: CGPoint(x: x, y: y)) }
    }
    p.closeSubpath()
    return p
}

let body = CGRect(x: 100, y: 100, width: 824, height: 824)
let bodyPath = squircle(body)

// Drop shadow under the whole tile.
ctx.saveGState()
ctx.setShadow(offset: CGSize(width: 0, height: -12), blur: 28, color: rgb(0x0b2a55, 0.30))
ctx.addPath(bodyPath)
ctx.setFillColor(rgb(0xdfeafb))
ctx.fillPath()
ctx.restoreGState()

ctx.saveGState()
ctx.addPath(bodyPath)
ctx.clip()

// Soft sky gradient, lighter toward the top like light on water.
ctx.drawLinearGradient(gradient([(0, rgb(0xf4f8ff)), (0.5, rgb(0xd3e3fb)), (1, rgb(0x97bcef))]),
                       start: CGPoint(x: 360, y: 924), end: CGPoint(x: 660, y: 100),
                       options: [.drawsBeforeStartLocation, .drawsAfterEndLocation])
// A pool of light under the drop.
let center = CGPoint(x: 512, y: 540)
ctx.drawRadialGradient(gradient([(0, rgb(0xffffff, 0.85)), (0.55, rgb(0xffffff, 0.25)), (1, rgb(0xffffff, 0))]),
                       startCenter: center, startRadius: 0, endCenter: center, endRadius: 400, options: [])

// MARK: Ripples (a tap on water: two glassy rings, the outer one fainter)

func ring(radius: CGFloat, width: CGFloat, alpha: CGFloat, squash: CGFloat) {
    let rect = CGRect(x: center.x - radius, y: center.y - radius * squash - 26,
                      width: radius * 2, height: radius * 2 * squash)
    // Soft blue shadow below the ring.
    ctx.saveGState()
    ctx.setShadow(offset: CGSize(width: 0, height: -10), blur: 22, color: rgb(0x2b5ea8, 0.22 * alpha))
    ctx.setLineWidth(width)
    ctx.setStrokeColor(rgb(0xffffff, 0.92 * alpha))
    ctx.strokeEllipse(in: rect)
    ctx.restoreGState()
    // Glass: a bright crest along the top of the ring.
    ctx.saveGState()
    ctx.addEllipse(in: rect)
    ctx.setLineWidth(width)
    ctx.replacePathWithStrokedPath()
    ctx.clip()
    ctx.drawLinearGradient(gradient([(0, rgb(0xffffff, alpha)), (0.5, rgb(0xffffff, 0.55 * alpha)), (1, rgb(0xc9dcf6, 0.6 * alpha))]),
                           start: CGPoint(x: 0, y: rect.maxY + width), end: CGPoint(x: 0, y: rect.minY - width), options: [])
    ctx.restoreGState()
}

ring(radius: 330, width: 22, alpha: 0.55, squash: 0.80)
ring(radius: 228, width: 30, alpha: 0.9, squash: 0.80)

// MARK: The drop (a glossy white sphere) and its satellite droplet

let dropR: CGFloat = 132
let dropC = CGPoint(x: center.x, y: center.y + 14)
let satR: CGFloat = 40
let satC = CGPoint(x: dropC.x + 142, y: dropC.y + 104)

/// Tangent-bezier bridge between two circles (the pill's metaball neck).
func neck(_ c1: CGPoint, _ r1: CGFloat, _ c2: CGPoint, _ r2: CGFloat, spread v: CGFloat = 0.62, handle: CGFloat = 2.4) -> CGPath? {
    let d = hypot(c2.x - c1.x, c2.y - c1.y)
    guard d > abs(r1 - r2), d <= r1 + r2 * 2.6 else { return nil }
    var u1: CGFloat = 0, u2: CGFloat = 0
    if d < r1 + r2 {
        u1 = acos(min(max((r1 * r1 + d * d - r2 * r2) / (2 * r1 * d), -1), 1))
        u2 = acos(min(max((r2 * r2 + d * d - r1 * r1) / (2 * r2 * d), -1), 1))
    }
    let between = atan2(c2.y - c1.y, c2.x - c1.x)
    let maxSpread = acos(min(max((r1 - r2) / d, -1), 1))
    let a1 = between + u1 + (maxSpread - u1) * v
    let a2 = between - u1 - (maxSpread - u1) * v
    let a3 = between + .pi - u2 - (.pi - u2 - maxSpread) * v
    let a4 = between - .pi + u2 + (.pi - u2 - maxSpread) * v
    func polar(_ c: CGPoint, _ a: CGFloat, _ r: CGFloat) -> CGPoint { CGPoint(x: c.x + cos(a) * r, y: c.y + sin(a) * r) }
    let p1 = polar(c1, a1, r1), p2 = polar(c1, a2, r1), p3 = polar(c2, a3, r2), p4 = polar(c2, a4, r2)
    let total = r1 + r2
    let d2 = min(v * handle, hypot(p3.x - p1.x, p3.y - p1.y) / total) * min(1, d * 2 / total)
    let p = CGMutablePath()
    p.move(to: p1)
    p.addCurve(to: p3, control1: polar(p1, a1 - .pi / 2, r1 * d2), control2: polar(p3, a3 + .pi / 2, r2 * d2))
    p.addLine(to: p4)
    p.addCurve(to: p2, control1: polar(p4, a4 - .pi / 2, r2 * d2), control2: polar(p2, a2 + .pi / 2, r1 * d2))
    p.closeSubpath()
    return p
}

// One outline: drop, droplet and neck unioned (no inner seams to shade or stroke).
var liquid: CGPath = CGPath(ellipseIn: CGRect(x: dropC.x - dropR, y: dropC.y - dropR, width: dropR * 2, height: dropR * 2), transform: nil)
liquid = liquid.union(CGPath(ellipseIn: CGRect(x: satC.x - satR, y: satC.y - satR, width: satR * 2, height: satR * 2), transform: nil))
if let n = neck(dropC, dropR, satC, satR) { liquid = liquid.union(n) }

// Contact shadow on the water: a soft squashed radial falloff, no hard edge.
ctx.saveGState()
ctx.translateBy(x: dropC.x + 8, y: dropC.y - dropR - 8)
ctx.scaleBy(x: 1, y: 0.26)
ctx.drawRadialGradient(gradient([(0, rgb(0x123c78, 0.30)), (0.6, rgb(0x123c78, 0.10)), (1, rgb(0x123c78, 0))]),
                       startCenter: .zero, startRadius: 0, endCenter: .zero, endRadius: 170, options: [])
ctx.restoreGState()

ctx.saveGState()
ctx.setShadow(offset: CGSize(width: 0, height: -22), blur: 44, color: rgb(0x123c78, 0.30))
ctx.addPath(liquid)
ctx.setFillColor(rgb(0xffffff))
ctx.fillPath(using: .winding)
ctx.restoreGState()

// One shading pass over the whole liquid (drop, neck and droplet) so it reads
// as a single body of water: inner shadows darken the rim away from the light.
let everywhere = CGMutablePath()
everywhere.addRect(CGRect(x: -200, y: -200, width: 1424, height: 1424))
everywhere.addPath(liquid)
func innerShadow(_ offset: CGSize, _ blur: CGFloat, _ color: CGColor) {
    ctx.saveGState()
    ctx.addPath(liquid)
    ctx.clip(using: .winding)
    ctx.setShadow(offset: offset, blur: blur, color: color)
    ctx.addPath(everywhere)
    ctx.setFillColor(rgb(0x000000))
    ctx.fillPath(using: .evenOdd)
    ctx.restoreGState()
}
innerShadow(.zero, 70, rgb(0xa9c2e6, 0.55))
innerShadow(CGSize(width: -18, height: 24), 46, rgb(0x6f93c9, 0.42))
// Light passes through the drop and pools at the bottom rim (a caustic).
ctx.saveGState()
ctx.addPath(liquid)
ctx.clip(using: .winding)
let caustic = CGPoint(x: dropC.x + 34, y: dropC.y - dropR + 30)
ctx.drawRadialGradient(gradient([(0, rgb(0xffffff, 0.85)), (1, rgb(0xffffff, 0))]),
                       startCenter: caustic, startRadius: 0, endCenter: caustic, endRadius: 70, options: [])
ctx.restoreGState()

// Specular highlight: a soft window reflection, top left.
func highlight(_ rect: CGRect, _ alpha: CGFloat) {
    ctx.saveGState()
    ctx.addEllipse(in: rect)
    ctx.clip()
    ctx.drawLinearGradient(gradient([(0, rgb(0xffffff, alpha)), (1, rgb(0xffffff, 0))]),
                           start: CGPoint(x: rect.midX, y: rect.maxY), end: CGPoint(x: rect.midX, y: rect.minY), options: [])
    ctx.restoreGState()
}
ctx.saveGState()
ctx.translateBy(x: dropC.x - 50, y: dropC.y + 62)
ctx.rotate(by: 0.62)
highlight(CGRect(x: -64, y: -27, width: 128, height: 54), 1)
ctx.restoreGState()
highlight(CGRect(x: satC.x - 22, y: satC.y + 4, width: 28, height: 18), 0.95)

// A crisp hairline rim so the white reads on white.
ctx.saveGState()
ctx.addPath(liquid)
ctx.setLineWidth(1.6)
ctx.setStrokeColor(rgb(0x7f9cc7, 0.16))
ctx.strokePath()
ctx.restoreGState()

ctx.restoreGState() // body clip

// Inner edge light on the tile.
ctx.saveGState()
ctx.addPath(bodyPath)
ctx.setLineWidth(3)
ctx.setStrokeColor(rgb(0xffffff, 0.55))
ctx.strokePath()
ctx.restoreGState()

guard let img = ctx.makeImage() else { exit(1) }
let rep = NSBitmapImageRep(cgImage: img)
guard let png = rep.representation(using: .png, properties: [:]) else { exit(1) }
try png.write(to: out)
print("wrote \(out.path) \(Int(S))x\(Int(S))")
