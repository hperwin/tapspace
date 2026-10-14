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
              [--note TEXT] [--campaign TAG]        -> prints TICKET_ID
tap.py update TICKET (--image X.png | --video X.mp4) [--note TEXT]
tap.py wait   TICKET [--timeout 900]                -> exit 0 approved / 3 changes / 4 timeout
tap.py status TICKET                                -> ticket JSON, never blocks
tap.py up                                           -> idempotent start + health check
tap.py doctor                                       -> server, live cards, activity, sessions
```

Point it at a non-default server with `TAPSPACE_URL` (default `http://127.0.0.1:4747`).

## HTTP API (for agents that aren't Python)

`tap.py` wraps five of the endpoints below (`post`, `update`, `ticket`, `state`, `activity`); direct HTTP integrators have the full table. Any agent that can make HTTP requests can integrate.

| Method | Path | Body | Returns |
|---|---|---|---|
| POST | `/api/post` | `{agent, title, image_b64 \| video_b64 (+video_ext mp4\|mov), note?, campaign?}` | `{ok, id, version: 1}` |
| POST | `/api/update` | `{id, image_b64 \| video_b64 (+video_ext), note?}` | `{ok, id, version}` |
| POST | `/api/decide` | `{id, action: approve\|change, message?}` | `{ok}` (page only; agents never call this) |
| POST | `/api/archive` | `{id}` or `{campaign}` or `{all: true}` | `{ok, archived: n}` |
| GET | `/api/state` | | all live tickets |
| GET | `/api/ticket/<id>` | | one ticket, with `versions` and `decisions` |
| GET | `/api/activity` | | presence rows + session counts |
| GET | `/shots/<id>/<v>.<ext>` | | the capture; videos support Range requests, so they scrub |

`image_b64` must be the raw base64 of PNG bytes, and `video_b64` of mp4/mov bytes, with **no `data:` URI prefix**. The server stores what you send without validating or converting the format, and a prefixed or malformed string fails that request without a clean JSON error.

A worked `post` from plain shell:

```bash
curl -s -X POST 127.0.0.1:4747/api/post -H 'content-type: application/json' \
  -d "{\"agent\":\"T2-cards\",\"title\":\"Combined stat card\",\"campaign\":\"my-feature\",\"image_b64\":\"$(base64 < shot.png | tr -d '\n')\"}"
# -> {"ok": true, "id": "3f7de0b7af", "version": 1}
```

Every decision also appends one line to `data/INBOX.jsonl`:

```json
{"kind": "tapspace-decision", "ticket": "3f7de0b7af", "agent": "T2-cards",
 "title": "Combined stat card", "campaign": "my-feature", "version": 2,
 "action": "change", "message": "less white, make the pills look like glass",
 "received_epoch": 1755640000.0}
```

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
  An agent shows *Here* while it is listening, *Thinking* while your message
  waits for its reply, and *Away* otherwise.
- **Taps.** GitHub issues you authored with the tap label
  (`TAPSPACE_TAP_LABEL`, default `nava`), read through your `gh` login. Each
  tap shows its goals, its comments, and whether your messages were seen
  (an 👀 reaction). Writing to a tap posts an issue comment as you. Taps
  are scoped by issue author, so other people's taps never appear.

Endpoints: `GET /api/agents`, `GET /api/chat?agent=NAME`,
`GET /api/chat/listen?agent=NAME`, `POST /api/chat {agent, body}`,
`POST /api/chat/reply {agent, body}`, `GET /api/taps`,
`POST /api/taps/say {key: "owner/repo#n", body}`, `POST /api/taps/refresh`.
The endpoints that speak for you accept same-origin JSON only.

## The presence HUD

Top right of the page. Fully automatic; agents report nothing. Rows are derived from the server's own request stream (`posted`, `iterating`, `waiting on your tap`), fade after about 2 minutes of quiet, and vanish after 5. A blocked `tap.py wait` doubles as a 3-second heartbeat.

The session counter scans `~/.claude/projects` transcript mtimes to count working vs. recent Claude Code sessions. If you don't use Claude Code it just shows zeros; everything else works the same.

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

The server binds to `127.0.0.1` only and has no auth. It is built for one human and their agents on one machine. Do not expose it beyond localhost.

## Requirements

Python 3.8+. No packages. Any modern browser.

## License

MIT. `space/anime.esm.js` is anime.js v4.1.4 by Julian Garnier, also MIT, vendored unmodified.
