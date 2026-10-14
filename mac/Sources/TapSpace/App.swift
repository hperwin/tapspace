import AppKit
import QuartzCore
import SwiftUI

/// Launch flags (verification, like SnappWAU's):
///   --snapshot <dir>   render every scene to PNG at 2x with the real view hierarchy, then exit
///   --fps              log display-link frames per second with a still/moving state
///   --demo             a scripted ~10 s loop (tabs, rail, a bubble, the pill) for fps runs
///   --tab chat|visual  open on a tab;  --thread agent:NAME | owner/repo#n  open on a thread
struct Flags {
    var snapshot: String?
    var fps = false
    var demo = false
    var tab: Tab?
    var thread: String?

    init(_ args: [String]) {
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
    }
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

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.regular)
        if flags.demo || flags.snapshot != nil { API.allowWrites = false }
        if let t = flags.tab { store.tab = t }
        if let th = flags.thread { store.thread = th }
        store.tabProgress.snap(store.tab == .chat ? 1 : 0)

        buildMenu()
        buildWindow()

        pill = PillController(store: store)
        pill.onOpen = { [weak self] item in self?.open(item) }
        pill.install()

        store.onTickets = { [weak self] t in self?.visual.sync(t) }
        store.onChange = { [weak self] in self?.pill.wake(onlyIfNear: false) }
        store.start()

        keyMonitor = NSEvent.addLocalMonitorForEvents(matching: .keyDown) { [weak self] e in
            guard let self else { return e }
            return MainActor.assumeIsolated {
                if e.keyCode == 53, self.visual.lightbox != nil {
                    self.visual.closeLightbox()
                    return nil
                }
                return e
            }
        }

        if flags.fps { startFPSLog() }
        if flags.demo { Verify.demo(self) }
        if let dir = flags.snapshot { Verify.snapshots(self, dir: dir) }
        NSApp.activate()
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
        w.backgroundColor = .white
        w.isReleasedWhenClosed = false
        w.minSize = NSSize(width: 960, height: 620)
        w.appearance = NSAppearance(named: .aqua)
        w.delegate = self
        hosting = NSHostingView(rootView: RootView(store: store, visual: visual))
        hosting.sizingOptions = []
        w.contentView = hosting
        w.onEscape = { [weak self] in
            guard let self, self.visual.lightbox != nil else { return false }
            self.visual.closeLightbox()
            return true
        }
        if flags.snapshot != nil {
            w.setContentSize(NSSize(width: 1240, height: 820))
            w.setFrameOrigin(NSPoint(x: -30000, y: 200))
        } else {
            w.center()
            w.setFrameAutosaveName("TapSpaceMain")
        }
        window = w
        w.makeKeyAndOrderFront(nil)
        Motion.main.attach(to: hosting)
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
                store.markRead(key)
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
                func line(_ m: Motion) -> String {
                    let state = m.isStill ? "still" : "moving"
                    return "\(m.name)=\(m.frames) \(state)\(m.isPaused ? " paused" : "") drivers=\(m.activeCount)"
                }
                print("FPS \(line(Motion.main)) | \(line(Motion.pill)) | pill \(self.pill.liquid.debugState)")
                fflush(stdout)
                Motion.main.frames = 0
                Motion.pill.frames = 0
            }
        }
        RunLoop.main.add(t, forMode: .common)
        fpsTimer = t
    }
}
