"""`pugrec setup`: make a checkout ready to use. Idempotent, stdlib only (runs on the
system Python, before any venv exists) - the PugPal dotfiles' install.sh calls it.

  1. .venv from requirements.txt - built or updated only when requirements.txt or the
     Python version changed (hash marker inside .venv)
  2. the whisper model in ~/.cache/pugrec, pinned SHA-256 (skip with --no-model):
     downloaded to .part, verified, renamed; skipped if present and valid
Exit 0 when done; non-zero with one line "setup: <reason>" otherwise.
"""

import hashlib
import os
import subprocess
import sys
import urllib.request

from . import transcript

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENV = os.path.join(REPO, ".venv")
REQS = os.path.join(REPO, "requirements.txt")
MARKER = os.path.join(VENV, ".pugrec-requirements.sha256")


class SetupError(Exception):
    pass


def _say(msg):
    print(f"setup: {msg}", file=sys.stderr)


def wanted_marker():
    with open(REQS, "rb") as f:
        reqs = hashlib.sha256(f.read()).hexdigest()
    return f"{reqs} python{sys.version_info.major}.{sys.version_info.minor}"


def venv():
    want = wanted_marker()
    py = os.path.join(VENV, "bin", "python")
    try:
        have = open(MARKER).read().strip()
    except OSError:
        have = None
    if have == want and os.access(py, os.X_OK):
        _say(".venv up to date")
        return
    rebuild = not os.access(py, os.X_OK) or (have and have.split()[-1] != want.split()[-1])
    if rebuild:  # missing, or made by another Python version: start clean
        _say("creating .venv")
        r = subprocess.run([sys.executable, "-m", "venv", "--clear", VENV], capture_output=True, text=True)
        if r.returncode:
            raise SetupError(f"python -m venv failed: {(r.stderr or r.stdout).strip().splitlines()[-1:]}")
    _say("installing requirements.txt into .venv")
    r = subprocess.run([py, "-m", "pip", "install", "--quiet", "--disable-pip-version-check", "-r", REQS],
                       capture_output=True, text=True)
    if r.returncode:
        last = (r.stderr or r.stdout).strip().splitlines()
        raise SetupError(f"pip install failed: {last[-1] if last else 'exit ' + str(r.returncode)}")
    with open(MARKER, "w") as f:
        f.write(want + "\n")


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 22), b""):
            h.update(chunk)
    return h.hexdigest()


def model():
    d = transcript._cache()
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, transcript.MODEL)
    if os.path.exists(path):
        if _sha256(path) == transcript.MODEL_SHA256:
            _say(f"whisper model present ({transcript.MODEL})")
            return
        _say(f"{path} doesn't match the pinned SHA-256: downloading again")
    part = path + ".part"
    _say(f"downloading {transcript.MODEL_URL}")
    h = hashlib.sha256()
    try:
        with urllib.request.urlopen(transcript.MODEL_URL, timeout=60) as r, open(part, "wb") as f:
            for chunk in iter(lambda: r.read(1 << 20), b""):
                f.write(chunk)
                h.update(chunk)
    except OSError as e:
        raise SetupError(f"model download failed: {e}") from None
    if h.hexdigest() != transcript.MODEL_SHA256:
        os.unlink(part)
        raise SetupError("downloaded model doesn't match the pinned SHA-256 (deleted)")
    os.replace(part, path)
    _say(f"whisper model ready ({path})")


def run(no_model=False):
    try:
        venv()
        if not no_model:
            model()
    except SetupError as e:
        print(f"setup: {e}", file=sys.stderr)
        return 1
    except OSError as e:
        print(f"setup: {e}", file=sys.stderr)
        return 1
    _say("done")
    return 0
