"""Disposible OCR worker process (spawned by landrec/ocrpool.py).

Everything native — OpenCV, Tesseract, PyMuPDF, PIL — runs HERE, in a
separate process. A crash in this process (segfault, out-of-memory on a
pathological file, anything) can NEVER take down the website: the server
just notices the worker died, quarantines it, and starts a fresh one.
"""
import base64
import json
import os
import queue
import struct
import subprocess
import sys
import threading
import time


def _parent_alive() -> bool:
    try:
        ppid = int(os.environ.get("LR_PARENT_PID", "0") or "0")
    except ValueError:
        return True
    if not ppid:
        return True
    try:
        os.kill(ppid, 0)
        return True
    except OSError:
        return False


def _start_stdin_pump():
    """Start the ONE-and-ONLY stdin reader thread and return its queue.

    Protocol: 8-byte little-endian length + JSON body. The pump reads whole
    (length, body) pairs and puts them on a queue. The main loop consumes
    with queue.get(timeout=...) — a 1 s timeout doubles as the idle tick
    (parent-liveness check) without ever touching the fd.

    WHY this design (two real bugs fixed):
      * select.select() on a pipe raises [WinError 10038] on WINDOWS —
        the original code killed the worker on every Windows machine.
      * A per-call reader thread with a join(timeout) ABANDONS the thread,
        which stays blocked on os.read(); the next call starts a second
        thread on the same fd — two readers race and TEAR the byte stream
        apart (the worker read a JSON fragment as a length header and died).
        One persistent reader per pipe is the only safe shape.
    """
    inbox = queue.Queue()

    def _pump():
        try:
            while True:
                hdr = b""
                while len(hdr) < 8:
                    c = os.read(0, 8 - len(hdr))
                    if not c:
                        return  # EOF: server closed the pipe (it is gone)
                    hdr += c
                (ln,) = struct.unpack("<Q", hdr)
                body = b""
                while len(body) < ln:
                    c = os.read(0, ln - len(body))
                    if not c:
                        return
                    body += c
                inbox.put(body)
        except Exception:  # noqa: BLE001 — pump dies, main loop sees nothing more
            return

    threading.Thread(target=_pump, daemon=True, name="ocr-stdin-pump").start()
    return inbox


def worker_main():
    global sys
    # stdout (fd 1) is the protocol channel — save it, then point future
    # prints/warnings at stderr so they can never corrupt the protocol.
    out_fd = os.dup(1)
    os.dup2(2, 1)
    sys.stderr.write("[ocr-worker] starting (pid %d)\n" % os.getpid())
    sys.stderr.flush()

    # Windows: make every child process (Tesseract.exe is a console app)
    # start WITHOUT a console window, so OCR never flashes a black terminal.
    if os.name == "nt":
        try:
            _orig_popen = subprocess.Popen
            class _NoWindowPopen(_orig_popen):
                def __init__(self, args, **kw):
                    try:
                        kw["creationflags"] = (kw.get("creationflags") or 0) \
                            | getattr(subprocess, "CREATE_NO_WINDOW", 0)
                    except TypeError:
                        pass
                    super().__init__(args, **kw)
            subprocess.Popen = _NoWindowPopen
        except Exception:
            pass

    # Import the heavy native stack only after the fd swap.
    from landrec import ocr

    def _warm_tesseract_models():
        # Force Tesseract to load the language models NOW (in the background,
        # while the website is already up) instead of during the user's
        # first upload. On a slow laptop loading 5 LSTM models from disk
        # can take 20-60 s - that cost used to land in the upload path.
        try:
            from PIL import Image as _PILImage
            import pytesseract as _pt
            pool = [l for l in ocr.DETECT_LANGS if l in ocr.available_langs()]
            _pt.image_to_string(_PILImage.new("L", (16, 16), 255),
                                lang="+".join(["eng"] + pool),
                                config="--psm 6", timeout=600)
            sys.stderr.write("[ocr-worker] tesseract language models warmed\n")
        except Exception as e:
            sys.stderr.write("[ocr-worker] model warmup failed (harmless): %s\n" % e)
        sys.stderr.flush()
    threading.Thread(target=_warm_tesseract_models, daemon=True).start()

    hello = json.dumps({"hello": True}).encode()
    try:
        os.write(out_fd, struct.pack("<Q", len(hello)) + hello)
    except OSError:
        # Server went away during our startup (e.g. it was stopped) — nothing
        # to serve; exit quietly.
        return
    sys.stderr.write("[ocr-worker] ready\n")
    sys.stderr.flush()

    inbox = _start_stdin_pump()
    while True:
        try:
            body = inbox.get(timeout=1.0)
        except queue.Empty:
            # Idle 1-s tick: exit promptly if the server (parent) is gone
            # so a crashed/stopped server can never leave an orphaned
            # worker behind — orphans are what break one-file cleanup.
            if not _parent_alive():
                sys.stderr.write("[ocr-worker] parent gone - exiting\n")
                break
            continue
        try:
            req = json.loads(body)
            data = base64.b64decode(req["data_b64"])
            langs = req.get("langs") or None   # optional language hint
            def _progress_cb(stage):
                try:
                    m = json.dumps({"progress": stage}).encode()
                    os.write(out_fd, struct.pack("<Q", len(m)) + m)
                except OSError:
                    pass
            result = ocr.process_file(data, req.get("filename") or "upload",
                                      langs=langs, progress_cb=_progress_cb)
            resp = {"ok": True, "result": result}
        except Exception as e:  # noqa: BLE001  (quarantine: any failure is a clean error)
            sys.stderr.write("[ocr-worker] processing failed: %s: %s\n"
                             % (type(e).__name__, e))
            sys.stderr.flush()
            resp = {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
        payload = json.dumps(resp).encode()
        try:
            os.write(out_fd, struct.pack("<Q", len(payload)) + payload)
        except OSError:
            break
    sys.stderr.write("[ocr-worker] exiting\n")
    sys.stderr.flush()


if __name__ == "__main__":
    worker_main()
