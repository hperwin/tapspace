# TapSpace for macOS: build spec (authored by the orchestrator)

You are building a NATIVE macOS app for TapSpace, Hayden's local workspace where his agents and "taps" live.
Repo: ~/Developer/tapspace (git, branch feat/chat-agents-mac, already checked out). Put the app in ~/Developer/tapspace/mac/.
You are the ONLY writer in this repo. Commit each meaningful step on feat/chat-agents-mac (end commit messages with
"Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"). Do NOT push, do NOT open a PR, do NOT merge.

## What TapSpace is (read these first, fully)
- ~/Developer/tapspace/server.py  (stdlib HTTP server, port 4747; the API the app talks to)
- ~/Developer/tapspace/tap.py     (agent CLI: post/update/wait/listen/reply)
- ~/Developer/tapspace/space/index.html (the existing web UI: Visual tab + Chat tab. The app must do everything it does, natively, better.)
- ~/Developer/tapspace/README.md
The live server Hayden uses runs from ~/orchestration/tapspace (same code) on http://127.0.0.1:4747 with REAL data.

## The animation language to mirror: SnappWAU (read every file)
~/Developer/SnappWAU/Sources/SnappWAU/{Liquid.swift,Views.swift,App.swift,Store.swift}, Package.swift, Scripts/install.sh.
Hayden loves this widget. Mirror its feel EVERYWHERE, as much as possible:
- A CADisplayLink (NSView.displayLink, preferred 120 fps, min 80) drives damped harmonic SPRINGS (the Spring struct: stiffness/damping,
  substepped integration, snap when settled). The link PAUSES when everything is still and wakes on input/data, so idle cost is zero.
- "Liquid" shapes: one white body with a soft shadow that morphs (pill <-> card), width leads height on the way out, height retracts
  first on the way back; slightly underdamped droplets with metaball necks (metaballNeck) that wobble like water.
- Content inside fades/rises with smoothstep progress tied to the spring values; staggered per-row reveals (rowProgress).
- numericText content transitions for every number; symbol replace transitions; pixel-rounded positions so text stays crisp.
- White, clean, Apple-like; continuous corner radii; black ink, muted greys, green up, red down.

## The app
SwiftPM executable like SnappWAU (swift-tools 6.0, platforms .macOS("26.0"), swiftLanguageMode .v5), bundled into TapSpace.app by
mac/Scripts/install.sh (release build, Info.plist, icon, ad-hoc codesign, rsync to /Applications/TapSpace.app; a running copy is
never quit, relaunched or brought forward; it shows Update ready in the top bar, and only that click restarts it on the new build).
Bundle id com.hayden.tapspace. A regular app (Dock icon) PLUS a menu-bar liquid pill (below). App icon: Resources/TapSpace.icon
(Icon Composer format: a white tile with one flat blue dot), compiled by actool at install. Server URL: env
TAPSPACE_URL, default http://127.0.0.1:4747. If the server is down at launch, start it with
`python3 ~/orchestration/tapspace/tap.py up` (fall back to the repo's tap.py) and show a calm "connecting" state meanwhile.

### Main window
- Full-size content view, transparent titlebar, white background, min ~960x620, remembers its frame.
- Top center: a segmented control "Visual | Chat". The selection is a LIQUID white body that springs between the two segments,
  stretching toward the target first and trailing a metaball droplet as it travels, then settling with a tiny wobble. Each segment has
  a badge (blue capsule, numericText): Visual = cards waiting on Hayden; Chat = taps needing him + agent threads with unread replies.
  Cmd-1 / Cmd-2 switch tabs.
- Top right: the presence HUD from the web page (session dots + live activity rows from /api/activity) with spring-animated rings.

### Visual tab (parity with the web page, natively)
- Grid of cards from GET /api/state (poll ~1.5s): agent, title, vN + age; media = image (/shots/<id>/<v>.png) or video
  (/shots/<id>/<v>.<ext>, AVPlayer with controls; video_deleted -> calm placeholder). Cards enter staggered with blur + scale springs.
- Approve -> POST /api/decide {id, action:"approve"}: a green circle+check DRAWS itself (spring-driven trim), then the card collapses
  like liquid (shrinks toward its center, blur) and the grid reflows with springs.
- "Not quite" -> the card's bottom grows a notes field like the WAU pill spreading into its card (width/height springs), Send ->
  POST /api/decide {id, action:"change", message}; card shows "with <agent>, working on your notes" until a new version arrives.
- New versions swap in with a blur cross-fade; port the web page's pixel-diff change highlights (soft rounded blue boxes over changed
  regions, skipped when >55% changed) using Core Graphics/Accelerate on downsampled bitmaps.
- Click media -> zoom lightbox (matchedGeometryEffect), Esc/click closes.
- Empty state: "The space is clear" breathing softly.

### Chat tab (parity with the web Chat tab, natively)
Naming: Hayden's GitHub agent issues are called TAPS. Never show the word "pilot" anywhere in the UI.
- Left rail: "Agents" section (GET /api/agents, poll ~2s): each agent row = an ORB, a flat face disc in the master's look (its
  tone and mark; look 0 is the lead's) whose face follows its state (happy while "here", focused and pulsing with an
  expanding ripple while "thinking", wide-eyed while starting or switching cars, sleepy on a grey disc while "away"); name + state + where. Then
  A master row has Spin down (right-click, or the power control on hover, behind a confirm; POST /api/agents/stop). After a crash
  or reboot a master whose sessions all ended is served "offline": its row reads "Offline" on a grey sleepy orb, shows a visible
  "Restart" button (POST /api/agents/restart, no confirm; toast "Restarting <title>", the row reads Starting at once), and its Spin
  down reads "Clear" ("Clear <title>? Its session already ended; the row leaves the list.").
  "Your taps" (GET /api/taps, poll ~5s): ring avatar per tap by phase (dotted = proposed, spinning arc = en route, breathing blue =
  holding, green ring = landed, green check = done; a done tap aborted or closed draws the grey ring, its row dimmed), title, the
  board's word, repo, age. Unread dot (springs in) per thread (persist read times in UserDefaults).
  The selected-row highlight is ONE liquid white body that springs from row to row (like the WAU body), not a per-row background.
- Right pane: header (avatar, name/title, subtitle, status chip with breathing dot; taps get an "Open issue" link), for taps a goals
  strip (checkbox pills, "n/m goals" with numericText), then the message list: bubbles (Hayden = black, right; others = light grey,
  left), markdown rendered (bold, inline code, code blocks, links open in the default browser, bullet lists), each message one
  selectable text, so a drag selects across its paragraphs and on through the messages it crosses and Copy takes all of it with
  its line breaks (a blank line between messages), timestamps, and for
  Hayden's tap messages a receipt: "sending" -> "delivered" -> "seen 👀" (seen = the comment has an EYES reaction). New bubbles
  spring in (blur + offset + scale); a sent bubble flies from the composer into place. Typing dots (three bouncing dots) while an
  agent is "thinking". Keep scroll pinned to the bottom when already near it.
- Composer: rounded field that grows with a spring as you type; Return sends, Shift-Return newline. To an agent:
  POST /api/chat {agent, body} (then that agent shows thinking). To a tap: POST /api/taps/say {key, body} with optimistic bubble kept
  until GitHub shows the comment; on error, restore the text and show a toast. Always send header content-type: application/json and
  NO Origin header.
- Opening a tap thread POSTs /api/taps/refresh. Empty states as on the web.

### Chat images (added after the first build started; required)
Parity with the web Chat tab (see space/index.html and server.py `_chat_images`): in agent threads Hayden can attach images by a
paperclip button (NSOpenPanel, multiple), by pasting (Cmd-V image data or files), and by dragging files/images onto the chat pane (a
dashed blue "Drop images to attach" overlay springs in). Pending images show as rounded 84 pt thumbnails in a tray ABOVE the composer
that springs open/closed; each thumb springs in and has a small × to remove. Send works with text, images, or both:
POST /api/chat {agent, body, images:[{b64, ext}]} (png/jpg/gif/webp/heic, max 10). Messages with `images` render them as a rounded grid
above the bubble text (1 = natural aspect up to ~340 pt tall; 2/4 = 2 columns; 3/5+ = 3 columns, square crops); click opens the
lightbox. Images are served at /chat-media/<file>. Tap threads are text only: hide the paperclip there and toast
"Images go to agents. Taps take text." on a drop.

### Chat: masters' progress, card notes and links (added October 2026)
Same data as the web page and Glint: `GET /api/agents` rows carry `progress`, `notes` and `notes_read`, plus a top-level `links`.
- A third column when the window has room: one card per live master in a stable order (face with status dot, title, updated time,
  unread dot, the goal in at most 2 lines, a segmented step track with the current step blue, and "3 of 5 · Build"; "No goal yet"
  in muted text before a master publishes one). A click opens that master's thread.
- Under each master in the rail: the cards Hayden sent back with notes that went to that master (thumbnail or title plus the
  note's first words, newest first, at most 4 rows then "+N more") and the blue dot on the master. A row leaves when the master
  posts a new version; opening the master's thread posts the read mark (POST /api/agents/read), so the web page, the app and
  Glint agree.
- Two masters messaging each other: their cards sit together, joined by a line that draws and pulses with TapSpace's chase (Core
  Animation, paused when idle), one line "Title A → Title B · summary", and "Talking with <title>" under each in the rail.
- Titles only (never a NAME with a session code, a path, a hex id or an issue number), no em dashes, flat colors.

### Menu-bar liquid pill (mirror SnappWAU as directly as possible)
A borderless non-activating NSPanel at status-bar level, placed just RIGHT of the notch (SnappWAU sits LEFT of it; do not collide;
on non-notch screens, near the right of the menu bar's center). Reuse/adapt Liquid.swift: a white pill showing the TapSpace mark
and the count of things waiting on Hayden (numericText). Cursor nearby grows the metaball bulge; hover spreads it into a card with
staggered rows: cards waiting (agent: title), taps needing him, agent threads with unread replies; each row clickable -> brings the
main window forward on that tab/thread. Same display-link sleep/wake discipline as SnappWAU (global mouse monitor, no permissions).

### Hard rules
- NEVER call /api/decide or /api/taps/say or /api/chat against the LIVE server (port 4747) while building or testing. Those act for
  Hayden (decide removes his cards; taps/say posts a GitHub comment as him). Test against a SCRATCH server:
  `TAPSPACE_PORT=4748 TAPSPACE_DATA=<scratch dir> python3 ~/Developer/tapspace/server.py` and run the app with
  TAPSPACE_URL=http://127.0.0.1:4748. Seed the scratch server with tap.py (TAPSPACE_URL=http://127.0.0.1:4748): post 3-4 cards using
  REAL screenshots copied from ~/orchestration/tapspace/data/shots/*/ (include one v2 update and one video if any exist), make two
  agents join (tap.py reply --as "Claude · main" ..., tap.py listen --as "Claude · fantasy" --timeout 2) and exchange messages. A scratch
  server reads no Flightdeck board, so it serves no taps (the live server builds them from Hayden's board); never POST taps/say. Kill
  your scratch server and app processes by PID when done (never pkill -f patterns; never touch SnappWAU or the live 4747 server).
- No em dashes in any user-visible string. Product name "Snapp" has two Ps.
- Built-in verification flags (like SnappWAU's): `--snapshot <dir>` renders each scene to PNG at 2x and exits (visual tab, a card in
  notes mode, lightbox, chat tab on an agent thread, the same at 1512 x 887, chat tab on a tap thread, pill collapsed, pill bulging,
  pill expanded) using the real view hierarchy (NSView cacheDisplay / bitmapImageRepForCachingDisplay); `--fps` logs display-link
  frames per second with a still/moving state; `--demo` scripts a ~10 s loop (switch tabs, move the rail selection, send-in a bubble,
  expand/collapse the pill) so fps can be measured without a human. Optional `--tab chat --thread agent:NAME|owner/repo#n` to open a
  specific place.
- Verification never takes the screen from the person using the Mac: `--snapshot` (the scenes, and the link frames with
  `TAPSPACE_LINK_FRAMES=1`) runs offscreen as an accessory app (no Dock icon), never activates, and refuses any server but a
  scratch one on this Mac. `--demo`, fps.sh and the ChatHarness drives run on screen:
  only with that person's go.
- Real signals only: `swift build -c release` clean (no warnings you introduced if avoidable), snapshots you actually LOOKED at with
  the Read tool, fps logs >= 110 while animating and the link paused when still, no crash in 30 s of --demo.
- Do not run more than two heavy jobs at once. No Docker. No Xcode project needed (SwiftPM only).

## Done condition for the build
mac/ builds in release; install.sh produces /Applications/TapSpace.app without disturbing a running copy (OPEN=1 opens it behind the
front app when none runs; opening does not decide or post); all snapshot scenes rendered from a seeded scratch server and inspected;
fps measured; README.md gets a "Mac app" section (install, flags). Report: what you built (files), commits, snapshot paths, fps
numbers, and anything not done with the honest reason.
