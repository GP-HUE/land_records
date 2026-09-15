"""OCR worker pool — crash isolation for the web server.

The web server NEVER runs OCR in its own process. All native image work
(OpenCV / Tesseract / PyMuPDF / PIL) happens in a separate, DISPOSABLE
worker process. If a worker dies on a bad file (segfault, out-of-memory,
whatever), only the worker dies:

  * the website keeps running (it contains no native OCR code at all)
  * the one bad file gets a clean error
  * a fresh worker is started automatically for the next file

Protocol (stdin/stdout, 8-byte little-endian length + JSON body):
  worker -> server : {"hello": true}                       (on start)
  server -> worker : {"filename": str, "data_b64": str,
                      "langs": [str] (optional hint)}
  worker -> server : {"progress": "stage"}                  (live notes)
  worker -> server : {"ok": true,  "result": {...}}
                    | {"ok": false, "error": "msg"}

PIPE READING RULE (learned the hard way):
  * NO select.select() — on Windows it raises [WinError 10038] on pipe
    fds (sockets only). That one line killed every worker on Windows.
  * ONE reader thread per pipe, for the pipe's whole life (a _Worker pump).
    Per-call reader threads with join(timeout) ABANDON threads that stay
    blocked on os.read(); the next call starts a second reader on the same
    fd — two readers race and tear the byte stream apart (the worker read
    a JSON fragment as a length header and died). Never do that.
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


class OcrWorkerError(Exception):
    """Raised when OCR cannot be completed (friendly message for the UI)."""


class _WorkerDead(OcrWorkerError):
    pass


_worker = None
_state = "none"                 # none | starting | ready
_ready = threading.Event()
_failed = threading.Event()     # spawn failed -> surface the error IMMEDIATELY
_spawn_error = None
_lock = threading.Lock()        # guards spawn / state
_req_lock = threading.Lock()    # serializes requests (one file at a time)

_START_TIMEOUT = 300            # generous: frozen exe re-extracts on start
# Per-file OCR limit. A 5-page scanned PDF on a slow laptop CPU can take
# 10+ minutes; the old 300 s limit killed the worker mid-file, retried,
# killed it again, and only then errored out — the user just saw
# "processing..." forever. 25 minutes gives even very slow PCs a chance
# to finish, and the UI now shows live progress while it works.
_REQ_TIMEOUT = 1500

# Live OCR progress (worker -> server), shown in the web UI.
_progress = {"stage": "", "ts": 0.0}


def progress() -> dict:
    """Current OCR stage for the web UI (empty string = idle)."""
    d = dict(_progress)
    d["stale"] = (time.time() - d["ts"]) > 900 if d["ts"] else True
    return d


def _worker_command():
    if getattr(sys, "frozen", False):
        # Frozen: re-launch the exe itself in worker mode. The mode is
        # passed BOTH as a command-line arg (100% reliable) and as the
        # LR_OCR_WORKER env var (belt + suspenders).
        return [sys.executable, "--lr-ocr-worker"]
    # Dev mode: run the worker module with the project's interpreter.
    return [sys.executable, "-m", "landrec.ocr_worker"]


def _dev_cwd():
    if getattr(sys, "frozen", False):
        return None
    # project root = parent of the landrec package directory
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class _Worker:
    """A live worker process + its single stdout pump thread.

    The pump reads whole (8-byte length, JSON body) pairs off the worker's
    stdout into a queue. Consumers use get(timeout) — the ONLY safe way to
    wait on pipe data (no select, no abandoned readers, no torn streams).
    """

    def __init__(self, proc):
        self.proc = proc
        self.inbox = queue.Queue()
        self._pump = threading.Thread(target=self._pump_loop, daemon=True,
                                      name="ocr-outbox-pump")
        self._pump.start()

    def _pump_loop(self):
        fd = self.proc.stdout.fileno()
        try:
            while True:
                hdr = b""
                while len(hdr) < 8:
                    c = os.read(fd, 8 - len(hdr))
                    if not c:
                        return  # EOF: worker exited
                    hdr += c
                (ln,) = struct.unpack("<Q", hdr)
                body = b""
                while len(body) < ln:
                    c = os.read(fd, ln - len(body))
                    if not c:
                        return
                    body += c
                self.inbox.put(body)
        except Exception:  # noqa: BLE001 — pump dies quietly; get() times out
            return

    def get(self, timeout):
        """Next JSON message, or _WorkerDead (death/timeout)."""
        deadline = time.time() + timeout
        while True:
            remain = deadline - time.time()
            if remain <= 0:
                if self.proc.poll() is not None:
                    raise _WorkerDead("OCR worker process died")
                raise _WorkerDead("timeout waiting for the OCR worker")
            try:
                body = self.inbox.get(timeout=min(2.0, remain))
                return json.loads(body)
            except queue.Empty:
                if self.proc.poll() is not None:
                    raise _WorkerDead("OCR worker process died")

    def kill(self):
        try:
            self.proc.kill()
        except Exception:
            pass
        # IMPORTANT: wait for the worker to be fully dead before returning.
        # The server process must not exit while the worker still lives —
        # a live child holding the one-file bundle's files is exactly what
        # makes PyInstaller's temp-dir cleanup fail on Windows with the
        # "Failed to remove temporary directory" dialog.
        try:
            self.proc.wait(timeout=10)
        except Exception:
            pass


def _write_all(fd, data):
    """Write ALL bytes to a pipe.

    A single os.write() may return after writing only part of a large
    payload (the pipe buffer is 64 KB; base64 file payloads can be MBs).
    Dropping the rest silently corrupts the protocol and the worker hangs
    forever - which is exactly the "processing... forever" symptom.
    """
    view = memoryview(data)
    while len(view):
        n = os.write(fd, view)
        if n <= 0:
            raise OSError("worker pipe write failed")
        view = view[n:]


def _spawn():
    """Start a worker and wait for its hello handshake."""
    global _worker, _state, _spawn_error
    env = dict(os.environ)
    env["LR_OCR_WORKER"] = "1"
    env["LR_PARENT_PID"] = str(os.getpid())
    kwargs = {}
    if os.name == "nt":
        # No console window for the worker (a noconsole exe's console
        # children would otherwise flash a window while OCR runs).
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    try:
        # The worker's stderr goes to its OWN log file (data/worker.log) —
        # not mixed into server.log — so when the worker fails to start on
        # a user's PC, the real reason (import error, Tesseract error,
        # whatever) is in ONE place to read. The file object is stored on
        # the _Worker so it is never garbage-collected (a GC'd file object
        # would close the fd mid-run and corrupt the log).
        wlog = None
        try:
            from landrec import paths as _paths
            _lp = os.path.join(_paths.data_dir(), "data", "worker.log")
            os.makedirs(os.path.dirname(_lp), exist_ok=True)
            wlog = open(_lp, "a", encoding="utf-8")
        except Exception:  # noqa: BLE001
            wlog = None
        p = subprocess.Popen(_worker_command(), stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=wlog,
                             cwd=_dev_cwd(), **kwargs)
    except Exception as e:  # noqa: BLE001
        _spawn_error = str(e)
        _state = "none"
        _failed.set()
        return False
    try:
        w = _Worker(p)
        msg = w.get(timeout=_START_TIMEOUT)
        if not msg.get("hello"):
            raise RuntimeError("worker handshake failed")
    except Exception as e:  # noqa: BLE001
        try:
            p.kill()
        except Exception:
            pass
        _spawn_error = str(e)
        _state = "none"
        _failed.set()
        return False
    if wlog is not None:
        w.stderr_file = wlog
    _worker = w
    _state = "ready"
    _spawn_error = None
    _ready.set()
    return True


def ensure_worker(timeout=_START_TIMEOUT):
    """Make sure a live, ready worker exists (spawns one if needed).

    FAILS FAST: if the worker cannot start (missing Tesseract, killed by
    antivirus, whatever), the error is raised immediately with the reason
    - the old code waited out the full timeout first, so a dead worker
    looked like "processing... forever" in the browser.
    """
    global _state, _spawn_error
    with _lock:
        if _state == "ready":
            return
        if _state != "starting":
            _state = "starting"
            _ready.clear()
            _failed.clear()
            _spawn_error = None
            threading.Thread(target=_spawn, daemon=True).start()
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _ready.wait(0.5):
            break
        if _failed.is_set():
            break
    if _state != "ready":
        with _lock:
            err = _spawn_error
        hint = ""
        if err:
            hint = " (%s)" % err
            if "tesseract" in err.lower():
                hint += (" - Tesseract OCR is missing or broken. Install it from "
                         "https://github.com/UB-Mannheim/tesseract/wiki, then "
                         "restart the app.")
        raise OcrWorkerError(
            "The OCR engine could not be started%s. See the server log." % hint)


def _kill_worker():
    global _worker, _state
    with _lock:
        w, _worker = _worker, None
        _state = "none"
    _ready.clear()
    if w is not None:
        w.kill()
        try:
            if getattr(w, "stderr_file", None) is not None:
                w.stderr_file.close()
        except Exception:  # noqa: BLE001
            pass


def run_ocr(data: bytes, filename: str, langs: list = None) -> dict:
    """Run the OCR pipeline in the worker process.

    `langs` is an optional language hint (e.g. ["hin"]); when given the
    worker skips the script-detection pass (faster + more accurate).

    If the worker dies (crash on a bad file, OOM, whatever), a fresh
    worker is started and the same file is retried once — transparently,
    without any user action. Only if the file kills TWO workers in a row
    does the upload get a clean error (the website itself is never
    affected by worker failures).
    """
    for _attempt in (1, 2):
        ensure_worker()
        with _lock:
            w = _worker
        if w is None or w.proc.poll() is not None:
            _kill_worker()
            continue  # worker died between requests: respawn and retry
        req = {"filename": filename,
               "data_b64": base64.b64encode(data).decode("ascii")}
        if langs:
            req["langs"] = langs
        payload = json.dumps(req).encode("ascii")
        with _req_lock:
            try:
                _write_all(w.proc.stdin.fileno(),
                           struct.pack("<Q", len(payload)) + payload)
                msg = None
                # The worker may send live-progress notes before the final
                # result; relay them to the UI and keep reading until done.
                while msg is None:
                    candidate = w.get(timeout=_REQ_TIMEOUT)
                    if "progress" in candidate:
                        _progress["stage"] = candidate["progress"]
                        _progress["ts"] = time.time()
                        continue
                    msg = candidate
            except _WorkerDead:
                _kill_worker()
                continue  # worker died on this file: fresh worker, retry once
            except BrokenPipeError:
                # EPIPE on the write: the worker died in the tiny window
                # between the poll() liveness check and the write (kill -9,
                # OOM kill, native crash). The read side of the pipe is gone
                # because the process is gone — treat it exactly like
                # _WorkerDead: respawn and retry. (Before this fix it fell
                # into the generic handler and the user got a 500 even
                # though a fresh worker would have handled the file fine.)
                _kill_worker()
                continue
            except Exception as e:  # noqa: BLE001
                _kill_worker()
                raise OcrWorkerError("OCR processing failed: %s" % e)
        if not msg.get("ok"):
            # Deterministic error reported by the worker (not a crash) —
            # retrying the same file would give the same error.
            raise OcrWorkerError(msg.get("error") or "OCR worker error")
        return msg["result"]
    raise OcrWorkerError(
        "The OCR worker stopped on this file (it was quarantined). "
        "The website is unaffected — please retry or try another file.")


def worker_pid():
    with _lock:
        return _worker.proc.pid if _worker else None


def status() -> dict:
    """Diagnostics snapshot for the System Status panel."""
    with _lock:
        w, st, err = _worker, _state, _spawn_error
    alive = w is not None and w.proc.poll() is None
    from landrec import paths as _paths
    try:
        wlog_path = os.path.join(_paths.data_dir(), "data", "worker.log")
    except Exception:  # noqa: BLE001
        wlog_path = ""
    return {
        "state": st,                      # none | starting | ready
        "worker_pid": w.proc.pid if w else None,
        "worker_alive": alive,
        "last_spawn_error": err,
        "worker_log": wlog_path,
    }


def shutdown_worker():
    _kill_worker()
