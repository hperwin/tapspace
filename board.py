"""Flightdeck's board, live: the rows behind TapSpace's tap list.

One stream (GET /api/v1/journeys/stream) on Hayden's key keeps the board's current rows, keyed by
lower-cased journey URL; while it is down, GET /api/v1/journeys/fleet every 10 s instead. A removed
row is never trusted alone (a key that died looks the same on the wire): removals wait 1.5 s, then the
fleet decides. The key is Claude Code's own flightdeck sign-in, read from the login Keychain through
/usr/bin/security. This module never refreshes it, never writes it, and never logs or serves any of it.

server.py:  Board(on_change=...).start() once, when enabled(); snapshot() for the rows, the state and
            the error sentence; read(url) for one journey's final word (paced).
"""
import http.client
import json
import os
import queue
import random
import socket
import subprocess
import threading
import time
from collections import deque
from urllib.parse import quote, urlsplit

SECURITY = "/usr/bin/security"      # the only reader the Keychain item admits without a prompt
ITEM = "Claude Code-credentials"
API = "/api/v1/journeys/"
BACKOFF_BASE, BACKOFF_MAX, JITTER = 2.0, 60.0, 1.0   # reconnect: 2 s doubling to 60 s, plus 0-1 s
SILENCE = 60.0                      # no bytes this long on the open stream: reconnect
STEADY = 60.0                       # a stream open this long was healthy: the next drop backs off from 2 s again
COALESCE = 1.5                      # removals gathered this long, then one fleet read decides
CONFIRM_GAP = 5.0                   # removal checks at least this far apart; removals inside the gap join the next
CRED_EVERY = 60.0                   # Keychain re-read while there is no live key
DEAD_RETRY = 300.0                  # a key the board refused is tried again this often: some refusals reverse
UNREACHABLE_AFTER = 60.0            # no answer from the board this long while the stream is down
HOLD = 60.0                         # after a 429, no call of any kind this long (the key is shared)
READ_MAX, READ_WINDOW = 20, 60.0    # read(): at most 20 a minute
GET_TIMEOUT = 20.0                  # connect plus answer, for every call (the stream's headers too)
READ_TIMEOUT = 8.0                  # read() alone: its caller is server.py's one writer of the list
CRED_KINDS = frozenset({"anonymous", "malformed", "unknown", "mismatch", "revoked", "expired",
                        "undated", "signin-revoked", "signin-unchecked"})
NO_KEY = "Flightdeck isn't signed in on this Mac, so the tap list can't update."
RAN_OUT = "The Flightdeck sign-in on this Mac ran out, so the tap list can't update."
CANT_REACH = "Flightdeck's board can't be reached right now. This list may be out of date."


def keychain_cred():
    """{"token", "base", "exp"} from Claude Code's flightdeck sign-in, or None. Never raises, never logs."""
    try:
        r = subprocess.run([SECURITY, "find-generic-password", "-s", ITEM, "-w"], capture_output=True,
                           text=True, timeout=10, stdin=subprocess.DEVNULL)
        if r.returncode:
            return None
        ents = [v for k, v in json.loads(r.stdout).get("mcpOAuth", {}).items()
                if k.startswith("flightdeck|") and v.get("accessToken") and v.get("serverUrl")]
        e = max(ents, key=lambda v: v.get("expiresAt") or 0)
        return {"token": e["accessToken"], "base": e["serverUrl"].rstrip("/").removesuffix("/mcp"),
                "exp": (e.get("expiresAt") or 0) / 1000}
    except Exception:
        return None


def enabled():
    """On for the live server (default data dir) or when a board URL is given (tests, scratch runs)."""
    return not os.environ.get("TAPSPACE_DATA") or bool(os.environ.get("TAPSPACE_BOARD_URL"))


def frames(readline):
    """(event, data) for each frame of an SSE byte stream until EOF. A line starting with ":" is a
    keepalive; data lines join with newlines; id is read and dropped (the board never resumes)."""
    event, data = "", []
    while True:
        raw = readline()
        if not raw:
            return
        line = raw.decode("utf-8", "replace").rstrip("\r\n")
        if not line:
            if data:
                yield event or "message", "\n".join(data)
            event, data = "", []
        elif not line.startswith(":"):
            name, _, value = line.partition(":")
            value = value[1:] if value.startswith(" ") else value
            if name == "event":
                event = value
            elif name == "data":
                data.append(value)


def _verdict(status, body):
    """ok | cred | slow | fail for one answer; a refusal is read by its kind (a cut-off comes as 403)."""
    if status == 200:
        return "ok"
    try:
        kind = json.loads(body).get("kind")
    except Exception:
        kind = None
    if status == 401 or kind in CRED_KINDS:
        return "cred"
    return "slow" if status == 429 else "fail"


def _keyed(rows):
    """Board rows by lower-cased journey URL; None when the answer carries no row list."""
    if not isinstance(rows, list):
        return None
    return {r["journey"].lower(): r for r in rows if isinstance(r, dict) and isinstance(r.get("journey"), str)}


class Board:
    def __init__(self, base=None, key=None, on_change=None, cred=None, poll_every=10.0, clock=time.time):
        if cred is None and (base or key):                    # given (tests, TAPSPACE_BOARD_*): never the Keychain
            cred = lambda: {"token": key, "base": base, "exp": 0} if base and key else None
        self._cred, self.on_change, self.poll_every, self.clock = cred, on_change, poll_every, clock
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._q = queue.Queue()
        self._rows, self._at, self._loaded, self._state, self._error = {}, 0.0, False, "off", ""
        self._token = self._base = self._dead = None
        self._exp = self._dead_at = 0
        self._hold_until = self._fleet_at = 0.0
        self._reads = deque()
        self._heard = time.monotonic()
        self._poll_at = None
        self._sock = None
        self._thread = self._pump_thread = None
        self._n = 0

    # ---- the contract ----
    def start(self):
        with self._lock:
            if self._thread or self._stop.is_set():
                return
            self._thread = threading.Thread(target=self._run, name="board", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._shut()
        self._q.put((None, "stop", None))
        for t in (self._thread, self._pump_thread):
            if t and t is not threading.current_thread():
                t.join(5)
        with self._lock:
            self._state, self._error = "off", ""

    def snapshot(self):
        with self._lock:
            return {"loaded": self._loaded, "rows": dict(self._rows), "at": self._at,
                    "state": self._state, "error": self._error}

    def read(self, journey_url):
        """The board's read answer for one journey (R11), or None: on any failure, without a live key, during a
        hold, and at once when READ_MAX reads already ran in the last READ_WINDOW seconds (never waits for a
        slot). One call takes at most about READ_TIMEOUT."""
        with self._lock:
            now = time.monotonic()
            while self._reads and now - self._reads[0] >= READ_WINDOW:
                self._reads.popleft()
            if self._stop.is_set() or not self._live() or now < self._hold_until or len(self._reads) >= READ_MAX:
                return None
            self._reads.append(now)
            token, base = self._token, self._base
        v, obj = self._get(base, token, "read?journey=" + quote(journey_url, safe=""), READ_TIMEOUT)
        if v == "cred":
            self._q.put((None, "cred", token))
        return obj if v == "ok" and isinstance(obj, dict) and obj.get("ok") is not False else None

    # ---- the board thread ----
    def _run(self):
        self._reread()
        fails = 0
        while not self._stop.is_set():
            if not self._live():
                self._put(state="expired" if self._token and self._base else "signed-out")
                if self._idle(CRED_EVERY):
                    return
                self._reread()
                if self._dead and time.monotonic() - self._dead_at >= DEAD_RETRY:
                    with self._lock:
                        self._dead = None                      # the same key once more (A2.6)
                continue
            held = self._hold_until - time.monotonic()
            if held > 0:
                if self._idle(held):
                    return
                continue
            if self._state not in ("live", "polling", "connecting", "expired"):   # a retried key still reads ran out
                self._put(state="connecting")
            how = self._stream()
            if how == "stop":
                return
            if how in ("rotated", "refused"):
                fails = 0
                continue
            if self._poll_at is None:
                self._poll_at = time.monotonic() + self.poll_every
            fails = 1 if how == "lost" else fails + 1
            if self._down(time.monotonic() + self._delay(fails)):
                return

    def _stream(self):
        """One stream, from open to end: stop | rotated | refused | lost (after its snapshot, open STEADY or longer)
        | failed. A stream lost sooner counts as failed, so the backoff keeps doubling (A2.8)."""
        token, base = self._token, self._base
        try:
            sock, r = self._open(base, token, "stream", "text/event-stream", GET_TIMEOUT)
        except Exception:                                      # refused, reset, bad URL: all one failure
            return "failed"
        if r.status != 200:
            try:
                body = r.read(65536)
            except Exception:
                body = b""
            r.close()
            return self._refusal(_verdict(r.status, body), token, "failed")
        self._heard = opened = time.monotonic()
        self._n += 1
        sock.settimeout(SILENCE)
        self._sock = sock
        self._pump_thread = threading.Thread(target=self._pump, args=(self._n, r), name="board-stream", daemon=True)
        self._pump_thread.start()
        try:
            how = self._listen(self._n, token)
            return "failed" if how == "lost" and time.monotonic() - opened < STEADY else how
        finally:
            self._shut()
            self._heard = time.monotonic()

    def _listen(self, n, token):
        live, due = False, None
        while True:
            if due is not None:                                    # the confirm is a call: a hold delays it,
                due = max(due, self._hold_until, self._fleet_at + CONFIRM_GAP)   # and it keeps its gap (A2.7)
                if time.monotonic() >= due:                        # the removal window closed (R2)
                    due = None
                    v = self._confirm()
                    if v != "ok":
                        return self._refusal(v, token, "lost" if live else "failed")
                    continue
            item = self._next(None if due is None else due - time.monotonic())
            if self._stop.is_set():
                return "stop"
            if item is None:
                continue
            m, ev, data = item
            if m is None:
                if ev == "cred" and data == self._token:
                    return self._refusal("cred", data, None)
                continue
            if m != n:
                continue
            if ev is None:
                return "lost" if live else "failed"
            try:
                obj = json.loads(data)
            except ValueError:
                continue
            if not isinstance(obj, dict):
                continue
            if ev == "snapshot":
                rows = _keyed(obj.get("rows"))
                if rows is None:
                    continue
                if not rows and self._rows:
                    v = self._confirm("live")
                    if v != "ok":
                        return self._refusal(v, token, "failed")
                else:
                    self._put(rows, "live")
                live, self._poll_at = True, None
            elif ev == "change":
                j = str(obj.get("journey") or "").lower()
                if obj.get("change") == "removed":
                    if j in self._rows:                            # never applied: the fleet decides
                        due = due or time.monotonic() + COALESCE
                elif live and obj.get("change") in ("added", "changed") and isinstance(obj.get("row"), dict) and j:
                    with self._lock:
                        rows = dict(self._rows)
                    rows[j] = obj["row"]
                    self._put(rows)

    def _down(self, retry_at):
        """Wait out one backoff, polling the fleet every poll_every meanwhile. True when stopped."""
        self._put(state="polling" if self._state == "polling" else "connecting")
        while True:
            now = time.monotonic()
            if now >= retry_at:
                return False
            if self._poll_at is not None and now >= self._poll_at and now >= self._hold_until:
                v = self._confirm("polling")
                self._poll_at = time.monotonic() + self.poll_every   # from the answer: the door never sees two closer
                if v != "ok" and self._refusal(v, self._token, None) in ("rotated", "refused"):
                    return False
                if v != "ok":
                    self._put(state="connecting")
            wake = retry_at
            if self._poll_at is not None:                          # a due poll waits out a hold, never spins
                wake = min(wake, max(self._poll_at, self._hold_until))
            item = self._next(wake - time.monotonic())
            if self._stop.is_set():
                return True
            if item and item[0] is None and item[1] == "cred" and item[2] == self._token:
                self._refusal("cred", item[2], None)
                return False

    def _refusal(self, v, token, otherwise):
        if v == "cred":
            if token == self._token:
                with self._lock:
                    self._dead, self._dead_at = token, time.monotonic()
                self._reread()                                 # R3: at once
            return "rotated" if self._live() else "refused"
        if v == "slow":
            with self._lock:
                self._hold_until = time.monotonic() + HOLD
        return otherwise

    def _confirm(self, state=None):
        """One fleet read with the current key; a 200 replaces the rows (the board's truth)."""
        v, obj = self._get(self._base, self._token, "fleet")
        self._fleet_at = time.monotonic()
        rows = _keyed(obj.get("rows")) if v == "ok" and isinstance(obj, dict) else None
        if v == "ok" and rows is None:
            return "fail"
        if v == "ok":
            self._put(rows, state)
        return v

    # ---- plumbing ----
    def _open(self, base, token, verb, accept, timeout):
        u = urlsplit(base)
        c = (http.client.HTTPSConnection if u.scheme == "https" else http.client.HTTPConnection)(
            u.hostname, u.port, timeout=timeout)
        try:
            c.request("GET", u.path.rstrip("/") + API + verb, headers={"Authorization": "Bearer " + token, "Accept": accept})
            sock = c.sock                                      # a Connection: close answer takes it off c
            return sock, c.getresponse()
        except BaseException:
            c.close()                                          # a timed-out or refused call leaves no socket behind
            raise

    def _get(self, base, token, verb, timeout=None):
        """One JSON GET: (verdict, object or None). A 429 starts the hold; a 200 is contact."""
        try:
            _, r = self._open(base, token, verb, "application/json", GET_TIMEOUT if timeout is None else timeout)
            with r:
                status, body = r.status, r.read()
        except Exception:
            return "fail", None
        v = _verdict(status, body)
        if v == "slow":
            with self._lock:
                self._hold_until = time.monotonic() + HOLD
        if v != "ok":
            return v, None
        try:
            obj = json.loads(body)
        except ValueError:
            return "fail", None
        self._heard = time.monotonic()
        return "ok", obj

    def _pump(self, n, r):
        try:
            for ev, data in frames(r.readline):
                self._q.put((n, ev, data))
        except Exception:
            pass
        finally:
            r.close()
            self._q.put((n, None, None))

    def _shut(self):
        s, self._sock = self._sock, None
        if s:
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def _next(self, timeout):
        try:
            return self._q.get(timeout=None if timeout is None else max(0.0, timeout))
        except queue.Empty:
            return None

    def _idle(self, seconds):
        end = time.monotonic() + seconds
        while not self._stop.is_set():
            left = end - time.monotonic()
            if left <= 0:
                return False
            self._next(left)
        return True

    def _reread(self):
        try:
            c = (self._cred or keychain_cred)() or {}
        except Exception:
            c = {}
        with self._lock:
            self._token, self._base, self._exp = c.get("token") or None, c.get("base") or None, c.get("exp") or 0

    def _live(self):
        return bool(self._token and self._base and self._token != self._dead
                    and not (self._exp and self._exp <= self.clock()))

    def _delay(self, fails):
        return min(BACKOFF_BASE * 2 ** (fails - 1), BACKOFF_MAX) + JITTER * random.random()

    def _put(self, rows=None, state=None):
        """The one writer of what snapshot() serves; calls on_change when any of it moved. Silent once stopped."""
        if self._stop.is_set():
            return
        with self._lock:
            before = (self._rows, self._loaded, self._state, self._error)
            if rows is not None and (rows != self._rows or not self._loaded):
                self._rows, self._at, self._loaded = rows, self.clock(), True
            if state:
                self._state = state
            s = self._state
            self._error = (NO_KEY if s == "signed-out" else RAN_OUT if s == "expired"
                           else CANT_REACH if s in ("connecting", "polling")
                           and time.monotonic() - self._heard > UNREACHABLE_AFTER else "")
            moved = before != (self._rows, self._loaded, self._state, self._error)
        if moved and self.on_change and not self._stop.is_set():
            try:
                self.on_change()
            except Exception:
                pass
