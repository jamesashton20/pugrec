"""Append-only JSONL: one writer thread, readers that survive a crash.

Every record goes through one Writer, so lines from the cursor poller, the
window poller and the input helper never interleave. The file is only ever
appended to; it's flushed every FLUSH_S, so a crash loses at most that much and
leaves at worst a truncated last line, which read() skips.
"""

import json
import queue
import threading
import time

FLUSH_S = 0.1


class Writer:
    def __init__(self, path):
        self._f = open(path, "a", encoding="utf-8")
        self._q = queue.Queue()
        self._t = threading.Thread(target=self._run, name="jsonl-writer", daemon=True)
        self._t.start()

    def write(self, record):
        self._q.put(record)

    def close(self):
        self._q.put(None)
        self._t.join()
        self._f.close()

    def _run(self):
        last_flush = time.monotonic()
        while True:
            try:
                rec = self._q.get(timeout=FLUSH_S)
            except queue.Empty:
                rec = ...
            if rec is None:
                self._f.flush()
                return
            if rec is not ...:
                self._f.write(json.dumps(rec, separators=(",", ":"), ensure_ascii=False) + "\n")
            now = time.monotonic()
            if now - last_flush >= FLUSH_S:
                self._f.flush()
                last_flush = now


def read(path):
    """All records of a session file, sorted by t. Skips unparseable lines
    (a crash's truncated last line); sources write out of order, so sort."""
    records = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    records.sort(key=lambda r: r.get("t", 0))
    return records
