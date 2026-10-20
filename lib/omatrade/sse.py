"""Server-sent events: a line parser that follows the WHATWG rules, and the
reconnect backoff."""

import random
from dataclasses import dataclass

MAX_EVENT_BYTES = 256 * 1024


@dataclass
class Event:
    event: str = "message"
    data: str = ""
    id: str = None
    retry: int = None


class SSEParser:
    """Feed it lines (bytes or str, with or without the line ending); it
    returns an Event when a blank line completes one, else None.

    - `data:` lines accumulate, joined with newlines.
    - One space after the colon is dropped; lines starting with a colon are
      comments (heartbeats).
    - `id:` persists as the last event id; ids containing NUL are ignored.
    - `retry:` must be all digits.
    - An event that grows beyond MAX_EVENT_BYTES is discarded, so a broken
      or hostile stream cannot exhaust memory.
    """

    def __init__(self):
        self.last_event_id = None
        self.retry = None
        self._reset()

    def _reset(self):
        self._event = ""
        self._data = []
        self._size = 0
        self._overflow = False
        self._id_seen = False

    def feed(self, line):
        if isinstance(line, bytes):
            line = line.decode("utf-8", errors="replace")
        line = line.rstrip("\r\n")
        if line.startswith("\ufeff"):
            line = line[1:]
        if line == "":
            return self._dispatch()
        if line.startswith(":"):
            return None
        field, sep, value = line.partition(":")
        if sep and value.startswith(" "):
            value = value[1:]
        if field == "data":
            self._size += len(value) + 1
            if self._size > MAX_EVENT_BYTES:
                self._overflow = True
            elif not self._overflow:
                self._data.append(value)
        elif field == "event":
            self._event = value
        elif field == "id":
            if "\x00" not in value:
                self.last_event_id = value
                self._id_seen = True
        elif field == "retry":
            if value.isdigit():
                self.retry = int(value)
        return None

    def _dispatch(self):
        if self._overflow or not self._data:
            self._reset()
            return None
        event = Event(event=self._event or "message", data="\n".join(self._data),
                      id=self.last_event_id, retry=self.retry)
        self._reset()
        return event


class Backoff:
    """Exponential backoff with full jitter: the n-th wait is uniform in
    [floor, min(cap, base * 2**n)]. Full jitter matters at scale: after a
    deploy every client loses its stream in the same second, and without it
    they would all come back in the same second too."""

    def __init__(self, base=1.0, cap=300.0, floor=0.0, rng=None):
        self.base = base
        self.cap = cap
        self.floor = floor
        self.rng = rng or random.Random()
        self.attempt = 0

    def next(self, minimum=None):
        ceiling = min(self.cap, self.base * (2 ** min(self.attempt, 30)))
        self.attempt += 1
        low = max(self.floor, 0.0)
        delay = self.rng.uniform(low, max(low, ceiling))
        if minimum is not None:
            delay = max(delay, min(float(minimum), self.cap))
        return delay

    def reset(self):
        self.attempt = 0
