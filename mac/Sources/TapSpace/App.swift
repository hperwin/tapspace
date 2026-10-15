import AppKit
import Observation
import QuartzCore
import SwiftUI

/// Launch flags (verification, like SnappWAU's):
///   --snapshot <dir>   render every scene to PNG at 2x with the real view hierarchy, then exit
///   --fps              log display-link frames per second with a still/moving state
///   --demo             a scripted ~10 s loop (tabs, rail, a bubble, the pill) for fps runs
///   --tab chat|visual  open on a tab;  --thread agent:NAME | owner/repo#n  open on a thread
/// and from the environment:
///   TAPSPACE_CHAT_SESSION=<dir>  ChatHarness's New session drive (runs behind the front app)
///   TAPSPACE_BACKGROUND=1        a launch behind the front app (install.sh OPEN=1)
struct Flags {
    var snapshot: String?
    var fps = false
    var demo = false
    var tab: Tab?
    var thread: String?
    var chatSession = false
    var background = false

    init(_ args: [String], env: [String: String] = ProcessInfo.processInfo.environment) {
        var it = args.makeIterator()
        while let a = it.next() {
            switch a {
            case "--snapshot": snapshot = it.next()
            case "--fps": fps = true
            case "--demo": demo = true
            case "--tab": tab = it.next().flatMap(Tab.init(rawValue:))
            case "--thread": thread = it.next()
            default: break
            }
        }
        chatSession = env["TAPSPACE_CHAT_SESSION"] != nil
        background = env["TAPSPACE_BACKGROUND"] == "1"
    }

    /// A render nobody sees (--snapshot, scenes or link frames): no Dock icon, never
    /// activates, the window and the pill parked off every screen, writes off, and
    /// only against a scratch server.
    var offscreen: Bool { snapshot != nil }
    /// The launch takes the foreground: only one meant to be looked at.
    var activates: Bool { !offscreen && !chatSession && !background }
}

final class MainWindow: NSWindow {
    var onEscape: (() -> Bool)?
    override func cancelOperation(_ sender: Any?) {
        if onEscape?() != true { super.cancelOperation(sender) }
    }
}

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate {
    let flags = Flags(Array(CommandLine.arguments.dropFirst()))
    let store = Store.shared
    lazy var visual = VisualModel(store: store)
    var window: MainWindow!
    var hosting: NSHostingView<RootView>!
    var pill: PillController!
    private var fpsTimer: Timer?
    private var keyMonitor: Any?
    private weak var chatResponder: NSTextView?

    func applicationDidFinishLaunching(_ notification: Notification) {
        // An offscreen render writes nothing, but it reads, refreshes taps and would
        // restart a server that is down: only against a scratch server in TAPSPACE_URL.
        if flags.offscreen, let why = ChatHarness.refusal() {
            Harness.log("SNAPSHOT refused: \(why). Point TAPSPACE_URL at a scratch server (Scripts/seed.sh).")
            exit(2)
        }
        NSApp.setActivationPolicy(flags.offscreen ? .accessory : .regular)
        if flags.demo || flags.offscreen { API.allowWrites = false }
        if let t = flags.tab { store.tab = t }
        if let th = flags.thread { store.thread = th }
        store.tabProgress.snap(store.tab == .chat ? 1 : 0)

        buildMenu()
        buildWindow()

        pill = PillController(store: store)
        pill.onOpen = { [weak self] item in self?.open(item) }
        pill.install()
        // Off every screen in the same turn it was ordered in, so it never shows at the menu bar.
        if flags.offscreen, let p = pill.panel { p.setFrameOrigin(NSPoint(x: -32000, y: p.frame.minY)) }

        store.onTickets = { [weak self] t in self?.visual.sync(t) }
        store.onChange = { [weak self] in self?.pill.wake(onlyIfNear: false) }
        store.start()
        watchTab()

        keyMonitor = NSEvent.addLocalMonitorForEvents(matching: .keyDown) { [weak self] e in
            guard let self, e.keyCode == 53 else { return e }
            let closed = MainActor.assumeIsolated { () -> Bool in
                guard self.visual.lightbox != nil else { return false }
                self.visual.closeLightbox()
                return true
            }
            return closed ? nil : e
        }

        if flags.fps { startFPSLog() }
        Harness.run(flags: flags, app: self)
        // Renders, the session drive and background launches stay behind whatever Hayden is using.
        if flags.activates { NSApp.activate() }
    }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        if !flag { showWindow() }
        return true
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { false }

    private func buildWindow() {
        let w = MainWindow(contentRect: NSRect(x: 0, y: 0, width: 1240, height: 820),
                           styleMask: [.titled, .closable, .miniaturizable, .resizable, .fullSizeContentView],
                           backing: .buffered, defer: false)
        w.titlebarAppearsTransparent = true
        w.titleVisibility = .hidden
        w.title = "TapSpace"
        // An empty unified toolbar gives the titlebar its full height, so the
        // traffic lights sit on the same centerline as the Visual | Chat control.
        let toolbar = NSToolbar(identifier: "TapSpaceMain")
        toolbar.allowsUserCustomization = false
        w.toolbar = toolbar
        w.toolbarStyle = .unified
        w.backgroundColor = .white
        w.isOpaque = true
        w.isReleasedWhenClosed = false
        w.minSize = NSSize(width: 960, height: 620)
        w.appearance = NSAppearance(named: .aqua)
        w.collectionBehavior.insert(.fullScreenPrimary)
        w.delegate = self
        hosting = NSHostingView(rootView: RootView(store: store, visual: visual))
        hosting.sizingOptions = []
        w.contentView = hosting
        w.onEscape = { [weak self] in
            guard let self, self.visual.lightbox != nil else { return false }
            self.visual.closeLightbox()
            return true
        }
        if flags.offscreen {
            w.setContentSize(NSSize(width: 1240, height: 820))
            w.setFrameOrigin(NSPoint(x: -30000, y: 200))
        } else {
            // Restores the last frame if one was saved, and keeps saving it.
            if !w.setFrameUsingName(Self.frameName) { w.center() }
            w.setFrameAutosaveName(Self.frameName)
            fitOnScreen(w)
        }
        window = w
        // A background launch waits behind the front app for the Dock icon or the pill.
        if flags.background { w.orderBack(nil) } else { w.makeKeyAndOrderFront(nil) }
        Motion.main.attach(to: hosting)
        hosting.layoutSubtreeIfNeeded()
        ShellChrome.shared.measure(w)
    }

    private static let frameName = "TapSpaceMain"

    /// A frame saved on a display that is gone comes back fully visible.
    private func fitOnScreen(_ w: NSWindow) {
        guard let screen = w.screen ?? NSScreen.main else { return }
        let vis = screen.visibleFrame
        var f = w.frame
        f.size.width = min(f.width, vis.width)
        f.size.height = min(f.height, vis.height)
        f.origin.x = min(max(f.minX, vis.minX), vis.maxX - f.width)
        f.origin.y = min(max(f.minY, vis.minY), vis.maxY - f.height)
        if f != w.frame { w.setFrame(f, display: false) }
    }

    // Delegate callbacks can arrive while the window is still being built
    // (before `window` is set), so they read the notification's window.
    private func remeasure(_ n: Notification) {
        guard let w = n.object as? NSWindow else { return }
        ShellChrome.shared.measure(w)
    }
    func windowDidResize(_ notification: Notification) { remeasure(notification) }
    func windowDidEnterFullScreen(_ notification: Notification) { remeasure(notification) }
    func windowDidExitFullScreen(_ notification: Notification) { remeasure(notification) }
    func windowDidChangeScreen(_ notification: Notification) {
        remeasure(notification)
        // The display link follows the view to the new display.
        Motion.main.wake()
    }

    /// Leaving Chat hands the keyboard back (the composer is hidden, so
    /// typing on Visual must not land in it); coming back gives it back.
    private func watchTab() {
        withObservationTracking { _ = store.tab } onChange: { [weak self] in
            Task { @MainActor in
                guard let self else { return }
                if self.store.tab == .visual, let tv = self.window.firstResponder as? NSTextView,
                   tv.isDescendant(of: self.hosting) {
                    self.chatResponder = tv
                    self.window.makeFirstResponder(nil)
                } else if self.store.tab == .chat, let tv = self.chatResponder, tv.window === self.window,
                          !(self.window.firstResponder is NSTextView) {
                    self.window.makeFirstResponder(tv)
                }
                self.watchTab()
            }
        }
    }

    func showWindow() {
        window.makeKeyAndOrderFront(nil)
        NSApp.activate()
    }

    /// Bring the window forward on what a pill row points at.
    func open(_ item: PillItem?) {
        if let item {
            switch item.kind {
            case .card:
                store.tab = .visual
                let id = String(item.id.dropFirst(5))
                Task { @MainActor in
                    try? await Task.sleep(for: .milliseconds(250))
                    self.visual.focus(id)
                }
            case .tap(let key), .agent(let key):
                store.thread = key
                store.tab = .chat
                store.open(key)
            }
        }
        showWindow()
    }

    func windowDidBecomeKey(_ notification: Notification) {
        store.windowKey = true
        if store.tab == .chat { store.markRead(store.thread) }
    }

    func windowDidResignKey(_ notification: Notification) { store.windowKey = false }

    // MARK: Menu

    private func buildMenu() {
        let main = NSMenu()
        let appItem = NSMenuItem()
        let app = NSMenu()
        app.addItem(withTitle: "About TapSpace", action: #selector(NSApplication.orderFrontStandardAboutPanel(_:)), keyEquivalent: "")
        app.addItem(.separator())
        app.addItem(withTitle: "Hide TapSpace", action: #selector(NSApplication.hide(_:)), keyEquivalent: "h")
        let others = app.addItem(withTitle: "Hide Others", action: #selector(NSApplication.hideOtherApplications(_:)), keyEquivalent: "h")
        others.keyEquivalentModifierMask = [.command, .option]
        app.addItem(.separator())
        app.addItem(withTitle: "Quit TapSpace", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        appItem.submenu = app
        main.addItem(appItem)

        let editItem = NSMenuItem()
        let edit = NSMenu(title: "Edit")
        edit.addItem(withTitle: "Undo", action: Selector(("undo:")), keyEquivalent: "z")
        let redo = edit.addItem(withTitle: "Redo", action: Selector(("redo:")), keyEquivalent: "z")
        redo.keyEquivalentModifierMask = [.command, .shift]
        edit.addItem(.separator())
        edit.addItem(withTitle: "Cut", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        edit.addItem(withTitle: "Copy", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        edit.addItem(withTitle: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        edit.addItem(withTitle: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        editItem.submenu = edit
        main.addItem(editItem)

        let viewItem = NSMenuItem()
        let view = NSMenu(title: "View")
        view.addItem(withTitle: "Visual", action: #selector(showVisual), keyEquivalent: "1").target = self
        view.addItem(withTitle: "Chat", action: #selector(showChat), keyEquivalent: "2").target = self
        viewItem.submenu = view
        main.addItem(viewItem)

        let winItem = NSMenuItem()
        let win = NSMenu(title: "Window")
        win.addItem(withTitle: "Minimize", action: #selector(NSWindow.performMiniaturize(_:)), keyEquivalent: "m")
        win.addItem(withTitle: "Close", action: #selector(NSWindow.performClose(_:)), keyEquivalent: "w")
        win.addItem(.separator())
        win.addItem(withTitle: "TapSpace", action: #selector(showMain), keyEquivalent: "0").target = self
        winItem.submenu = win
        main.addItem(winItem)
        NSApp.windowsMenu = win
        NSApp.mainMenu = main
    }

    @objc private func showVisual() { store.tab = .visual; showWindow() }
    @objc private func showChat() { store.tab = .chat; showWindow() }
    @objc private func showMain() { showWindow() }

    // MARK: --fps

    private func startFPSLog() {
        Motion.main.frames = 0
        Motion.pill.frames = 0
        let t = Timer(timeInterval: 1, repeats: true) { _ in
            MainActor.assumeIsolated {
                @MainActor func line(_ m: Motion) -> String {
                    let state = m.isStill ? "still" : "moving"
                    // rate = frames over the time the link actually ran; worst = longest gap.
                    let rate = m.runTime > 0.05 ? Int((Double(m.frames) / m.runTime).rounded()) : 0
                    let worst = Int((m.longestFrame * 1000).rounded())
                    return "\(m.name)=\(m.frames) \(state)\(m.isPaused ? " paused" : "") drivers=\(m.activeCount)"
                        + " run=\(String(format: "%.2f", m.runTime)) rate=\(rate) worst=\(worst)ms"
                }
                print("FPS \(line(Motion.main)) | \(line(Motion.pill)) | pill \(self.pill.liquid.debugState)")
                fflush(stdout)
                for m in [Motion.main, Motion.pill] { m.frames = 0; m.runTime = 0; m.longestFrame = 0 }
            }
        }
        RunLoop.main.add(t, forMode: .common)
        fpsTimer = t
    }
}
