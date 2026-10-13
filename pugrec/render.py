"""Render a session to a normal-sized video with an automatic virtual camera.

The source is a huge screen (5120x2160 on main-pc); the output is e.g.
1920x1080 H.264 MP4, which plays everywhere. A virtual camera moves over the
screen:
  - activity (clicks, typing, scrolling) -> zoom in on it, starting a little
    *before* it happens (the timeline is known in advance)
  - otherwise -> frame the focused window
  - never wider than the largest output-aspect rectangle that fits the screen,
    so there are no black bars
and a critically damped spring smooths it. Crops are sub-pixel (OpenCV), so slow
pans don't jitter the way ffmpeg's zoompan does.

Needs numpy + opencv (the project .venv); recording doesn't.
"""

import bisect
import math
import os
import subprocess
import sys

from . import jsonl
from .timeline import Timeline  # noqa: F401  (moved; re-exported for old callers)

ORANGE_BGR = (44, 107, 255)  # FieldPal orange on dark, #ff6b2c


# ------------------------------------------------------------------ camera --
class Camera:
    """Target view per moment + spring smoothing. Views are (cx, cy, width) in
    screen pixels; height = width / aspect."""

    def __init__(self, tl, aspect, zoom=2.0, hold=1.6, lead=0.45, stiffness=7.0):
        self.tl, self.aspect = tl, aspect
        self.max_w = min(tl.W, tl.H * aspect)        # widest view without bars
        self.min_w = self.max_w / zoom
        self.hold, self.lead, self.k = hold, lead, stiffness

    def clamp(self, cx, cy, w):
        w = min(max(w, self.min_w), self.max_w)
        h = w / self.aspect
        cx = min(max(cx, w / 2), self.tl.W - w / 2)
        cy = min(max(cy, h / 2), self.tl.H - h / 2)
        return cx, cy, w

    def target(self, t):
        times = self.tl.activity_t
        acts = self.tl.activity[bisect.bisect_left(times, t - self.hold):bisect.bisect_right(times, t + self.lead)]
        if acts:
            xs = [a[1] for a in acts]
            ys = [a[2] for a in acts]
            # Fit all recent activity with a margin, but at least the zoom level.
            w = max(self.min_w, (max(xs) - min(xs)) * 1.4, (max(ys) - min(ys)) * 1.4 * self.aspect)
            return self.clamp((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2, w)
        rect = self.tl.focus_at(t)
        if rect:
            x, y, w, h = rect
            return self.clamp(x + w / 2, y + h / 2, max(w * 1.06, h * 1.06 * self.aspect))
        cx, cy = self.tl.cursor_at(t)
        return self.clamp(cx, cy, self.max_w)

    def path(self, fps, start=0.0, end=None):
        """Smoothed views, one per output frame. Spring on cx, cy and log(width)
        (zoom feels even in log space), critically damped."""
        end = self.tl.duration if end is None else end
        n = max(1, int(round((end - start) * fps)))
        dt = 1 / fps
        cx, cy, w = self.target(start)
        pos = [cx, cy, math.log(w)]
        vel = [0.0, 0.0, 0.0]
        k = self.k
        out = []
        for i in range(n):
            t = start + i * dt
            tx, ty, tw = self.target(t)
            goal = [tx, ty, math.log(tw)]
            for j in range(3):
                acc = k * k * (goal[j] - pos[j]) - 2 * k * vel[j]
                vel[j] += acc * dt
                pos[j] += vel[j] * dt
            out.append(self.clamp(pos[0], pos[1], math.exp(pos[2])))
        return out


# ------------------------------------------------------------------ render --
def _read_exact(f, n):
    buf = bytearray(n)
    view = memoryview(buf)
    got = 0
    while got < n:
        k = f.readinto(view[got:])
        if not k:
            return None
        got += k
    return buf


def face_crop_path(track, fps, start, n, aspect, stiffness=2.5, scale=1.7, min_side=0.55):
    """Square camera crops that keep the face centred, one per output frame.

    track: [(t_s, cx, cy, face_h)] in camera fractions (faces.load), or None/[].
    aspect: camera width / height. Returns [(cx, cy, side)] in fractions of the
    camera *height* (so a square is side x side). A slow critically damped spring
    (~1.5 s to settle) keeps small head movements from wobbling the crop.
    """
    if not track:
        return [(aspect / 2, 0.5, 1.0)] * n  # plain centre square
    times = [p[0] for p in track]

    def target(t):
        i = bisect.bisect_left(times, t)
        if i <= 0:
            p = track[0]
        elif i >= len(track):
            p = track[-1]
        else:  # linear between the two samples around t
            a, b = track[i - 1], track[i]
            f = (t - a[0]) / (b[0] - a[0]) if b[0] > a[0] else 0
            p = tuple(a[k] + (b[k] - a[k]) * f for k in range(4))
        _, cx, cy, h = p
        side = min(1.0, max(min_side, h * scale))
        return cx * aspect, cy, side  # x in units of camera height

    def clamp(cx, cy, side):
        cx = min(max(cx, side / 2), aspect - side / 2)
        cy = min(max(cy, side / 2), 1 - side / 2)
        return cx, cy, side

    pos = list(target(start))
    vel = [0.0, 0.0, 0.0]
    dt, k = 1 / fps, stiffness
    out = []
    for i in range(n):
        goal = target(start + i * dt)
        for j in range(3):
            vel[j] += (k * k * (goal[j] - pos[j]) - 2 * k * vel[j]) * dt
            pos[j] += vel[j] * dt
        out.append(clamp(*pos))
    return out


class Bubble:
    """The webcam as a fixed circle, bottom-left, following the face (faces.jsonl).
    Frames are decoded on the session clock: camera PTS 0 is shifted to its
    stream_start first_frame_t, then fps= with start_time gives exactly one frame
    per output frame (padding before the camera started with its first frame)."""

    CREAM_BGR = (217, 228, 241)  # Jesse's cream, #f1e4d9
    DECODE_H = 540  # enough for a ~280 px bubble even from a tight crop

    def __init__(self, session_dir, rec, fps, start, end, out_h, track=None, frac=0.26, margin_frac=0.04):
        import cv2
        import numpy as np
        self.np, self.cv2 = np, cv2
        self.d = d = round(out_h * frac) // 2 * 2
        self.margin = round(out_h * margin_frac)
        cw, ch = rec.get("pixels", [1920, 1080])
        self.H = self.DECODE_H
        self.W = round(self.H * cw / ch) // 2 * 2
        o = rec["first_frame_t"] / 1e9
        n = int(round((end - start) * fps))
        self.path = face_crop_path(track, fps, start, n, self.W / self.H)
        self.i = 0
        vf = f"setpts=PTS-STARTPTS+{o:.6f}/TB,fps={fps}:start_time={start:.6f},scale={self.W}:{self.H}:flags=area"
        self.proc = subprocess.Popen(
            ["ffmpeg", "-v", "error", "-i", os.path.join(session_dir, rec["file"]), "-vf", vf,
             "-frames:v", str(n), "-f", "rawvideo", "-pix_fmt", "bgr24", "-"],
            stdout=subprocess.PIPE, bufsize=0)
        self.last = None

        def aa_disc(r, size, ss=4):  # anti-aliased disc mask, 0..1, by supersampling
            big = np.zeros((size * ss, size * ss), np.uint8)
            cv2.circle(big, (size * ss // 2, size * ss // 2), int(r * ss), 255, -1, cv2.LINE_AA)
            return cv2.resize(big, (size, size), interpolation=cv2.INTER_AREA).astype(np.float32)[..., None] / 255

        outer = aa_disc(d / 2, d)
        self.alpha = outer
        self.ring = outer - aa_disc(d / 2 - max(2, d // 90), d)

    def draw(self, img):
        np, cv2 = self.np, self.cv2
        raw = _read_exact(self.proc.stdout, self.W * self.H * 3)
        if raw is not None:
            frame = np.frombuffer(raw, np.uint8).reshape(self.H, self.W, 3)
            cx, cy, side = self.path[min(self.i, len(self.path) - 1)]
            s = int(round(side * self.H))
            x0 = min(max(0, int(round(cx * self.H - s / 2))), self.W - s)
            y0 = min(max(0, int(round(cy * self.H - s / 2))), self.H - s)
            crop = frame[y0:y0 + s, x0:x0 + s]
            self.last = cv2.resize(crop, (self.d, self.d), interpolation=cv2.INTER_AREA).astype(np.float32)
        self.i += 1
        if self.last is None:
            return
        y0 = img.shape[0] - self.margin - self.d
        x0 = self.margin
        roi = img[y0:y0 + self.d, x0:x0 + self.d].astype(np.float32)
        roi = roi * (1 - self.alpha) + self.last * self.alpha
        roi = roi * (1 - self.ring) + np.float32(self.CREAM_BGR) * self.ring
        img[y0:y0 + self.d, x0:x0 + self.d] = roi.astype(np.uint8)

    def close(self):
        self.proc.stdout.close()
        self.proc.wait()


def render(session_dir, out, size=(1920, 1080), fps=None, zoom=2.0, start=0.0, end=None,
           rings=True, crf=18, camera=True):
    import cv2
    import numpy as np

    records = jsonl.read(os.path.join(session_dir, "session.jsonl"))
    tl = Timeline(records)
    screen = next(r for r in records if r["type"] == "stream_start" and r["stream"] == "screen")
    fps = fps or screen.get("fps", 60)
    ow, oh = size
    cam = Camera(tl, ow / oh, zoom=zoom)
    end = min(end or tl.duration, tl.duration)
    views = cam.path(fps, start, end)
    src = os.path.join(session_dir, screen["file"])
    W, H = tl.W, tl.H

    dec = subprocess.Popen(
        ["ffmpeg", "-v", "error", "-ss", f"{start:.3f}", "-i", src, "-t", f"{end - start:.3f}",
         "-vf", f"fps={fps}", "-f", "rawvideo", "-pix_fmt", "bgr24", "-"],
        stdout=subprocess.PIPE, bufsize=0)
    has_audio = bool(screen.get("audio"))
    clean_mic = os.path.join(session_dir, "mic-clean.flac")  # pugrec audio; same timeline as the track
    audio_src = clean_mic if os.path.exists(clean_mic) else src
    enc_cmd = ["ffmpeg", "-v", "error", "-y",
               "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{ow}x{oh}", "-r", str(fps), "-i", "-"]
    if has_audio:
        enc_cmd += ["-ss", f"{start:.3f}", "-t", f"{end - start:.3f}", "-i", audio_src, "-map", "0:v", "-map", "1:a?",
                    "-c:a", "aac", "-b:a", "192k"]
    enc_cmd += ["-c:v", "libx264", "-preset", "medium", "-crf", str(crf), "-pix_fmt", "yuv420p",
                "-movflags", "+faststart", out]
    enc = subprocess.Popen(enc_cmd, stdin=subprocess.PIPE)
    cam_rec = next((r for r in records if r["type"] == "stream_start" and r["stream"] == "camera"), None)
    bubble = None
    if camera and cam_rec and os.path.exists(os.path.join(session_dir, cam_rec["file"])):
        from . import faces
        track = faces.load(session_dir)
        if track is None:
            faces.detect(session_dir)
            track = faces.load(session_dir)
        bubble = Bubble(session_dir, cam_rec, fps, start, end, oh, track=track)

    frame_bytes = W * H * 3
    i = 0
    try:
        while True:
            raw = _read_exact(dec.stdout, frame_bytes)
            if raw is None:
                break
            frame = np.frombuffer(raw, np.uint8).reshape(H, W, 3)
            cx, cy, vw = views[min(i, len(views) - 1)]
            vh = vw / (ow / oh)
            x0f, y0f = cx - vw / 2, cy - vh / 2
            s = vw / ow  # source px per output px (>= 1: always downscaling)
            # 1) integer crop around the view + area resample (sharp text, no aliasing)
            x0, y0 = max(0, int(math.floor(x0f)) - 1), max(0, int(math.floor(y0f)) - 1)
            x1, y1 = min(W, int(math.ceil(x0f + vw)) + 2), min(H, int(math.ceil(y0f + vh)) + 2)
            crop = frame[y0:y1, x0:x1]
            small = cv2.resize(crop, (max(1, round((x1 - x0) / s)), max(1, round((y1 - y0) / s))),
                               interpolation=cv2.INTER_AREA)
            # 2) the sub-pixel remainder as a translation (scale ~1 now, so linear is fine)
            sx, sy = small.shape[1] / (x1 - x0), small.shape[0] / (y1 - y0)
            M = np.float32([[1, 0, -(x0f - x0) * sx], [0, 1, -(y0f - y0) * sy]])
            img = cv2.warpAffine(small, M, (ow, oh), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
            if rings:
                t = start + i / fps
                for ct, px, py in tl.clicks:
                    age = t - ct
                    if 0 <= age < 0.5:
                        ox, oy = (px - x0f) / s, (py - y0f) / s
                        r = int(10 + 40 * age / 0.5)
                        alpha = 1 - age / 0.5
                        over = img.copy()
                        cv2.circle(over, (int(ox), int(oy)), r, ORANGE_BGR, 4, cv2.LINE_AA)
                        cv2.addWeighted(over, alpha, img, 1 - alpha, 0, img)
            if bubble:
                bubble.draw(img)
            enc.stdin.write(img.tobytes())
            i += 1
            if i % fps == 0:
                print(f"\rrender: {i / fps:6.1f}/{end - start:.1f} s", end="", file=sys.stderr)
    finally:
        enc.stdin.close()
        if bubble:
            bubble.close()
        dec.stdout.close()
        dec.wait()
        rc = enc.wait()
    print(f"\rrender: {i} frames -> {out}" + ("" if rc == 0 else f" (encoder exit {rc})"), file=sys.stderr)
    return rc
