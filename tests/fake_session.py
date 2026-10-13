"""A synthetic session: a 5120x2160 test-pattern video plus a scripted timeline.

Two tiled windows (left/right half), the cursor wanders, clicks in the right
window at 2 s and 2.6 s, types at 4.5-5.5 s, focus moves to the left window at 7 s.
    python3 tests/fake_session.py OUTDIR [--no-video]
"""

import json
import os
import subprocess
import sys

W, H, DUR = 5120, 2160, 10.0
LEFT = {"address": "0xa", "class": "kitty", "title": "left", "at": [10, 54], "size": [2540, 2096],
        "workspace": 1, "floating": False, "fullscreen": 0, "monitor": 0, "mapped": True, "hidden": False, "pid": 1}
RIGHT = dict(LEFT, address="0xb", title="right", **{"at": [2570, 54]})
S = 1_000_000_000


def records():
    yield {"t": 0, "type": "session_start", "format": 1, "started": "2026-01-01T00:00:00+00:00", "host": "fake",
           "monitors": [{"name": "DP-3", "x": 0, "y": 0, "width": W, "height": H, "scale": 1, "transform": 0,
                         "workspace": 1, "special": 0, "focused": True, "locked": False}],
           "windows": [LEFT, RIGHT], "focus": "0xb", "sync": "fake"}
    yield {"t": 0, "type": "stream_start", "stream": "screen", "file": "screen.mkv", "monitor": "DP-3",
           "logical": [0, 0, W, H], "pixels": [W, H], "scale": 1, "fps": 30, "codec": "h264", "audio": None,
           "cursor": "embedded"}
    for i in range(int(DUR * 60)):  # cursor: right window until 6.5 s, then drifts left
        t = i / 60
        x = 3800 + 400 * (t / 6.5) if t < 6.5 else 4200 - (t - 6.5) * 900
        yield {"t": int(t * S), "type": "cursor", "x": round(x), "y": round(800 + 200 * (t % 3))}
    for t in (2.0, 2.6):
        yield {"t": int(t * S), "type": "button", "code": "BTN_LEFT", "value": 1}
        yield {"t": int((t + 0.1) * S), "type": "button", "code": "BTN_LEFT", "value": 0}
    for k in range(10):
        yield {"t": int((4.5 + k * 0.1) * S), "type": "key", "code": "KEY_A", "value": 1, "mods": []}
    yield {"t": int(7.0 * S), "type": "window_focus", "address": "0xa"}
    yield {"t": int(DUR * S), "type": "session_end", "duration": int(DUR * S), "counts": {}}


def main():
    out = sys.argv[1]
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, "session.jsonl"), "w") as f:
        for r in records():
            f.write(json.dumps(r) + "\n")
    if "--no-video" not in sys.argv:
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=s={W}x{H}:r=30:d={DUR}",
                        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
                        os.path.join(out, "screen.mkv")], check=True)


if __name__ == "__main__":
    main()
