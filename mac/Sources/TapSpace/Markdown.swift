import AppKit
import SwiftUI

/// The chat's markdown, as the web page renders it: paragraphs per line,
/// bullet lists, fenced code blocks, and inline bold / code / links, built as
/// ONE attributed string per message. A message is one AppKit text view
/// (ChatTextView), so a drag selects across its paragraphs and on into the
/// next messages, and Copy takes all of it with its breaks. SwiftUI's selectable
/// Text keeps a selection inside one Text, which held a copy to one paragraph.
enum Markdown {
    private static let bullet = try! NSRegularExpression(pattern: #"^\s*[-*]\s+(?:\[[ xX]\]\s*)?(.*)$"#)
    private static let bareURL = try! NSRegularExpression(pattern: #"(^|[\s(])(https?://[^\s<>)`]+)"#)

    private enum Line { case para(String), item(String), code(String), blank }
    /// A code block's padding inside its well.
    static let wellPad: CGFloat = 10

    /// Each source line is a paragraph, 7 pt apart (3 pt inside a list, none inside a code block). A blank line
    /// stays in the text with no height, so a copy keeps the message's own paragraph breaks.
    static func attributed(_ src: String, dark: Bool, size: CGFloat) -> NSAttributedString {
        var lines: [Line] = []
        var inCode = false
        for raw in src.replacingOccurrences(of: "\r\n", with: "\n").components(separatedBy: "\n") {
            if raw.trimmingCharacters(in: .whitespaces).hasPrefix("```") { inCode.toggle(); continue }
            if inCode { lines.append(.code(raw)); continue }
            let ns = raw as NSString
            if let m = bullet.firstMatch(in: raw, range: NSRange(location: 0, length: ns.length)) {
                lines.append(.item(ns.substring(with: m.range(at: 1))))
            } else if raw.trimmingCharacters(in: .whitespaces).isEmpty {
                lines.append(.blank)
            } else {
                lines.append(.para(raw.replacingOccurrences(of: #"^#+\s*"#, with: "", options: .regularExpression)))
            }
        }
        while case .blank = lines.first { lines.removeFirst() }
        while case .blank = lines.last { lines.removeLast() }

        let ink: NSColor = dark ? .white : Palette.ink
        let font = NSFont.systemFont(ofSize: size)
        let mono = NSFont.monospacedSystemFont(ofSize: 12, weight: .regular)
        let well = dark ? NSColor.white.withAlphaComponent(0.12) : NSColor.black.withAlphaComponent(0.05)
        let out = NSMutableAttributedString()
        var prev: Line?                       // the last line with height
        var last: [NSAttributedString.Key: Any] = [:]   // the paragraph above, for its newline
        for line in lines {
            let style = NSMutableParagraphStyle()
            style.lineSpacing = 2.5
            var text: NSAttributedString
            var lineFont = font
            switch line {
            case .blank:
                style.maximumLineHeight = 0.01
                style.lineSpacing = 0
                text = NSAttributedString(string: "", attributes: [.font: font])
            case .para(let s):
                text = inline(s, font: font, ink: ink, dark: dark)
            case .item(let s):
                style.tabStops = [NSTextTab(textAlignment: .left, location: 13)]
                style.headIndent = 13
                let t = NSMutableAttributedString(string: "•\t", attributes: [
                    .font: font, .foregroundColor: dark ? NSColor.white.withAlphaComponent(0.7) : Palette.dim])
                t.append(inline(s, font: font, ink: ink, dark: dark))
                text = t
            case .code(let s):                // inside the well ChatTextView draws: 10 pt in from each side
                style.lineSpacing = 2
                style.firstLineHeadIndent = Markdown.wellPad
                style.headIndent = Markdown.wellPad
                style.tailIndent = -Markdown.wellPad
                lineFont = mono
                text = NSAttributedString(string: s, attributes: [.font: mono, .foregroundColor: ink, .codeWell: well])
            }
            switch (prev, line) {
            case (nil, .code): style.paragraphSpacingBefore = Markdown.wellPad
            case (nil, _), (_, .blank): style.paragraphSpacingBefore = 0
            case (.code, .code): style.paragraphSpacingBefore = 0
            case (_, .code), (.code, _): style.paragraphSpacingBefore = 7 + Markdown.wellPad
            case (.item, .item): style.paragraphSpacingBefore = 3
            default: style.paragraphSpacingBefore = 7
            }
            if case .blank = line {} else { prev = line }
            // A paragraph's newline is its own last character: it carries the paragraph's font, style and well.
            if out.length > 0 { out.append(NSAttributedString(string: "\n", attributes: last)) }
            let start = out.length
            out.append(text)
            out.addAttribute(.paragraphStyle, value: style, range: NSRange(location: start, length: out.length - start))
            last = [.font: lineFont, .paragraphStyle: style]
            if case .code = line { last[.codeWell] = well }
        }
        return out
    }

    /// One line's inline markdown (bold, italics, code, links), bare URLs linked.
    private static func parse(_ line: String) -> AttributedString {
        let ns = line as NSString
        let linked = bareURL.stringByReplacingMatches(in: line, range: NSRange(location: 0, length: ns.length),
                                                      withTemplate: "$1<$2>")
        return (try? AttributedString(markdown: linked, options: .init(
            allowsExtendedAttributes: false,
            interpretedSyntax: .inlineOnlyPreservingWhitespace,
            failurePolicy: .returnPartiallyParsedIfPossible
        ))) ?? AttributedString(line)
    }

    /// For a SwiftUI Text (the goal pills).
    static func inline(_ line: String, dark: Bool) -> AttributedString {
        var s = parse(line)
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

    /// For a message's text view: the same runs in AppKit attributes.
    static func inline(_ line: String, font: NSFont, ink: NSColor, dark: Bool) -> NSAttributedString {
        let parsed = parse(line)
        let out = NSMutableAttributedString()
        for run in parsed.runs {
            let intent = run.inlinePresentationIntent ?? []
            var f = font
            var a: [NSAttributedString.Key: Any] = [.foregroundColor: ink]
            if intent.contains(.code) {
                f = .monospacedSystemFont(ofSize: 12, weight: .regular)
                a[.backgroundColor] = dark ? NSColor.white.withAlphaComponent(0.16) : NSColor.black.withAlphaComponent(0.06)
            }
            if intent.contains(.stronglyEmphasized) { f = NSFontManager.shared.convert(f, toHaveTrait: .boldFontMask) }
            if intent.contains(.emphasized) { f = NSFontManager.shared.convert(f, toHaveTrait: .italicFontMask) }
            if intent.contains(.strikethrough) { a[.strikethroughStyle] = NSUnderlineStyle.single.rawValue }
            if let url = run.link { a[.link] = url }
            a[.font] = f
            out.append(NSAttributedString(string: String(parsed[run.range].characters), attributes: a))
        }
        return out
    }
}

/// One rendered message body: one selectable text view, sized to its text.
/// The attributed string is cached per (body, ink, size), so a bubble whose
/// entrance spring re-renders it every frame never re-parses or re-lays out.
struct MarkdownText: NSViewRepresentable {
    let body_: String
    var dark = false
    var size: CGFloat = 13.5

    init(_ body: String, dark: Bool = false, size: CGFloat = 13.5) {
        self.body_ = body
        self.dark = dark
        self.size = size
    }

    func makeNSView(context: Context) -> ChatTextView { ChatTextView() }

    func updateNSView(_ tv: ChatTextView, context: Context) {
        tv.show(body_, dark: dark, size: size)
    }

    func sizeThatFits(_ proposal: ProposedViewSize, nsView tv: ChatTextView, context: Context) -> CGSize? {
        tv.fit(width: proposal.width.flatMap { $0.isFinite ? $0 : nil } ?? 10_000)
    }
}

extension NSAttributedString.Key {
    /// A fenced code line; the value is its well's colour.
    static let codeWell = NSAttributedString.Key("tapspace.codeWell")
}

@MainActor
enum MarkdownCache {
    private static var store: [String: NSAttributedString] = [:]

    static func text(_ src: String, dark: Bool, size: CGFloat) -> NSAttributedString {
        let key = (dark ? "d" : "l") + "\(size)|" + src
        if let hit = store[key] { return hit }
        if store.count > 600 { store.removeAll(keepingCapacity: true) }
        let built = Markdown.attributed(src, dark: dark, size: size)
        store[key] = built
        return built
    }
}

/// A message body's text view. A drag that starts in one and leaves it carries
/// on through the messages it crosses: each of them shows its part selected,
/// and Copy (Edit menu, Cmd-C, the context menu) takes every part in order,
/// a blank line between messages.
final class ChatTextView: NSTextView {
    /// Every message body on screen, for a drag that crosses them.
    private static let all = NSHashTable<ChatTextView>.weakObjects()
    /// The view the current selection started in.
    private static weak var anchor: ChatTextView?

    /// The part of this message a drag from another one selected.
    private var crossed = NSRange(location: 0, length: 0)
    private var key = ""
    private var fitted: (width: CGFloat, size: CGSize)?

    init() {
        let storage = NSTextStorage()
        let layout = NSLayoutManager()        // TextKit 1: the fit and the wells read its line fragments
        storage.addLayoutManager(layout)
        let container = NSTextContainer(size: NSSize(width: 0, height: CGFloat.greatestFiniteMagnitude))
        container.lineFragmentPadding = 0
        container.widthTracksTextView = true
        layout.addTextContainer(container)
        super.init(frame: .zero, textContainer: container)
        isEditable = false
        isSelectable = ProcessInfo.processInfo.environment["CHATEXP_NOSEL"] == nil
        drawsBackground = false
        isVerticallyResizable = false
        isHorizontallyResizable = false
        Self.all.add(self)
    }

    required init?(coder: NSCoder) { fatalError("init(coder:) is not used") }

    func show(_ body: String, dark: Bool, size: CGFloat) {
        let k = (dark ? "d" : "l") + "\(size)|" + body
        guard k != key else { return }
        key = k
        fitted = nil
        textStorage?.setAttributedString(MarkdownCache.text(body, dark: dark, size: size))
        let ink: NSColor = dark ? .white : Palette.ink
        linkTextAttributes = [.foregroundColor: ink, .underlineStyle: NSUnderlineStyle.single.rawValue,
                              .cursor: NSCursor.pointingHand]
        cross(NSRange(location: 0, length: 0))
    }

    /// The text's size laid out at most `width` wide: as wide as its longest line, or the whole width when it
    /// holds a code block (the well spans the bubble, as the scrolling block did).
    func fit(width: CGFloat) -> CGSize {
        if let f = fitted, f.width == width { return f.size }
        guard let lm = layoutManager, let tc = textContainer, let s = textStorage else { return .zero }
        tc.size = NSSize(width: width, height: .greatestFiniteMagnitude)
        lm.ensureLayout(for: tc)
        var w: CGFloat = 0, h: CGFloat = 0
        lm.enumerateLineFragments(forGlyphRange: NSRange(location: 0, length: lm.numberOfGlyphs)) { r, used, _, _, _ in
            w = max(w, used.maxX)
            h = max(h, r.maxY)
        }
        var code = false
        s.enumerateAttribute(.codeWell, in: NSRange(location: 0, length: s.length)) { v, _, stop in
            if v != nil { code = true; stop.pointee = true }
        }
        if s.length > 0, s.attribute(.codeWell, at: s.length - 1, effectiveRange: nil) != nil { h += Markdown.wellPad }
        let size = CGSize(width: code ? width : min(width, ceil(w)), height: ceil(h))
        fitted = (width, size)
        return size
    }

    /// The code wells, under the text and any selection (one rounded box per block, the view's full width), then
    /// the part a drag from another message crossed.
    override func drawBackground(in rect: NSRect) {
        super.drawBackground(in: rect)
        guard let lm = layoutManager, let s = textStorage else { return }
        s.enumerateAttribute(.codeWell, in: NSRange(location: 0, length: s.length)) { v, range, _ in
            guard let color = v as? NSColor else { return }
            var box = NSRect.null
            lm.enumerateLineFragments(forGlyphRange: lm.glyphRange(forCharacterRange: range, actualCharacterRange: nil)) { _, used, _, _, _ in
                box = box.union(used)
            }
            guard !box.isNull else { return }
            let well = NSRect(x: 0, y: box.minY - Markdown.wellPad, width: bounds.width, height: box.height + 2 * Markdown.wellPad)
            color.setFill()
            NSBezierPath(roundedRect: well, xRadius: 10, yRadius: 10).fill()
        }
        guard crossed.length > 0, let tc = textContainer, NSMaxRange(crossed) <= s.length else { return }
        (window?.isKeyWindow == true ? NSColor.selectedTextBackgroundColor : .unemphasizedSelectedTextBackgroundColor).setFill()
        let glyphs = lm.glyphRange(forCharacterRange: crossed, actualCharacterRange: nil)
        lm.enumerateEnclosingRects(forGlyphRange: glyphs, withinSelectedGlyphRange: glyphs, in: tc) { r, _ in r.fill() }
    }

    // MARK: A selection across messages

    override func mouseDown(with event: NSEvent) {
        Self.anchor = self
        for v in Self.all.allObjects where v !== self { v.cross(NSRange(location: 0, length: 0)) }
        super.mouseDown(with: event)
    }

    override func setSelectedRanges(_ ranges: [NSValue], affinity: NSSelectionAffinity, stillSelecting: Bool) {
        super.setSelectedRanges(ranges, affinity: affinity, stillSelecting: stillSelecting)
        guard stillSelecting, Self.anchor === self, let e = NSApp.currentEvent, e.type == .leftMouseDragged,
              e.window === window else { return }
        extend(to: e.locationInWindow)
    }

    override func resignFirstResponder() -> Bool {
        let ok = super.resignFirstResponder()
        if ok, Self.anchor === self { for v in Self.all.allObjects where v !== self { v.cross(NSRange(location: 0, length: 0)) } }
        return ok
    }

    /// The drag is at `p` (window points): the messages between this one and the point are selected whole,
    /// the one under the point up to it, every other message not at all. This one's own part is AppKit's.
    private func extend(to p: NSPoint) {
        let views = Self.ordered(in: window)
        guard let a = views.firstIndex(where: { $0 === self }) else { return }
        let mine = convert(bounds, to: nil)
        for (i, v) in views.enumerated() where v !== self {
            let r = v.convert(v.bounds, to: nil), n = v.textStorage?.length ?? 0
            var part = NSRange(location: 0, length: 0)
            if i > a, p.y < mine.minY {                         // down (window points grow upward)
                if r.minY > p.y { part = NSRange(location: 0, length: n) }
                else if r.maxY >= p.y { part = NSRange(location: 0, length: v.index(at: p)) }
            } else if i < a, p.y > mine.maxY {                  // up
                if r.maxY < p.y { part = NSRange(location: 0, length: n) }
                else if r.minY <= p.y { let k = v.index(at: p); part = NSRange(location: k, length: n - k) }
            }
            v.cross(part)
        }
    }

    /// The insertion point nearest `p` (window points), clamped to the text.
    private func index(at p: NSPoint) -> Int {
        let n = textStorage?.length ?? 0
        let k = characterIndexForInsertion(at: convert(p, from: nil))
        return k == NSNotFound ? n : min(max(k, 0), n)
    }

    /// Shows `part` selected without taking the keyboard (drawBackground paints it).
    private func cross(_ part: NSRange) {
        let part = part.length > 0 ? part : NSRange(location: 0, length: 0)
        guard part != crossed else { return }
        crossed = part
        needsDisplay = true
    }

    /// A crossed part takes the colour of AppKit's own selection, which dims when the window does.
    override func viewDidMoveToWindow() {
        super.viewDidMoveToWindow()
        NotificationCenter.default.removeObserver(self)
        guard let window else { return }
        for name in [NSWindow.didBecomeKeyNotification, NSWindow.didResignKeyNotification] {
            NotificationCenter.default.addObserver(self, selector: #selector(keyChanged), name: name, object: window)
        }
    }

    @objc private func keyChanged() { if crossed.length > 0 { needsDisplay = true } }

    /// The message bodies in this window, top to bottom (then left to right).
    private static func ordered(in window: NSWindow?) -> [ChatTextView] {
        guard let window else { return [] }
        let placed: [(view: ChatTextView, rect: NSRect)] = all.allObjects.filter { $0.window === window }
            .map { ($0, $0.convert($0.bounds, to: nil)) }
        return placed.sorted { a, b in a.rect.maxY != b.rect.maxY ? a.rect.maxY > b.rect.maxY : a.rect.minX < b.rect.minX }
            .map { $0.view }
    }

    override func copy(_ sender: Any?) {
        let parts: [NSAttributedString] = Self.ordered(in: window).compactMap { v in
            let r = v === self ? v.selectedRange() : v.crossed
            guard r.length > 0, let s = v.textStorage, NSMaxRange(r) <= s.length else { return nil }
            return s.attributedSubstring(from: r)
        }
        guard !parts.isEmpty else { return }
        let all = NSMutableAttributedString()
        for (i, p) in parts.enumerated() {
            if i > 0 { all.append(NSAttributedString(string: "\n\n")) }
            all.append(p)
        }
        NSPasteboard.general.clearContents()
        NSPasteboard.general.writeObjects([all])
    }
}
