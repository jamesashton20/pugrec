"""WhatsApp-ready copies: `pugrec whatsapp FILE.mp4` (and `pugrec build ... --whatsapp`).

James's videos were sent but not shown inline. Meta's own media docs (WhatsApp
Business Platform, "Supported media types", checked 2026-10-13):
  - MP4 (or 3GPP), H.264 video + AAC audio, a single audio stream or none
  - "videos encoded with the H.264 'High' profile and B-frames are not supported
    by Android WhatsApp clients" -> Main profile without B-frames (or Baseline)
  - moov before mdat (-movflags +faststart)
  - 16 MB per video; bigger goes as a document, without the inline player
Our normal builds were H.264 High with B-frames and ~20+ MB: both rules broken.

So: H.264 Main, no B-frames, 30 fps, yuv420p, limited range tagged BT.709, AAC LC
stereo 96 kb/s, faststart, and a two-pass bitrate aimed under 16 MB (stepping down
to 720p / 540p when the bitrate would be too low for the size). Stdlib + ffmpeg only.
"""

import json
import os
import subprocess
import sys
import tempfile

LIMIT_BYTES = 16 * 1000 * 1000      # WhatsApp's media limit; aim a little under it
TARGET_BYTES = 15.2 * 1000 * 1000
AUDIO_BPS = 96_000
MAX_VIDEO_BPS = 5_000_000           # no point going higher for phones
# Lowest video bitrate (b/s) that still looks fine per output height; below it, step down.
MIN_BPS_FOR = {1080: 1_400_000, 720: 700_000, 540: 380_000}
FLOOR_BPS = 200_000                 # below this even 540p falls apart


class ShareError(Exception):
    pass


def probe(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                        "stream=index,codec_type,width,height,color_range,color_space:format=duration", "-of", "json", path],
                       capture_output=True, text=True)
    if r.returncode:
        raise ShareError(f"can't read {path}: {r.stderr.strip()[-200:]}")
    d = json.loads(r.stdout)
    v = next((s for s in d["streams"] if s["codec_type"] == "video"), None)
    if not v:
        raise ShareError(f"{path} has no video")
    a = any(s["codec_type"] == "audio" for s in d["streams"])
    return float(d["format"]["duration"]), v["width"], v["height"], a, \
        v.get("color_range", "unknown"), v.get("color_space", "unknown")


def colour_in(rng, space):
    """swscale input options: trust the file's tags; untagged = our builds (BT.601, limited)."""
    rng = {"pc": "pc", "tv": "tv"}.get(rng, "tv")
    space = {"bt709": "bt709", "bt470bg": "bt601", "smpte170m": "bt601", "bt2020nc": "bt2020"}.get(space, "bt601")
    return f"in_color_matrix={space}:in_range={rng}"


def plan(duration, w, h, has_audio):
    """(short-side height to scale to, or None to keep it; video b/s) for this length and size."""
    budget = TARGET_BYTES * 8 / duration - (AUDIO_BPS if has_audio else 0)
    v = int(min(MAX_VIDEO_BPS, budget * 0.97))  # mp4 overhead
    short = min(w, h)
    for target in (1080, 720, 540):
        if target <= short and v >= MIN_BPS_FOR[target]:
            return (None if target == short else target), v
    if v >= FLOOR_BPS:  # long: 540p at whatever fits
        return (540 if short > 540 else None), v
    longest = TARGET_BYTES * 8 / (FLOOR_BPS / 0.97 + (AUDIO_BPS if has_audio else 0)) / 60
    raise ShareError(f"{duration / 60:.1f} min is too long for a WhatsApp inline video (16 MB) - "
                     f"about {longest:.0f} min at most; cut it shorter or send it as a document")


def whatsapp(src, dst=None, quiet=False):
    duration, w, h, has_audio, rng, space = probe(src)
    dst = dst or os.path.splitext(src)[0] + "-whatsapp.mp4"
    short_target, vbps = plan(duration, w, h, has_audio)
    # Fit inside 1920x1080 (or 1080x1920) at most, smaller when the bitrate needs it: level 4.0
    # allows 8192 macroblocks a frame, so a 5K ultrawide can't just keep 1080 lines (2560x1080).
    short = short_target or min(w, h, 1080)
    bw, bh = (short * 16 // 9, short) if w >= h else (short, short * 16 // 9)
    scale = (f"scale='min(iw,{bw})':'min(ih,{bh})':force_original_aspect_ratio=decrease:"
             f"force_divisible_by=2:flags=lanczos,")
    # Convert to limited-range BT.709 and say so, so phones don't guess (full range = "yuvj",
    # which phones mangle). Our builds are untagged (swscale: BT.601, limited); raw
    # recordings are tagged full range.
    vf = (f"{scale}scale={colour_in(rng, space)}:out_color_matrix=bt709:out_range=tv,"
          f"format=yuv420p,fps=30")
    for attempt in range(3):
        with tempfile.TemporaryDirectory(prefix="pugrec-wa-") as tmp:
            log = os.path.join(tmp, "pass")
            common = ["-i", src, "-map", "0:v:0", "-vf", vf, "-c:v", "libx264", "-profile:v", "main",
                      "-level", "4.0", "-bf", "0", "-preset", "slow", "-b:v", str(vbps),
                      "-maxrate", str(int(vbps * 1.5)), "-bufsize", str(int(vbps * 2)),
                      "-color_range", "tv", "-colorspace", "bt709", "-color_primaries", "bt709",
                      "-color_trc", "bt709", "-passlogfile", log]
            r1 = subprocess.run(["ffmpeg", "-v", "error", "-y", *common, "-pass", "1", "-an", "-f", "mp4", os.devnull],
                                capture_output=True, text=True)
            if r1.returncode:
                raise ShareError(f"ffmpeg pass 1 failed: {r1.stderr.strip()[-300:]}")
            audio = ["-map", "0:a:0", "-c:a", "aac", "-profile:a", "aac_low", "-b:a", str(AUDIO_BPS), "-ac", "2"] \
                if has_audio else ["-an"]
            part = dst + ".part.mp4"
            r2 = subprocess.run(["ffmpeg", "-v", "error", "-y", *common, "-pass", "2", *audio,
                                 "-movflags", "+faststart", part], capture_output=True, text=True)
            if r2.returncode:
                raise ShareError(f"ffmpeg pass 2 failed: {r2.stderr.strip()[-300:]}")
        size = os.path.getsize(part)
        if size <= LIMIT_BYTES:
            os.replace(part, dst)
            if not quiet:
                print(f"whatsapp: {dst} ({size / 1e6:.1f} MB, fits {bw}x{bh}, {vbps / 1000:.0f} kb/s, H.264 Main, no B-frames)",
                      file=sys.stderr)
            return dst
        os.unlink(part)
        vbps = int(vbps * LIMIT_BYTES / size * 0.92)  # overshot: aim lower and go again
    raise ShareError("couldn't get it under 16 MB")
