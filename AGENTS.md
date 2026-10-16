# TapSpace agent contract

This file is the integration contract between your agents and TapSpace. The **Agent duties** section is written to be pasted directly into the prompt or brief of any agent whose deliverable is something a human will look at: UI, layout, tables, cards, charts, rendered docs. Logic-only work (no user-visible surface) skips TapSpace entirely.

It assumes the server is at `http://127.0.0.1:4747` and `tap.py` is reachable at a known path. Adjust paths to your setup.

---

## Agent duties (paste this into every design-stream agent prompt)

1. **Real renders only.** Build the actual thing and capture the running surface:
   - iOS: `xcrun simctl io <your-UDID> screenshot shot.png` of your own simulator.
   - Web: a Playwright (or equivalent) screenshot of the served page.
   - A table, card, or document: render the real HTML and screenshot it.
   - NEVER a generated or mocked image, and never a stale tree. Rebuild or rebase before capture; a screenshot of a stale tree is evidence of nothing.

2. **Motion units are VIDEOS.** Any unit whose value is motion (transitions, entrance animations, arcs, live-updating state) posts a short screen recording, not a still: `tap.py post/update --video clip.mp4` (mp4 or mov, 5 to 20 seconds, under ~60MB). The space plays it inline with native scrub controls. Stills remain correct for static composition.
   - iOS recording recipe, including the known trap: `xcrun simctl io <UDID> recordVideo --codec h264 clip.mp4 & echo $! > rec.pid`, drive the motion, then stop with `kill -INT $(cat rec.pid)` and WAIT for the file to finalize. Never hard-timeout a recording: a timeout-killed recording leaves a device-side lock that silently breaks later recordings (clear it with simulator shutdown + boot, then a short probe recording before the real capture).
   - Web: Playwright video or any bounded screen capture.

3. **Post the card the moment a visual unit is finished**, not when your whole task is done:
   ```bash
   python3 /path/to/tapspace/tap.py post --agent T2-cards --title "Combined stat card" --image shot.png --campaign <campaign>
   ```
   Save the printed TICKET_ID in your report. One card per coherent unit; a stream with five units posts five cards over its life. A lane that runs inside its master's own Claude Code process (a workflow agent or an in-process teammate) lands under that master by itself (`tap.py` tells the server which Claude Code session it runs in). A lane in its OWN process (`claude -p`, Codex, a separate tmux session) or outside Claude Code must add `--owner "<master's chat NAME>"`.

4. **Do not park on the card.** Keep working your next unit. Check decisions at natural boundaries (`tap.py status TICKET`), or block with `tap.py wait TICKET --timeout 900` only when the decision truly gates your next edit. Exit codes: 0 = approved, 3 = changes (the reviewer's message is on stdout), 4 = timeout (note it in your report and continue other work; your orchestrator will wake you when the decision lands).

5. **Changes come back as work.** The reviewer's message is a change order on that unit: iterate, re-capture, then
   ```bash
   python3 /path/to/tapspace/tap.py update TICKET --image shot2.png --note "v2: <what changed>"
   ```
   Same ticket, new version, back in the space. Decisions are versioned: a "changes" filed on v1 is never the verdict on v2.

6. **Approve ratifies the DESIGN of that version.** Note "TapSpace approved vN" plus the ticket id in your report or PR body. Approval is not a merge gate, not a substitute for tests, and not code review; those run on their own tracks.

7. **Never decide for the human.** Agents never POST to `/api/decide`. That endpoint belongs to the space page alone. A card sitting unjudged is blocked-external for that unit, never a reason to fabricate approval; keep building your other units.

8. **Keep your local recording until your card is decided**, then delete your local copy too. The server deletes a ticket's video files the moment the reviewer decides on them (the card shows a placeholder), so a change order means you re-record for the next version anyway.

---

## Orchestrator duties (if you run a multi-agent fleet)

You don't need an orchestrator: a solo agent can post and `wait`. But if one process coordinates several agents, it owns the relay:

1. **At campaign start**, ensure the rig is up (`python3 tap.py up`, idempotent, any agent may run it) and arm ONE watcher on the inbox:
   ```bash
   tail -n 0 -f /path/to/tapspace/data/INBOX.jsonl
   ```
   In Claude Code this is a Monitor on that tail command. In any other framework it's the same tail plus your own event handling. After a restart or context compaction, check whether a live tail already exists before re-arming: duplication, not death, is the usual failure mode.

2. **Relay every decision event to the owning agent** (`agent` names the lane, `owner` the master whose session posted the card): forward the action and message verbatim, AND append it to that agent's brief or task file under a `## TAPSPACE DECISIONS` heading. The message is the notification; the file is the source of truth that survives restarts. When several masters share one space, relay the events whose `owner` is your own chat NAME, plus any without an owner whose `agent` is one of your lanes.

3. **Relay approvals too.** An approved card may be exactly the gate a lane is politely polling on.

4. **Never answer for the human.** An unjudged card is blocked-external for that unit. The stream keeps building its other units.

5. **At campaign wrap**, archive the campaign's tickets and copy any captures you want to keep out of `data/shots/`:
   ```bash
   curl -s -X POST 127.0.0.1:4747/api/archive -H 'content-type: application/json' -d '{"campaign":"<campaign>"}'
   ```
   The server stays up; the space empties itself.

6. **Keep your goal card current** (a master in the Chat tab). Publish the run once, then move it at each real completion signal (a sentinel, a finished workflow, an opened PR), never on intent:
   ```bash
   python3 /path/to/tapspace/tap.py goal --as "NAME" "<the outcome the human will see, one plain line>" --steps "Audit|Spec|Build|Review|PR open"
   python3 /path/to/tapspace/tap.py step --as "NAME" next --note "14 of 20 builders done"
   ```
   The goal is one line under 90 characters; steps are 3 to 7 phases of one or two words, in order, and scope rows roll up into the note. The card lives under your NAME, so a relay keeps it: the next car reads it with `tap.py progress --as "NAME"` and keeps moving it. Lanes never publish one.

---

## Protocol reference

| Actor | Action | Mechanism |
|---|---|---|
| Agent | new card | `tap.py post` prints ticket id, status `waiting` |
| Human | approve | space page. Status `approved`, INBOX line, card check-animates out |
| Human | change notes | space page. Status `changes`, INBOX line, card shows "with \<agent\>" |
| Agent | iteration | `tap.py update`. Version+1, status `waiting`, card re-blurs in |
| Agent | read decision | `tap.py wait` (blocking) / `tap.py status` (instant) |
| Orchestrator | wake + truth | tail on INBOX -> relay verbatim + append to the agent's brief |
| Master | goal card | `tap.py goal` once, then `tap.py step` as each step lands |

Decision JSON, as returned by `tap.py wait` (and stored per-version in the ticket's `decisions` array):

```json
{"action": "change", "message": "less white, make the pills look like glass", "version": 2, "ts": 1755640000.0}
```

The `INBOX.jsonl` event carries a richer shape, and it is what names the owning agent for the relay:

```json
{"kind": "tapspace-decision", "ticket": "3f7de0b7af", "agent": "T2-cards",
 "owner": "Claude \u00b7 main", "title": "Combined stat card", "campaign": "my-feature",
 "version": 2, "action": "change", "message": "less white, make the pills look like glass",
 "received_epoch": 1755640000.0}
```

`owner` is the master's chat NAME (ASCII JSON: the `·` reads `\u00b7`).

## Notes that keep the loop honest

- **The presence HUD is automatic.** Do not build status reporting into agent prompts; the server derives presence from its own request stream. The HUD sees traffic, not claims. Card owners and agent-to-agent links are detected the same way, so lanes never report them. The one thing a master publishes on purpose is its goal card (Orchestrator duty 6).
- **On v2+ the space pixel-diffs the new capture against the previous version** and highlights changed regions. This only works when captures are same-surface, same-device renders, which is one more reason they must be.
- **Campaign tags keep wraps clean** when several fleets share one space. Keep agent names unique within a campaign (presence rows are keyed by campaign + agent).
- **The human views the space in their own browser.** If you verify the page with an automation browser, close it when done; never leave a script-pinned viewport open as the human's viewer.
