import AppKit
import SwiftUI

/// A multi-line field that reports its content height (so the container can
/// spring to it) and owns Return: either Return sends and Shift-Return is a
/// newline (chat), or Cmd-Return sends (card notes).
struct GrowingTextView: NSViewRepresentable {
    @Binding var text: String
    var placeholder: String
    var fontSize: CGFloat = 13.5
    var returnSends = true
    /// Bump to pull keyboard focus here.
    var focusToken: Int = 0
    var onSend: () -> Void
    var onHeight: (CGFloat) -> Void = { _ in }
    var onFocus: (Bool) -> Void = { _ in }

    final class Coordinator: NSObject, NSTextViewDelegate {
        var parent: GrowingTextView
        var focusToken = 0
        var lastHeight: CGFloat = 0
        init(_ p: GrowingTextView) { parent = p }

        func textDidChange(_ n: Notification) {
            guard let tv = n.object as? NSTextView else { return }
            if parent.text != tv.string { parent.text = tv.string }
            report(tv)
        }

        func report(_ tv: NSTextView) {
            guard let lm = tv.layoutManager, let tc = tv.textContainer else { return }
            lm.ensureLayout(for: tc)
            let h = ceil(lm.usedRect(for: tc).height + tv.textContainerInset.height * 2)
            if h != lastHeight {
                lastHeight = h
                let cb = parent.onHeight
                DispatchQueue.main.async { cb(h) }
            }
        }
    }

    func makeCoordinator() -> Coordinator { Coordinator(self) }

    func makeNSView(context: Context) -> NSScrollView {
        let tv = InputTextView()
        tv.delegate = context.coordinator
        tv.isRichText = false
        tv.importsGraphics = false
        tv.allowsUndo = true
        tv.drawsBackground = false
        tv.font = .systemFont(ofSize: fontSize)
        tv.textColor = Palette.ink
        tv.insertionPointColor = Palette.ink
        tv.textContainerInset = NSSize(width: 0, height: 4)
        tv.textContainer?.lineFragmentPadding = 0
        tv.isVerticallyResizable = true
        tv.isHorizontallyResizable = false
        tv.autoresizingMask = [.width]
        tv.textContainer?.widthTracksTextView = true
        tv.isAutomaticQuoteSubstitutionEnabled = false
        tv.isAutomaticDashSubstitutionEnabled = false
        tv.placeholder = placeholder
        tv.coordinator = context.coordinator

        let sv = NSScrollView()
        sv.drawsBackground = false
        sv.borderType = .noBorder
        sv.hasVerticalScroller = true
        sv.autohidesScrollers = true
        sv.scrollerStyle = .overlay
        sv.documentView = tv
        return sv
    }

    func updateNSView(_ sv: NSScrollView, context: Context) {
        context.coordinator.parent = self
        guard let tv = sv.documentView as? InputTextView else { return }
        tv.placeholder = placeholder
        tv.returnSends = returnSends
        if tv.string != text {
            tv.string = text
            tv.needsDisplay = true
            context.coordinator.report(tv)
        }
        if focusToken != context.coordinator.focusToken {
            context.coordinator.focusToken = focusToken
            DispatchQueue.main.async { tv.window?.makeFirstResponder(tv) }
        }
    }

    final class InputTextView: NSTextView {
        var placeholder = "" { didSet { if oldValue != placeholder { needsDisplay = true } } }
        var returnSends = true
        weak var coordinator: Coordinator?

        override func keyDown(with event: NSEvent) {
            let isReturn = event.keyCode == 36 || event.keyCode == 76
            if isReturn && !hasMarkedText() {
                let mods = event.modifierFlags.intersection(.deviceIndependentFlagsMask)
                let send = returnSends ? !mods.contains(.shift) && !mods.contains(.option) : mods.contains(.command)
                if send {
                    coordinator?.parent.onSend()
                    return
                }
                if returnSends && mods.contains(.shift) {
                    insertNewlineIgnoringFieldEditor(nil)
                    return
                }
            }
            super.keyDown(with: event)
        }

        override func becomeFirstResponder() -> Bool {
            let ok = super.becomeFirstResponder()
            if ok { coordinator?.parent.onFocus(true) }
            return ok
        }

        override func resignFirstResponder() -> Bool {
            let ok = super.resignFirstResponder()
            if ok { coordinator?.parent.onFocus(false) }
            return ok
        }

        override func draw(_ dirtyRect: NSRect) {
            super.draw(dirtyRect)
            guard string.isEmpty, !placeholder.isEmpty else { return }
            let attrs: [NSAttributedString.Key: Any] = [
                .font: font ?? .systemFont(ofSize: 13.5),
                .foregroundColor: Palette.dim.withAlphaComponent(0.85),
            ]
            let inset = textContainerInset
            (placeholder as NSString).draw(at: NSPoint(x: inset.width + (textContainer?.lineFragmentPadding ?? 0),
                                                       y: inset.height), withAttributes: attrs)
        }
    }
}
