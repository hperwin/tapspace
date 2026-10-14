import AppKit
import SwiftUI

/// The chat's markdown, as the web page renders it: paragraphs per line,
/// bullet lists, fenced code blocks, and inline bold / code / links.
enum MDBlock: Hashable {
    case para(AttributedString)
    case bullets([AttributedString])
    case code(String)
}

enum Markdown {
    private static let bullet = try! NSRegularExpression(pattern: #"^\s*[-*]\s+(?:\[[ xX]\]\s*)?(.*)$"#)
    private static let bareURL = try! NSRegularExpression(pattern: #"(^|[\s(])(https?://[^\s<>)`]+)"#)

    static func blocks(_ src: String, dark: Bool) -> [MDBlock] {
        var out: [MDBlock] = []
        var list: [AttributedString] = []
        var code: [String]? = nil

        func flushList() {
            if !list.isEmpty { out.append(.bullets(list)); list = [] }
        }

        for raw in src.replacingOccurrences(of: "\r\n", with: "\n").components(separatedBy: "\n") {
            if raw.trimmingCharacters(in: .whitespaces).hasPrefix("```") {
                if let c = code {
                    out.append(.code(c.joined(separator: "\n")))
                    code = nil
                } else {
                    flushList()
                    code = []
                }
                continue
            }
            if code != nil { code!.append(raw); continue }

            let ns = raw as NSString
            if let m = bullet.firstMatch(in: raw, range: NSRange(location: 0, length: ns.length)) {
                list.append(inline(ns.substring(with: m.range(at: 1)), dark: dark))
                continue
            }
            flushList()
            let line = raw.replacingOccurrences(of: #"^#+\s*"#, with: "", options: .regularExpression)
            if !line.trimmingCharacters(in: .whitespaces).isEmpty {
                out.append(.para(inline(line, dark: dark)))
            }
        }
        if let c = code { out.append(.code(c.joined(separator: "\n"))) }
        flushList()
        return out
    }

    static func inline(_ line: String, dark: Bool) -> AttributedString {
        let ns = line as NSString
        let linked = bareURL.stringByReplacingMatches(in: line, range: NSRange(location: 0, length: ns.length),
                                                      withTemplate: "$1<$2>")
        var s = (try? AttributedString(markdown: linked, options: .init(
            allowsExtendedAttributes: false,
            interpretedSyntax: .inlineOnlyPreservingWhitespace,
            failurePolicy: .returnPartiallyParsedIfPossible
        ))) ?? AttributedString(line)
        for run in s.runs {
            if let intent = run.inlinePresentationIntent, intent.contains(.code) {
                s[run.range].font = .system(size: 12, design: .monospaced)
                s[run.range].backgroundColor = dark ? Color.white.opacity(0.16) : Color.black.opacity(0.06)
            }
            if run.link != nil {
                s[run.range].underlineStyle = .single
                s[run.range].foregroundColor = dark ? .white : .ink
            }
        }
        return s
    }
}

/// One rendered message body. Parsing is cached per (body, ink), so a
/// bubble whose entrance spring re-renders it every frame never re-parses.
struct MarkdownText: View {
    let body_: String
    var dark = false
    var size: CGFloat = 13.5

    init(_ body: String, dark: Bool = false, size: CGFloat = 13.5) {
        self.body_ = body
        self.dark = dark
        self.size = size
    }

    var body: some View {
        let blocks = MarkdownCache.blocks(body_, dark: dark)
        let ink: Color = dark ? .white : .ink
        VStack(alignment: .leading, spacing: 7) {
            ForEach(Array(blocks.enumerated()), id: \.offset) { _, b in
                MDBlockView(block: b, dark: dark)
            }
        }
        .font(.system(size: size))
        .lineSpacing(2.5)
        .foregroundStyle(ink)
        .tint(ink)
        .textSelection(.enabled)
    }
}

@MainActor
enum MarkdownCache {
    private static var store: [String: [MDBlock]] = [:]

    static func blocks(_ src: String, dark: Bool) -> [MDBlock] {
        let key = (dark ? "d" : "l") + src
        if let hit = store[key] { return hit }
        if store.count > 600 { store.removeAll(keepingCapacity: true) }
        let parsed = Markdown.blocks(src, dark: dark)
        store[key] = parsed
        return parsed
    }
}

struct MDBlockView: View {
    let block: MDBlock
    let dark: Bool

    var body: some View {
        switch block {
        case .para(let s):
            Text(s).fixedSize(horizontal: false, vertical: true)
        case .bullets(let items):
            MDBullets(items: items, dark: dark)
        case .code(let c):
            MDCode(code: c, dark: dark)
        }
    }
}

struct MDBullets: View {
    let items: [AttributedString]
    let dark: Bool

    var body: some View {
        let dot: Color = dark ? Color.white.opacity(0.7) : Color.dim
        VStack(alignment: .leading, spacing: 3) {
            ForEach(Array(items.enumerated()), id: \.offset) { _, item in
                HStack(alignment: .firstTextBaseline, spacing: 7) {
                    Text("•").foregroundStyle(dot)
                    Text(item).fixedSize(horizontal: false, vertical: true)
                }
            }
        }
    }
}

struct MDCode: View {
    let code: String
    let dark: Bool

    var body: some View {
        let well: Color = dark ? Color.white.opacity(0.12) : Color.black.opacity(0.05)
        ScrollView(.horizontal, showsIndicators: false) {
            Text(code)
                .font(.system(size: 12, design: .monospaced))
                .lineSpacing(2)
                .fixedSize()
                .padding(10)
        }
        .background(RoundedRectangle(cornerRadius: 10, style: .continuous).fill(well))
    }
}
