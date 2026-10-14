# TapSpace mac: shared contract

Target: `TapSpace` (Sources/TapSpace), Swift 6 tools, language mode v5, macOS 26. Builds clean (`swift build`, 0 errors, 0 warnings) as of the stabilize commit.
Everything below is depended on across lanes. Changing a signature listed here breaks other lanes: ADD new members instead of changing or removing existing ones. If you truly need a breaking change, put it in your final report so the orchestrator can schedule it.

## File ownership

| Lane | Owns (edit only these, plus new files with your prefix) |
|---|---|
| visual | VisualView.swift, PixelDiff.swift, new `Visual*.swift` |
| chat | ChatView.swift, Markdown.swift, TextInput.swift, new `Chat*.swift` |
| pill | PillView.swift, Liquid.swift, new `Pill*.swift` |
| shell | MainView.swift, Ambient.swift, App.swift, main.swift, new `Shell*.swift` |
| core | Store.swift, API.swift, Motion.swift, new `Core*.swift` |
| harness | Harness.swift, mac/Scripts/*, mac/Resources/*, the icon generator, README.md (only a new "Mac app" section), new `Harness*.swift` |

Package.swift: core lane (only if a lane asks; no new dependencies).

## Models (API.swift, core). All `Decodable, Equatable`

- `Ticket` (Identifiable): `id, agent, title, campaign?, version, versions: [TicketVersion], status`; `entry(_ v:)`, `current`, `isVideo`, `videoDeleted`, `lastTs`, `mediaPath(_ v:) -> String` (`/shots/<id>/<v>.<ext>`), `versionKey` (`"id#v"`).
- `TicketVersion`: `v, note?, ts, media?, ext?, video_deleted?`; `isVideo`.
- `Agent` (Identifiable, id = name): `name, state ("thinking"|"here"|"away"), seen, first?, where?, last, count, last_master?`; `key` = `"agent:" + name`.
- `ChatMessage` (Identifiable): `id: Int, agent, role ("master" = agent side, else Hayden), body, ts`.
- `Tap` (Identifiable, id = key): `key ("owner/repo#n"), repo, number, title, url, status ("flying"|"needs-you"|"boarding"|"landed"), goals: [TapGoal], messages: [TapMessage], created, closed?, last`; `name`, `repoName`.
- `TapGoal { text, done }`, `TapMessage { who ("you" = Hayden), body, ts (ISO), seen?, url? }` + `date: Double`.
- `TapsResponse { me, items: [Tap], fetched, error }`, `ActivityRow { campaign, agent, verb, detail, kind, age, state }` + `key`, `Sessions { working, recent }`, `ActivityResponse { rows, sessions }`.
- Free functions: `parseISO(_:) -> Double`, `when(_ ts:) -> String`, `timeago(_ ts:) -> String`.

## API (API.swift, core): `enum API`

- `API.base: URL` (TAPSPACE_URL, default http://127.0.0.1:4747), `API.allowWrites: Bool` (false under --demo/--snapshot), `API.isLocal`.
- `get<T>(_:_:_:)`, `data(_ path:) -> Data`, `post(_ path:, _ body:, speaksForHayden: Bool = true) -> [String: Any]` (content-type json, no Origin header; speaksForHayden posts are refused when `allowWrites` is false).
- `state() -> [Ticket]`, `agents() -> [Agent]`, `chat(_ agent:) -> [ChatMessage]`, `taps() -> TapsResponse`, `activity() -> ActivityResponse`. All `async throws`.

## Store (Store.swift, core): `@MainActor @Observable final class Store`, `Store.shared`

- Enums: `Tab { visual, chat }` (String raw), `Connection { connecting, live, down }`. Structs: `PendingMessage { id, key, body, ts, demo }`, `Toast { id, text }`.
- State (read-only outside core unless noted): `connection, tickets, ticketsLoaded, agents (rw), agentsLoaded, chats: [agentName: [ChatMessage]], taps: TapsResponse, activity, sessions, pending, confirmed, decided, reads, windowKey (rw), toast (rw), focusCard (rw), tab (rw), thread (rw, a thread key: "agent:NAME" or "owner/repo#n")`.
- `tabProgress: SpringBox` (0 visual .. 1 chat). Hooks: `onTickets: ([Ticket]) -> Void`, `onChange: () -> Void`.
- `start()` begins polling (state 1.5 s, agents 2 s, chat, taps 5 s, activity) and starts the server via tap.py up if down.
- Threads: `threadMissing`, `threadKeys`, `agent(_ key:)`, `tap(_ key:)`, `readAt(_:)`, `markRead(_:)`, `lastTheirs(_:)`, `isViewing(_:)`, `isUnread(_:)`.
- Counts: `waitingCards: [Ticket]`, `needsYouTaps: [Tap]`, `unreadAgents: [Agent]`, `visualBadge`, `chatBadge`, `waitingTotal`.
- Actions: `decide(_ t:, approve:, message:) async -> Bool`, `send(_ body:, to key:) async -> Bool` (agent -> /api/chat, tap -> /api/taps/say, optimistic `pending`), `demoBubble(_:to:)`, `show(_ text:)` (toast).
- Extensions living in other lanes' files (owned by that lane, callable by all): `isViewingCandidate`, `open(_ key:)` (ChatView.swift, chat); `pillItems: [PillItem]` (PillView.swift, pill).
- `Images.shared` (Store.swift): `cached(_ path:, maxPixel:) -> NSImage?`, `load(_ path:, maxPixel:) async -> NSImage?` (downsampled, deduped).

## Motion (Motion.swift, core)

- `struct Spring(value, stiffness:, damping:)` / `Spring(value, SpringPreset)`: `step`, `advance(dt) -> Bool` (substepped at 480 Hz, snaps when settled), `snap(to:)`, `use(_:)`, `settled`, `atRest`.
- `SpringPreset { stiffness, damping }` presets: `.smooth` 260/30, `.liquid` 260/24, `.snappy` 420/36, `.pop` 380/15, `.gentle` 120/21, `.draw` 70/16, `.droplet` 420/19, `.spreadW` 300/25, `.spreadH` 190/23, `.retractW` 330/34, `.retractH` 420/38.
- `@MainActor protocol MotionDriver: AnyObject { func advance(_ dt: Double) -> Bool; func finish() }`.
- `@MainActor final class Motion`: `Motion.main` (window), `Motion.pill` (pill panel). `attach(to: NSView)` (CADisplayLink 80-120 fps, paused when still), `activate(_ driver)`, `wake()`, `keepAwake: (() -> Bool)?`, `frames`, `isPaused`, `isStill`, `activeCount`, `settleAll()` (snapshots), `run(seconds:step:)` (offline stepping for snapshots).
- `@MainActor @Observable final class SpringBox: MotionDriver`: `init(_ v, _ preset = .smooth, motion: = .main)`, `value` (observed), `target`, `velocity`, `atRest`, `set(_ t, _ preset?, delay:, then:)`, `snap(_:)`, `kick(_ velocity)`, `onStep`. Create SpringBoxes from MainActor code only (not as a default stored-property value of a nonisolated struct).
- Helpers: `lerp` (CGFloat/Double), `clamp01`, `smoothstep(e0, e1, x)`, `lerpRect`, `pixel(_:)` (round to half point), `aspectFit(_:in:)`, `metaballNeck(c1, r1, c2, r2, ...)`.

## Liquid (Liquid.swift, pill; LiquidTrack used by shell + chat)

- `@MainActor @Observable final class LiquidTrack: MotionDriver`: the springing white selection body with a metaball droplet. `init(axis:, cornerRadius:, dropRadius:, anchorRadius:, squish: = 0.5, motion: = .main)`, `move(to: CGRect, animated:)`, `hide()`, `targetRect`, `bodyRect`, `path: Path`. Presets `LiquidTrack.lead/trail/cross`.
- `struct LiquidCanvas: View { track, fill = .white, shadow = 1 }` draws a track.
- `@MainActor @Observable final class PillLiquid` (pill only): `pillH 24`, `cardW 316`, `windowSize 400x470`, `leftMargin`, `reachRadius`, `setPillContentWidth`, `setCardHeight`, `shapeRect`, `cornerRadius`, `hot`, `debugState`, `distanceToPill`.

## Palette (Ambient.swift, shell)

- `enum Palette` (NSColor): `ink #1d1d1f, dim #86868b, faint #c7c7cc, green #30a14e, blue #007aff`.
- `Color`: `.ink, .dim, .faint, .up, .tapBlue, .well, .bubbleGrey, .hairline`. `View.softShadow(_ k = 1)`.
- Core Animation ambient views (zero main-thread cost): `RingView` + `RingStyle`, `BreathingDot`, `OrbView`, `TypingDots`, `SessionDots`, `Breathing<Content>`, `LayerHost`.

## Shell wiring (App.swift / MainView.swift, shell)

- `struct Flags(_ args: [String])`: `snapshot: String?` (`--snapshot <dir>`), `fps` (`--fps`), `demo` (`--demo`), `tab: Tab?` (`--tab visual|chat`), `thread: String?` (`--thread agent:NAME|owner/repo#n`). New flags: add a field here (shell) and read it in Harness.
- `@MainActor final class AppDelegate`: `flags`, `store`, `visual: VisualModel`, `window: MainWindow`, `hosting: NSHostingView<RootView>`, `pill: PillController`, `showWindow()`, `open(_ item: PillItem?)` (tab/thread routing from the pill).
- `RootView(store:, visual:)`, `TopBar`, `TabSwitcher`, `Badge`, `PresenceHUD` (+ `PresenceModel`), `ToastView`, `ConnectingView`, `TapMark`.
- Cross-lane types the shell calls: `VisualModel(store:)` with `sync(_ tickets)`, `lightbox`, `closeLightbox()`, `focus(_ id)`; `PillController(store:)` with `install()`, `onOpen`, `wake(onlyIfNear:)`, `liquid`, `simMouse`; `Harness.run(flags:app:)`.

## Lane surfaces others call

- visual: `VisualModel` (`cards`, `lightbox`, `resize`, `sync`, `relayout`, `approve`, `toggleNotes`, `sendNotes`, `focus`, `openLightbox`, `closeLightbox`, `mediaReady`), `CardModel`, `Lightbox`, `VisualView`; `PixelDiff.regions(_ a: CGImage, _ b: CGImage) -> Result?` (`boxes`, `aspect`; nil when >55% changed).
- chat: `ChatView`, `MarkdownText(…, dark:, size:)`, `Markdown.blocks/inline`, `GrowingTextView`.
- pill: `PillItem { id, kind (.card | .tap(key) | .agent(key)), who, text, status, number }`, `PillController`, `PillPanel`.

## Harness entry point (Harness.swift, harness)

```swift
@MainActor enum Harness {
    static func run(flags: Flags, app: AppDelegate)
}
```
Called once at the end of `applicationDidFinishLaunching`, after window, pill and `store.start()`. Currently a stub. The harness lane implements `--snapshot <dir>` (render scenes at 2x with `cacheDisplay`, then exit) and `--demo` (scripted ~10 s loop) here, reaching state through `app.store`, `app.visual`, `app.pill`, `app.window`, `Motion.main/pill.settleAll()`. `--fps` logging already lives in App.swift (`startFPSLog`).
