"""Executable entry point (used when building the single-file .exe).

Architecture (crash isolation + self-healing):

  LandRecordSystem.exe
  ├── MONITOR   (this file, default mode)
  │     - checks the port, opens the browser once
  │     - starts the server as a child process and watches it; if the
  │       server ever dies, the monitor restarts it after ~2 seconds and
  │       the browser tab reconnects automatically
  │     - exits when the server exits cleanly (all tabs closed)
  ├── SERVER    (this file with LR_MODE=server)
  │     - the uvicorn web server + idle auto-stop watchdog
  │     - contains NO native OCR code: it only does HTTP/SQLite/JSON
  │     - spawns and manages the OCR worker
  └── OCR WORKER (this file with LR_OCR_WORKER=1)
        - all native image work (OpenCV / Tesseract / PyMuPDF / PIL)
        - DISPOSABLE: a bad file can at worst kill the worker, never the
          website. A fresh worker starts automatically for the next file.
        - also stops itself if the server dies (no orphan processes)

No console window: built with PyInstaller --noconsole. All output goes to
<exe folder>\\data\\server.log
"""
import os
import socket
import subprocess
import sys
import threading
import time
import webbrowser

from landrec import paths

# Grace period (seconds) of "zero open tabs" before the server stops.
# Short on purpose: the website says "bye" the moment a tab closes, so the
# server stops almost immediately. The grace only protects page refreshes.
IDLE_SHUTDOWN_SECONDS = int(os.environ.get("LR_IDLE_TIMEOUT", "10"))

# Crash self-healing: the server is restarted on ANY unexpected exit.
# (Guarded against crash-loops: >3 restarts within 2 minutes => stop and
# show a message with the log location.)
RESTART_DELAY_SECONDS = 2


def _server_mode() -> bool:
    return os.environ.get("LR_MODE") == "server"


def _worker_mode() -> bool:
    return os.environ.get("LR_OCR_WORKER") == "1"


def _child_command():
    if getattr(sys, "frozen", False):
        # Frozen: re-launch the exe itself in server mode (arg + env var).
        return [sys.executable, "--lr-server"]
    return [sys.executable, os.path.abspath(__file__)]


def _data_log_path() -> str:
    return os.path.join(paths.data_dir(), "data", "server.log")


def _line_buffer_stdio():
    """Line-buffer stdout/stderr so server.log is always up to date
    (block buffering would lose the last lines on a hard crash, which
    is exactly when you need the log most)."""
    for s in (sys.stdout, sys.stderr):
        try:
            if s is not None:
                s.reconfigure(line_buffering=True)
        except Exception:
            pass


def _fix_std_streams():
    """Make stdout/stderr usable in ALL process modes.

    In a windowed (noconsole) frozen exe, sys.stdout / sys.stderr can be:
      * None                       (classic case)  -> redirect to the log
      * a file object wrapping an INVALID Windows handle — writes then
        raise OSError [Errno 22] Invalid argument and the FIRST print()
        kills the process (this is exactly the crash seen on the user's
        PC: run.py _run_server line "print(Server process starting...)").
    SAFETY RULE: a stream that is a PIPE is NEVER test-written — in worker
    mode, stdout IS the binary protocol pipe to the server, and even one
    stray byte corrupts the length-prefixed protocol (the "OCR worker
    process died" failure). We inspect the fd type and only test consoles.
    """
    import stat as _stat

    def _redirect(name):
        lp = _data_log_path()
        os.makedirs(os.path.dirname(lp), exist_ok=True)
        oldobj = getattr(sys, name)
        try:
            if oldobj is not None:
                oldobj.close()
        except Exception:  # noqa: BLE001
            pass
        setattr(sys, name, open(lp, "a", encoding="utf-8"))

    for name in ("stdout", "stderr"):
        obj = getattr(sys, name)
        if obj is None:
            _redirect(name)
            continue
        try:
            st = os.fstat(obj.fileno())
        except Exception:  # noqa: BLE001 - invalid handle of any kind
            _redirect(name)
            continue
        if _stat.S_ISFIFO(st.st_mode) or _stat.S_ISSOCK(st.st_mode):
            continue  # protocol pipe / socket: NEVER test-write (a stray
                      # byte corrupts the worker's length-prefixed protocol)
        if _stat.S_ISCHR(st.st_mode):
            # console device: a windowed build has no real console, so a
            # 1-byte test write reveals broken handles (EINVAL on Windows)
            try:
                obj.write(" ")
                obj.flush()
            except Exception:  # noqa: BLE001
                _redirect(name)
            continue
        # regular file (e.g. inherited log file): fine as-is


def _port_busy(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def _message_box(title: str, text: str):
    try:
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        messagebox.showwarning(title, text)
        root.destroy()
    except Exception:
        pass


def _port_busy_dialog(port: int):
    _message_box(
        "Land Record System already running",
        "A Land Record System server is already running on port %d.\n\n"
        "The existing website has been opened for you.\n\n"
        "If it did NOT open: press Ctrl+Shift+Esc, end every\n"
        "'LandRecordSystem.exe' process in Task Manager, then\n"
        "double-click the .exe again." % port,
    )


# --------------------------------------------------------------------------
# Clean-stop marker: the ONLY way the monitor stops for good.
# The server writes this file when the user genuinely closed all browser
# tabs. ANY other exit (crash, clean-but-unexpected, whatever) makes the
# monitor restart the server. This is what prevents the "site opens, dies
# a few seconds later, and never comes back" failure.
# --------------------------------------------------------------------------
def _clean_stop_marker() -> str:
    return os.path.join(paths.data_dir(), "data", "clean_stop_tabs")


def _write_clean_stop_marker():
    try:
        mp = _clean_stop_marker()
        os.makedirs(os.path.dirname(mp), exist_ok=True)
        with open(mp, "w") as f:
            f.write(str(time.time()))
    except Exception:
        pass


def _consume_clean_stop_marker() -> bool:
    """True (and removed) if the server deliberately stopped because all
    tabs were closed."""
    try:
        mp = _clean_stop_marker()
        if os.path.exists(mp):
            os.remove(mp)
            return True
    except Exception:
        pass
    return False


def _crash_dialog(port: int):
    _message_box(
        "Land Record System stopped",
        "The server process crashed repeatedly and could not be\n"
        "restarted automatically (port %d).\n\n"
        "Check the log file:\n%s\n\n"
        "Then double-click the .exe again to start fresh." % (port, _data_log_path()),
    )


# --------------------------------------------------------------------------
# MONITOR mode (default)
# --------------------------------------------------------------------------
class _Splash:
    """'Starting up' window shown while the server unpacks/boots.

    The windowed exe has no terminal, so without this the user would
    double-click and see nothing for a while (startup can still take a
    few seconds while the server and OCR engine initialise; a one-file exe
    would additionally unpack its bundle every start, which we avoid).

    Thread-safety: Tkinter widgets may only be touched from the main
    thread. Other threads (the monitor loop) just set a flag / stash text;
    the main thread applies them in a light `after` pump.
    """

    def __init__(self, port):
        import threading
        self.root = None
        self._close = threading.Event()
        self._pending_text = None
        self._text_lock = threading.Lock()
        try:
            import tkinter as tk
            r = self.root = tk.Tk()
            r.title("Land Record System")
            r.geometry("500x170")
            r.attributes("-topmost", True)
            r.resizable(False, False)
            tk.Label(r, text="भू-अभिलेख प्रणाली | Land Record System",
                     font=("Segoe UI", 13, "bold")).pack(pady=(18, 4))
            self.status = tk.Label(
                r, text="Server is starting...\nYour browser will open automatically.",
                font=("Segoe UI", 10))
            self.status.pack()
            tk.Label(r, text="If your browser does not open, visit:\nhttp://localhost:%d" % port,
                     font=("Consolas", 10), fg="#0f2b48").pack(pady=(10, 14))
            self._pump()
        except Exception:
            self.root = None

    def _pump(self):
        """Runs on the main thread only."""
        try:
            if self._close.is_set():
                self.root.destroy()
                return
            with self._text_lock:
                text, self._pending_text = self._pending_text, None
            if text is not None:
                self.status.config(text=text)
        except Exception:
            return
        try:
            self.root.after(150, self._pump)
        except Exception:
            pass

    def set_text(self, text):
        with self._text_lock:
            self._pending_text = text

    def close(self):
        self._close.set()


def _open_browser_when_ready(port, on_opened=None, timeout=240):
    """Open the browser only once the server actually accepts connections.

    (Opening it at a fixed 2 s — the old behaviour — showed the user a
    'server unreachable' page on slow machines, because a one-file exe
    takes a while to unpack, and antivirus slows it down further.)
    """
    def _loop():
        deadline = time.time() + timeout
        while time.time() < deadline:
            if _port_busy(port):
                break
            time.sleep(0.5)
        try:
            webbrowser.open("http://localhost:%d" % port)
        finally:
            if on_opened:
                on_opened()
    threading.Thread(target=_loop, daemon=True).start()


def _monitor_loop(port, splash):
    def _note(text, hold_ms=8000):
        """Show a message on the splash (frozen) or a message box (dev),
        then close the splash after hold_ms."""
        if splash:
            splash.set_text(text)
            threading.Timer(hold_ms / 1000.0, splash.close).start()
        else:
            _message_box("Land Record System", text)

    if _port_busy(port):
        print("Port %d already in use - opening the existing server." % port)
        webbrowser.open("http://localhost:%d" % port)
        _note("A Land Record System is already running.\n\nOpening your browser...", hold_ms=2500)
        return

    def _browser_opened():
        if splash:
            splash.close()

    _open_browser_when_ready(port, on_opened=_browser_opened)

    # Crash-loop guard: >3 restarts within 2 minutes => stop trying and tell
    # the user (with the log location) instead of spinning forever.
    crash_times = []
    while True:
        _consume_clean_stop_marker()  # never start with a stale marker
        env = dict(os.environ)
        env["LR_MODE"] = "server"
        env["PORT"] = str(port)
        env["LR_PARENT_PID"] = str(os.getpid())
        try:
            proc = subprocess.Popen(_child_command(), env=env)
        except Exception as e:  # noqa: BLE001
            print("Failed to start the server process: %s" % e)
            _note("The server could not be started:\n%s\n\nCheck the log:\n%s"
                  % (e, _data_log_path()), hold_ms=15000)
            return
        print("Server process started (pid %d)." % proc.pid)
        if splash:
            splash.set_text("Server starting...\nYour browser will open as soon as it is ready.")
        code = proc.wait()
        # The marker file is the SINGLE source of truth for "the user closed
        # the browser": the server writes it only on the deliberate idle-stop
        # path. (Exit codes can't be trusted — a signal gives -15 on Linux,
        # and a clean exit can happen for unexplained reasons.)
        if _consume_clean_stop_marker():
            print("All browser tabs closed (clean stop) - monitor exiting.")
            break
        # Anything else — a crash, or an exit without the marker — gets a
        # restart. Never a silent give-up.
        now = time.time()
        crash_times.append(now)
        crash_times = [t for t in crash_times if now - t <= 120]
        print("Server process exited (code %s) without a clean-stop marker - "
              "restarting..." % code)
        if len(crash_times) > 3:
            _note("The server keeps stopping unexpectedly.\n\n"
                  "Check the log for details:\n%s\n\n"
                  "Then double-click the .exe again to start fresh."
                  % _data_log_path(), hold_ms=20000)
            break
        time.sleep(RESTART_DELAY_SECONDS)
    if splash:
        splash.close()


def _run_as_monitor():
    port = int(os.environ.get("PORT", "8000"))

    print("=" * 60)
    print("  Intelligent Land Record Digitization & Validation System")
    print("  Running at: http://localhost:%d" % port)
    print("  Default admin: admin@landrec.gov.in / Admin@123")
    print("  Log file: %s" % _data_log_path())
    print("  Auto-stop: closing all browser tabs stops the server")
    print("  (~%d s). Self-healing: a crashed server restarts itself."
          % IDLE_SHUTDOWN_SECONDS)
    print("=" * 60)

    # Frozen windowed build: show a 'starting up' window until the browser
    # opens (the monitor loop runs in a thread; the main thread pumps the
    # splash UI so it stays responsive).
    splash = _Splash(port) if getattr(sys, "frozen", False) else None
    loop = threading.Thread(target=_monitor_loop, args=(port, splash), daemon=True)
    loop.start()
    if splash is not None and splash.root is not None:
        try:
            splash.root.mainloop()
        except Exception:
            pass
    else:
        loop.join()


# --------------------------------------------------------------------------
# SERVER mode (child process)
# --------------------------------------------------------------------------
def _idle_watchdog(server):
    """Stop the server when the last browser tab is closed.

    The web UI reports every open tab (ping every 5 s + a "bye" on close).
    We shut down once the tab count stays at zero for
    IDLE_SHUTDOWN_SECONDS straight — that short grace only exists so a
    page refresh (F5) can't kill the server mid-reload.

    IMPORTANT: this is the ONLY deliberate-stop path, and it writes the
    clean-stop marker so the monitor knows the exit was intentional
    (user closed the browser) and should NOT restart the server.
    """
    from landrec import main as landrec_main
    seen_any = False
    zero_since = None
    while not server.should_exit:
        time.sleep(2)
        try:
            tabs = landrec_main.active_tab_count()
        except Exception:
            continue
        if tabs > 0:
            seen_any = True
            zero_since = None
        elif seen_any:
            if zero_since is None:
                zero_since = time.time()
            elif time.time() - zero_since >= IDLE_SHUTDOWN_SECONDS:
                print("All browser tabs closed - stopping the server now "
                      "(like closing the old terminal window).")
                _write_clean_stop_marker()
                server.should_exit = True


def _prewarm_ocr_worker():
    """Start the OCR worker in the background so the first upload is fast.

    (A frozen worker re-extracts the bundle once at start — this happens
    while the website is already up and usable.)
    """
    try:
        from landrec import ocrpool
        ocrpool.ensure_worker()
        print("OCR worker ready (pid %s) - upload processing is live."
              % ocrpool.worker_pid())
    except Exception as e:  # noqa: BLE001
        print("OCR worker prewarm failed (will retry on first upload): %s" % e)


def _migrate_legacy_data_dir():
    """One-time migration for users upgrading from the old single-file build.

    Old layout:  dist\\LandRecordSystem.exe  ->  database in dist\\data\\
    New layout:  dist\\LandRecordSystem\\LandRecordSystem.exe
                ->  database in dist\\LandRecordSystem\\data\\

    If the new data dir is missing but the old one exists in the parent
    folder (and really is an old database), move it over automatically.
    """
    import shutil
    exe_dir = os.path.dirname(os.path.abspath(sys.executable))
    new_data = os.path.join(exe_dir, "data")
    if os.path.exists(new_data):
        return  # already migrated (or fresh install)
    old_data = os.path.join(os.path.dirname(exe_dir), "data")
    if os.path.isdir(old_data) and os.path.exists(os.path.join(old_data, "landrec.db")):
        try:
            shutil.move(old_data, new_data)
            print("Migrated existing database from the previous build location: %s" % old_data)
        except Exception as e:  # noqa: BLE001
            print("Could not auto-migrate the old data folder (%s). "
                  "Copy '%s' into '%s' manually." % (e, old_data, new_data))


def _run_server():
    port = int(os.environ.get("PORT", "8000"))
    print("Server process starting (port %d, pid %d)." % (port, os.getpid()))
    _migrate_legacy_data_dir()

    import uvicorn
    from landrec.main import app
    config = uvicorn.Config(app, host="0.0.0.0", port=port, log_level="info")
    server = uvicorn.Server(config)
    threading.Thread(target=_idle_watchdog, args=(server,), daemon=True).start()
    threading.Thread(target=_prewarm_ocr_worker, daemon=True).start()
    try:
        server.run()
    finally:
        # Clean shutdown: take the OCR worker down with us (no orphans).
        try:
            from landrec import ocrpool
            ocrpool.shutdown_worker()
        except Exception:
            pass


def _arg_mode() -> str:
    """Mode passed on the command line (100% reliable, unlike env vars
    which some Windows/antivirus layers have been known to mangle).
    ocrpool passes --lr-ocr-worker when spawning the worker."""
    for a in sys.argv[1:]:
        if a == "--lr-ocr-worker":
            return "worker"
        if a == "--lr-server":
            return "server"
    return ""


def main():
    _fix_std_streams()
    _line_buffer_stdio()
    amode = _arg_mode()
    try:
        if amode == "worker" or _worker_mode():
            from landrec.ocr_worker import worker_main
            worker_main()
        elif amode == "server" or _server_mode():
            _run_server()
        else:
            _run_as_monitor()
    except Exception:
        # Last resort: a startup crash must leave a trace in the log file
        # directly (the streams may themselves be the thing that's broken).
        import traceback as _tb
        try:
            lp = _data_log_path()
            os.makedirs(os.path.dirname(lp), exist_ok=True)
            with open(lp, "a", encoding="utf-8") as _f:
                _f.write("\n--- FATAL in run.py main (mode=%s) ---\n" % amode)
                _tb.print_exc(file=_f)
        except Exception:  # noqa: BLE001
            pass
        raise


if __name__ == "__main__":
    main()
