#!/usr/bin/python3 -I
"""Root helper: keyboard + mouse button/wheel events -> JSON lines on stdout.

Started by the recorder for one recording only:
    pkexec /usr/bin/python3 -I /abs/path/input_helper.py
Wayland gives no unprivileged way to see keys or clicks; /dev/input needs root
(or the `input` group, which James chose not to grant permanently). Run without
root it reads whatever the desktop ACL allows (main-pc: the mouse, not the
keyboard) - the recorder's fallback when the password prompt is dismissed.

What it does and doesn't do:
- reads keyboards/mice/touchpads, never grabs them (input still reaches the desktop)
- writes only to stdout; no files, no network
- exits when the recorder closes its stdin (how it's stopped: it can't be
  signalled, it's root), when stdout closes, or when the recorder dies (PR_SET_PDEATHSIG)
- ignores SIGINT: Ctrl+C in the recorder's terminal is the recorder's to handle
- relative mouse motion is ignored (the recorder gets the cursor from Hyprland)
- security keys (YubiKey & co.) are skipped: they "type" one-time codes

Lines: {"mono": <CLOCK_MONOTONIC ns>, "kind": "key"|"button"|"wheel",
        "code": "KEY_A", "value": 0 up / 1 down / 2 repeat (wheel: steps), "dev": "..."}
Stdlib + python-evdev only, so it runs under `python3 -I` as root.
"""

import ctypes
import fcntl
import json
import os
import re
import select
import signal
import struct
import sys
import time

import evdev
from evdev import ecodes

EVIOCSCLOCKID = 0x400445A0  # _IOW('E', 0xa0, int): stamp this fd's events with another clock
CLOCK_MONOTONIC = 1
PR_SET_PDEATHSIG = 1
RESCAN_S = 2.0
SKIP_RE = re.compile(r"yubi|nitrokey|solokey|onlykey|token2|feitian", re.I)


def wanted(dev):
    if SKIP_RE.search(dev.name):
        return False
    caps = dev.capabilities()
    keys = set(caps.get(ecodes.EV_KEY, []))
    return ecodes.KEY_A in keys or ecodes.BTN_LEFT in keys or ecodes.BTN_TOUCH in keys


def open_new(devices):
    for path in evdev.list_devices():
        if path in devices:
            continue
        try:
            dev = evdev.InputDevice(path)
            if not wanted(dev):
                dev.close()
                continue
            fcntl.ioctl(dev.fd, EVIOCSCLOCKID, struct.pack("i", CLOCK_MONOTONIC))
            devices[path] = dev
        except OSError:
            continue


def name_of(code, table):
    n = table.get(code, str(code))
    return n[0] if isinstance(n, (list, tuple)) else n  # aliases: ("BTN_LEFT", "BTN_MOUSE")


def emit(rec):
    sys.stdout.write(json.dumps(rec, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def main():
    ctypes.CDLL(None, use_errno=True).prctl(PR_SET_PDEATHSIG, signal.SIGTERM)
    if os.getppid() == 1:  # recorder already gone
        return
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    # Stopping: the recorder closes our stdin. It can't signal us (we're root),
    # and Ctrl+C in its terminal isn't ours to act on.
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    stdin = sys.stdin.fileno()
    devices = {}
    open_new(devices)
    emit({"kind": "hello", "mono": time.monotonic_ns(), "devices": sorted(d.name for d in devices.values())})
    next_scan = time.monotonic() + RESCAN_S
    while True:
        fds = {d.fd: (p, d) for p, d in devices.items()}
        r, _, _ = select.select([stdin, *fds], [], [], RESCAN_S)
        if stdin in r and not os.read(stdin, 4096):
            return
        for fd in r:
            if fd == stdin:
                continue
            path, dev = fds[fd]
            try:
                evs = dev.read()
                for e in evs:
                    mono = e.sec * 1_000_000_000 + e.usec * 1000
                    if e.type == ecodes.EV_KEY:
                        is_btn = ecodes.BTN_MISC <= e.code < ecodes.KEY_OK
                        emit({"mono": mono, "kind": "button" if is_btn else "key",
                              "code": name_of(e.code, ecodes.BTN if is_btn else ecodes.KEY),
                              "value": e.value, "dev": dev.name})
                    elif e.type == ecodes.EV_REL and e.code in (ecodes.REL_WHEEL, ecodes.REL_HWHEEL):
                        emit({"mono": mono, "kind": "wheel",
                              "code": "vertical" if e.code == ecodes.REL_WHEEL else "horizontal",
                              "value": e.value, "dev": dev.name})
            except OSError:  # unplugged
                devices.pop(path, None)
        if time.monotonic() >= next_scan:
            open_new(devices)
            next_scan = time.monotonic() + RESCAN_S


if __name__ == "__main__":
    try:
        main()
    except (BrokenPipeError, KeyboardInterrupt):
        pass
