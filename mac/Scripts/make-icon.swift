// Renders the TapSpace app icon: a white tile with one flat blue dot in the
// middle (the same solid dot the app draws for an agent).
//
//   swift Scripts/make-icon.swift <out.png> [size]
//
// install.sh renders every size and packs Resources/TapSpace.icns with iconutil.
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

let bodyPath = squircle(CGRect(x: 100, y: 100, width: 824, height: 824))

// White tile with the system's soft drop shadow, so it reads on a white desktop.
ctx.saveGState()
ctx.setShadow(offset: CGSize(width: 0, height: -10), blur: 24, color: rgb(0x000000, 0.22))
ctx.addPath(bodyPath)
ctx.setFillColor(rgb(0xffffff))
ctx.fillPath()
ctx.restoreGState()

// The blue dot: one solid fill (#007AFF), centered.
let r: CGFloat = 170
ctx.setFillColor(rgb(0x007aff))
ctx.fillEllipse(in: CGRect(x: 512 - r, y: 512 - r, width: r * 2, height: r * 2))

guard let img = ctx.makeImage() else { exit(1) }
let rep = NSBitmapImageRep(cgImage: img)
guard let png = rep.representation(using: .png, properties: [:]) else { exit(1) }
try png.write(to: out)
print("wrote \(out.path) \(Int(S))x\(Int(S))")
