# TapSpace

A local design-approval space for AI agent fleets.

Agents post **real screenshots and screen recordings** of what they just built. The cards appear in a quiet page you keep open in your browser. You tap **Approve** or type change notes. The decision reaches the owning agent within seconds and it keeps iterating, without waiting on any other agent.

**The invariant:** you never wait for a fleet to finish to see design work, and you never judge design from prose. A visual claim without a card is not a deliverable. A card without a real render is a defect.

No dependencies. The server and CLI are Python standard library only. The page vendors [anime.js](https://animejs.com) locally, so nothing touches the network. Everything binds to `127.0.0.1`.

## Why this exists

When you run multiple coding agents in parallel, visual work has a review bottleneck: agents describe what they built in prose, or you wait for a whole batch to finish before looking at anything. Both are bad. Prose lies about pixels, and batching means your fastest agent sits idle while your slowest one finishes.

TapSpace decouples the review loop from the build loop:

- The moment an agent finishes a visual unit (a screen, a card, a chart, an animation), it posts a real capture and moves on to its next unit.
- You review whenever you glance at the page. Approve dismisses the card with a checkmark. "Not quite" opens a text box; whatever you type becomes a change order.
- Decisions land in `data/INBOX.jsonl`. Your orchestrator (or the agent itself, polling) picks them up and iterates. New version, same ticket, back in the space.

Faster agents get more approval cycles. Slower agents never block them.

## What's in the box

| File | What it is |
|---|---|
| `server.py` | The server. Python stdlib, single file, port `4747`, localhost only. Persists everything under `data/`. |
| `links.py` | Agent-to-agent links. Reads the Claude Code session transcripts on this Mac (read only) and serves which master agents are messaging each other; agents report nothing. |
| `tap.py` | The agent CLI. Also stdlib, works from any directory. This is the only thing agents need. |
| `space/index.html` | The page you watch. Cards blur in, pixel-diff highlights show what changed between versions, videos play inline with native scrub controls. |
| `space/anime.esm.js` | anime.js v4, vendored (MIT, by Julian Garnier). Drives the presence HUD motion. |
| `AGENTS.md` | The integration contract. Paste it (or its "Agent duties" section) into your agents' prompts. |

## Quickstart

```bash
git clone https://github.com/hperwin/tapspace.git
cd tapspace
python3 tap.py up          # starts the server if it isn't running, health-checks it
open http://127.0.0.1:4747 # the space (macOS; otherwise just open the URL)
```

Post a first card (any PNG works for a smoke test, but the whole point is real renders):

```bash
python3 tap.py post --agent demo --title "First card" --image shot.png
# prints a TICKET_ID, e.g. 3f7de0b7af
```

The card appears in your browser. Tap **Approve** or send a change note. Then, from the agent side:

```bash
python3 tap.py wait 3f7de0b7af --timeout 900
# blocks until you decide on the CURRENT version
# exit 0 = approved, 3 = changes (your message on stdout), 4 = timeout
```

Iterate after a change note:

```bash
python3 tap.py update 3f7de0b7af --image shot2.png --note "v2: moved the pills, glass buttons"
```

Same ticket, version 2, back to `waiting`. On v2+ the page pixel-diffs the new capture against the previous version and floats soft highlights over the regions that changed.

## Setting it up with an AI agent

You can hand this link to a coding agent (Claude Code or similar) and say "set this up." If you are that agent, the whole install is:

1. Clone this repo somewhere stable (it must stay on disk; the server and state live in it).
2. `python3 tap.py up`, then tell your human to open http://127.0.0.1:4747 in their own browser.
3. Paste the **Agent duties** section of [`AGENTS.md`](AGENTS.md) into the prompt of every agent whose deliverable is something the human will look at.
4. If one process coordinates several agents, give it the **Orchestrator duties** from the same file (the short version: tail `data/INBOX.jsonl` and relay each decision to the agent it names).

There is nothing else: no packages, no build, no config. If port 4747 is taken on your machine, change `PORT` at the top of `server.py` and set `TAPSPACE_URL` to match for every `tap.py` caller.

## The loop

| Actor | Action | Mechanism |
|---|---|---|
| Agent | new card | `tap.py post` prints a ticket id, status `waiting` |
| You | approve | space page. Status `approved`, INBOX line written, card check-animates out |
| You | change notes | space page. Status `changes`, INBOX line written, card shows "with \<agent\>" |
| Agent | iteration | `tap.py update`. Version+1, status `waiting`, card re-blurs in |
| Agent | read decision | `tap.py wait` (blocking) or `tap.py status` (instant) |
| Orchestrator | wake + relay | tail `data/INBOX.jsonl`, forward each decision to the owning agent |

Decisions are versioned: `wait` only returns a decision made on the **current** version, so a stale "changes" from v1 can never be misread as the verdict on v2.

## `tap.py` reference

```
tap.py post   --agent NAME --title TEXT (--image X.png | --video X.mp4)
              [--note TEXT] [--campaign TAG] [--owner MASTER]
                                                    -> prints TICKET_ID
tap.py update TICKET (--image X.png | --video X.mp4) [--note TEXT]
tap.py wait   TICKET [--timeout 900]                -> exit 0 approved / 3 changes / 4 timeout
tap.py status TICKET                                -> ticket JSON, never blocks
tap.py up                                           -> idempotent start + health check
tap.py doctor                                       -> server, cards and owners, goal cards, links, sessions
```

Point it at a non-default server with `TAPSPACE_URL` (default `http://127.0.0.1:4747`).

**Which master a card belongs to.** Every card records its `owner`: the chat NAME of the master agent whose Claude Code
session posted it, directly or through one of its lanes. Nobody types it. Inside Claude Code, `tap.py` names the session it
runs in on every call (the `X-Claude-Session` and `X-Claude-Pid` headers, sent only when that Claude process is an ancestor of
`tap.py`, so an id inherited through tmux or a launcher is never sent), and the server ties a session to a NAME whenever a
master listens, replies, retitles or relays. A relay keeps the NAME, so cards from every car and its lanes stay with one master.
Older cards fall back to the master already known for their campaign, or to an exact match of their agent label with a
master's title. Outside Claude Code (a plain terminal, a background job), add `--owner "NAME"`.

## HTTP API (for agents that aren't Python)

`tap.py` wraps five of the endpoints below (`post`, `update`, `ticket`, `state`, `activity`); direct HTTP integrators have the full table. Any agent that can make HTTP requests can integrate.

| Method | Path | Body | Returns |
|---|---|---|---|
| POST | `/api/post` | `{agent, title, image_b64 \| video_b64 (+video_ext mp4\|mov), note?, campaign?, owner?}` | `{ok, id, version: 1}` |
| POST | `/api/update` | `{id, image_b64 \| video_b64 (+video_ext), note?}` | `{ok, id, version}` |
| POST | `/api/decide` | `{id, action: approve\|change, message?}` | `{ok}` (page only; agents never call this) |
| POST | `/api/archive` | `{id}` or `{campaign}` or `{all: true}` | `{ok, archived: n}` |
| GET | `/api/state` | | all live tickets |
| GET | `/api/ticket/<id>` | | one ticket, with `versions` and `decisions` |
| GET | `/api/activity` | | presence rows + session counts + link-scanner counters (`links`) |
| GET | `/shots/<id>/<v>.<ext>` | | the capture; videos support Range requests, so they scrub |

`image_b64` must be the raw base64 of PNG bytes, and `video_b64` of mp4/mov bytes, with **no `data:` URI prefix**. The server stores what you send without validating or converting the format, and a prefixed or malformed string fails that request without a clean JSON error.

Tickets in `/api/state` and `/api/ticket/<id>` carry `owner` (the master's chat NAME, or null) and `owner_how` (`session`,
`pid`, `campaign`, `title`, `explicit`, or null). To file a card under its master over plain HTTP, send the poster's
`X-Claude-Session: <session id>` and `X-Claude-Pid: <claude pid>` headers with `/api/post` and `/api/update`, or pass `owner`.
A ticket's `session` is internal bookkeeping; no client shows it.

A worked `post` from plain shell:

```bash
curl -s -X POST 127.0.0.1:4747/api/post -H 'content-type: application/json' \
  -d "{\"agent\":\"T2-cards\",\"title\":\"Combined stat card\",\"campaign\":\"my-feature\",\"image_b64\":\"$(base64 < shot.png | tr -d '\n')\"}"
# -> {"ok": true, "id": "3f7de0b7af", "version": 1}
```

Every decision also appends one line to `data/INBOX.jsonl`:

```json
{"kind": "tapspace-decision", "ticket": "3f7de0b7af", "agent": "T2-cards",
 "owner": "Claude \u00b7 main", "title": "Combined stat card", "campaign": "my-feature",
 "version": 2, "action": "change", "message": "less white, make the pills look like glass",
 "received_epoch": 1755640000.0}
```

`agent` is the label the poster gave; `owner` is the master's chat NAME. The file is ASCII JSON, so the `·` in a NAME reads
`\u00b7`; match that form when you filter.

## Wiring your agents

The short version. The full contract, with capture recipes and the exact duties to paste into agent prompts, is in [`AGENTS.md`](AGENTS.md).

1. **Embed the duties in every agent's prompt.** Agents post a card the moment a visual unit is done, keep working their next unit instead of parking, treat your change notes as work orders, and never call `/api/decide`.
2. **Real renders only.** iOS: `xcrun simctl io <UDID> screenshot`. Web: a Playwright screenshot of the served page. A document or chart: render the real HTML and capture it. Never a generated or mocked image.
3. **Motion is a video, not a still.** Transitions, entrance animations, live-updating state: post a 5 to 20 second `.mp4`/`.mov` with `--video`. The space plays it inline and you can scrub it.
4. **Give your orchestrator the inbox.** One `tail -n 0 -f data/INBOX.jsonl` is the whole event bus. When a line arrives, relay the action and message verbatim to the owning agent (the event names it), and append it to that agent's brief file so the decision survives restarts. In Claude Code this maps to a Monitor on the tail plus SendMessage to the agent; in any other framework it's the same tail plus however you message a worker.
5. **Solo agent? Skip the orchestrator.** A single agent can post and then `tap.py wait` when the decision truly blocks its next edit, or check `tap.py status` between tasks.

## The Chat tab: many agents, and your taps

The space has two tabs. **Visual** is the review board above. **Chat** is a
workspace where any number of agents can be connected at once:

- **Agents.** Each agent joins under its own name and gets its own thread.
  It listens with `tap.py listen --as "NAME"` (run it in the background; it
  exits with your unanswered messages as soon as you write) and answers with
  `tap.py reply --as "NAME" "text"`. Set `TAPSPACE_AGENT` to skip `--as`.
  Attach images with the paperclip, by pasting, or by dragging them onto the
  chat; they preview above the message box and show as a grid in the bubble.
  The listening agent gets their file paths, and can send images back with
  `tap.py reply --as "NAME" "text" --image shot.png`.
  A long-running agent that hands off to a fresh session keeps ONE thread:
  the old session runs `tap.py relay --as "NAME"`, the thread shows
  *Switching car*, and the new session listens under the same NAME. Messages
  you send meanwhile wait for it.
  An agent shows *Here* while it is listening, *Thinking* while your message
  waits for its reply, and *Away* otherwise.
- **Goal cards.** A master publishes its run once: one plain line for the
  outcome, then its steps in order. It moves the current step when work
  really lands. The Chat tab (web page and Mac app) and Glint show one
  progress card per master. The card lives under the NAME, so a relay keeps
  it. Lanes never publish one.
  ```bash
  tap.py goal --as "NAME" "A calmer checkout screen" --steps "Audit|Spec|Build|Review|PR open"
  tap.py step --as "NAME" next --note "14 of 20 builders done"   # or a number (1 = first), a step's name, done
  tap.py progress --as "NAME"                                   # prints the card as stored
  ```
- **Notes per master.** Under each master in the rail: the cards you sent a
  change note on that belong to that master, newest first, with a blue dot on
  the agent. A row clears when the master posts a new version of that card;
  all of them clear when you open the master's thread. The read mark lives on
  the server, so the page, the Mac app and Glint agree.
- **Who is talking to whom.** When two masters message each other (Claude
  Code messages between sessions on this Mac), the server reads it from the
  session transcripts and shows a live link between their cards. Agents
  report nothing.
- **Spin down.** Every master row in the rail can spin that master down
  (on the page, the small control on the row; in the Mac app, the same
  control or the row's right-click menu). After you confirm, its Claude
  Code sessions end (the tmux session it runs in, else SIGTERM and then
  SIGKILL on the claude process) and it leaves the list; its thread moves
  to `data/archive/`. Only processes named `claude` are ever signalled.
  A new session that listens under the same NAME brings it back.
- **Taps.** GitHub issues you authored with the tap label
  (`TAPSPACE_TAP_LABEL`, default `nava`), read through your `gh` login. Each
  tap shows its goals, its comments, and whether your messages were seen
  (an 👀 reaction). Writing to a tap posts an issue comment as you. Taps
  are scoped by issue author, so other people's taps never appear.

Every agent can talk to all of your taps too: `tap.py taps`, `tap.py tap-read KEY`,
`tap.py tap-say --as NAME KEY "text"` (posted as you, signed "Sent by NAME"), and
`tap.py tap-watch` (blocks until any tap says something new). The server checks
each open tap every 3 seconds with GitHub conditional requests (a 304 costs no
rate limit), so replies show up within seconds without webhooks.

Endpoints: `GET /api/agents` (`{agents, links}`), `GET /api/chat?agent=NAME`,
`GET /api/chat/listen?agent=NAME`, `POST /api/chat {agent, body, images?}`,
`POST /api/chat/reply {agent, body, images?}` (images: `[{b64, ext}]`),
`POST /api/agents/title {agent, title}`, `POST /api/agents/relay {agent}`,
`POST /api/agents/progress {name, goal?, steps?, current?, step?, note?, clear?}`
(this Mac only; `clear: true` removes the card),
`POST /api/agents/read {name}`,
`POST /api/agents/stop {name}` (`{ok, stopped: [pids], tmux: [sessions]}`; an unknown NAME or one with no
live Claude session answers `{ok: false, error}` and ends nothing), `GET /api/taps`,
`POST /api/taps/say {key: "owner/repo#n", body}`, `POST /api/taps/refresh`.
The endpoints that speak for you accept same-origin JSON only.
`GET /api/chat?agent=NAME&after=ID` returns only the rows after that id.
An agent row may carry `progress` (`{goal, steps, current, note, updated}`;
`current` counts from 0 and equals the number of steps when the run is done),
`notes` (`[{ticket, version, title, note, ts, thumb}]`, newest first, at most 8)
and `notes_read` (the time of the last read mark). Each link is
`{a, b, last, count, summary, active, from}`: two agent NAMEs, the latest
message's time and sender, the messages in the last 24 hours, a short plain
summary, and `active` while the latest one is under 10 minutes old.

## Your phone (Glint)

The Glint iPhone app is a remote for the same space: the same agents, threads,
taps and cards as this page and the Mac app.

1. **Pair.** On the Mac, run `python3 tap.py pair`. It opens a route from your
   phone to this Mac and prints a `glint://pair?url=...&token=...` link plus a QR
   code (also at `http://127.0.0.1:4747/pair`, which only this Mac can open). In
   Glint, open You, then Pair with your Mac, and scan it.
   - **Tailscale Serve** (private HTTPS, preferred) when this Mac is logged in to
     Tailscale. Log in on the Mac and on your phone, run `tap.py pair` again, and
     it switches over and closes ngrok.
   - **ngrok** otherwise: a public URL, reused while the tunnel runs (set
     `TAPSPACE_NGROK_DOMAIN` for a reserved domain). `tap.py pair --off` closes it.
2. **Token.** The server makes `data/token` on first start (mode 600). A request
   counts as local only when it comes from loopback with no proxy header
   (`X-Forwarded-*`, `Forwarded`, `X-Real-IP`, `Tailscale-User-Login`, `Ngrok-*`).
   Every other request needs `Authorization: Bearer <token>` or gets 401; local
   callers need nothing. `/api/chat/import` and `/pair` answer local callers only.
3. **Live updates.** `GET /api/wait?since=<cursor>&timeout=25` (30 max) returns
   `{cursor, changed: ["agents", "chat", "taps", "cards"]}` the moment any of
   them changes, at once when you are behind, and an empty `changed` on timeout.
   Start with `since=0`.
   Goal cards, notes, read marks and links ride the `agents` kind.
4. **Push.** `POST /api/devices {token, env: "sandbox"|"production"}` stores the
   phone in `data/devices.json`. Agent replies, tap comments and new or updated
   cards then send a notification, once an APNs key exists:
   `~/.config/glint/apns/AuthKey_<KEYID>.p8` plus `key.json`
   `{"key_id": "<KEYID>", "team_id": "289D9TAJCH", "topic": "com.tappinai.glint"}`.
   `GET /api/health` returns `{ok, version, push, time}`; `push` says whether the
   key is in place. Sends are logged to `data/push.log`.

## The presence HUD

Top right of the page. Fully automatic; agents report nothing. Rows are derived from the server's own request stream (`posted`, `iterating`, `waiting on your tap`), fade after about 2 minutes of quiet, and vanish after 5. A blocked `tap.py wait` doubles as a 3-second heartbeat.

The session counter scans `~/.claude/projects` transcript mtimes to count working vs. recent Claude Code sessions. If you don't use Claude Code it just shows zeros; everything else works the same. Links between masters come from the same transcripts; only each message's short summary is kept. `TAPSPACE_CLAUDE_DIR` points the link reader at another Claude Code folder (default `~/.claude`).

## Videos and the house rule

Keep recordings short (5 to 20 seconds, under ~60MB; the CLI enforces the cap). The server streams them with Range support so the page can scrub.

**Decided videos are deleted on the spot.** The moment you decide on a card (approve or changes), the server deletes that ticket's video files up to the decided version, and again at archive time. The card shows a "video reviewed, deleted per house rule" placeholder. Review artifacts shouldn't accumulate on your machine; a change order means the agent re-records for v2 anyway. Stills are never auto-deleted.

## Housekeeping

- Archive a finished campaign (cards vanish from the space, state is kept in `data/tickets.json`):
  ```bash
  curl -s -X POST 127.0.0.1:4747/api/archive -H 'content-type: application/json' -d '{"campaign":"my-feature"}'
  ```
- `data/` (tickets, captures, inbox) survives server restarts and is gitignored.
- The page self-reloads when `space/index.html` changes (a version stamp is baked at serve time and checked against `/api/state`), so upgrading never requires a manual refresh.

## Security

The server binds to `127.0.0.1` only. Callers on this Mac need no auth; everything else, which includes every request through a tunnel, needs the bearer token in `data/token` (see "Your phone"). Reach it from elsewhere only through `tap.py pair`, never by changing the bind address.

## Requirements

Python 3.8+. No packages. Any modern browser.

## Mac app

`mac/` is a native macOS app for the same server: the Visual and Chat tabs, plus a liquid pill in the menu bar (just right of the notch) that counts what is waiting on you and spreads into a list when you hover it. SwiftPM only, macOS 26.

On the Chat tab, a window wide enough for it gets a third column: one card per live master with its goal, its steps and the step it is on (masters publish these with `tap.py goal` and `tap.py step`). Under each master in the rail sit the cards you sent back with notes that went to that master, newest first, with a blue dot on the master. A row leaves when the master posts a new version, and they all clear when you open that master's thread (the web page and Glint clear too). When two masters message each other, their cards sit together, joined by a line that draws and pulses, and the rail says who each one is talking with.

Install (release build, bundled as `TapSpace.app` with its icon, ad-hoc signed, copied to `/Applications`). Installing never quits, relaunches or brings forward a TapSpace that is running: it keeps its build until you next open it.

```bash
mac/Scripts/install.sh                                            # build and install
BUNDLE_ONLY=1 mac/Scripts/install.sh                              # build and sign mac/.build/TapSpace.app only
OPEN=1 mac/Scripts/install.sh                                     # also open it behind the front app, when it is not running
OPEN=1 TAPSPACE_URL=http://127.0.0.1:4748 mac/Scripts/install.sh  # ... on another server
```

The app reads `TAPSPACE_URL` (default `http://127.0.0.1:4747`) and starts the server with `tap.py up` if it is down.

Flags, for verification:

| Flag | What it does |
|---|---|
| `--snapshot <dir>` | Renders every scene from the real views to 2x PNGs (Visual, a card in notes mode, lightbox, an agent thread, the same thread in a 1512 by 887 window, a thinking agent opened the way a click opens it, a tap thread, the pill collapsed, bulging and expanded), writes `scenes.json` (each scene, any problems, and the masters data the app decoded), and exits. It runs offscreen with no Dock icon and never takes focus, and it refuses to run (exit 2) unless `TAPSPACE_URL` names a scratch server on this Mac. |
| `--snapshot <dir>` with `TAPSPACE_LINK_FRAMES=1` | Instead of the scenes, checks the progress column's ordering and pairing rules and renders the line between two masters forming, chasing, turning and ending as 2x frames, 30 per second of motion (`f000.png`..., `frames.json`), offscreen like the scenes. |
| `--demo` | A scripted 10 s loop (tabs, notes, rail selection, a bubble flying in, the pill spreading and retracting) that repeats until you quit. Never sends anything. On screen. |
| `--fps` | Logs display-link frames per second each second, and whether each surface is moving, still or paused. |
| `--tab visual\|chat`, `--thread agent:NAME\|owner/repo#n` | Open on a tab or thread. |

Never test against the live server. The scripts work on a scratch one:

```bash
mac/Scripts/seed.sh 4748                      # scratch server: real screenshots, two masters with goal cards, two cards sent back with notes, chat
mac/Scripts/seed.sh --link <its data dir>     # the two masters exchange a message (active for 10 minutes): right before a render
mac/Scripts/seed.sh --copy 4749               # scratch server on a copy of the live cards, chat and agents
mac/Scripts/verify.sh 4748                    # build, seed, link, what the server serves, offscreen scenes and link.mp4, the app against the server
FPS=1 mac/Scripts/verify.sh 4748              # also the --demo fps run: a window and a second pill ON SCREEN
mac/Scripts/fps.sh http://127.0.0.1:4748 30   # --demo --fps for 30 s on screen, then a frame-rate summary and crash check
mac/Scripts/seed.sh --stop <its data dir>     # stop a scratch server seed.sh started
```

A scratch server never pushes to a phone. A seeded one reads only its own Claude Code folder (its one link is the seeded exchange); a copy reads this Mac's transcripts, read-only, for links between the real masters. seed.sh only ever wipes a folder it made itself.

The icon is `mac/Resources/TapSpace.icon` (Icon Composer format: a white tile with one blue dot); install.sh compiles it with `actool` into the app's `Assets.car` and `TapSpace.icns`.

## License

MIT. `space/anime.esm.js` is anime.js v4.1.4 by Julian Garnier, also MIT, vendored unmodified.
