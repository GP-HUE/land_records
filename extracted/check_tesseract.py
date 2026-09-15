"""Build-time helper for build_exe.bat.

Usage:  python check_tesseract.py <path-to-tesseract-exe>

Prints the first line of `tesseract --version`, then exits 0 if the
version is >= 5.5 and 1 otherwise. The .bat uses the exit code to decide
whether to bundle the found Tesseract or install the official 5.5.3.

Deliberately tiny and dependency-free (subprocess + re only) so it runs
on any Python 3.9+ with zero pip packages.
"""
import re
import subprocess
import sys

if len(sys.argv) < 2:
    print("usage: check_tesseract.py <tesseract.exe>")
    sys.exit(1)

path = sys.argv[1]
try:
    proc = subprocess.run([path, "--version"], capture_output=True,
                          text=True, timeout=30)
    out = (proc.stdout or "") + (proc.stderr or "")
except Exception as e:  # noqa: BLE001
    print("could not run tesseract: %s" % e)
    sys.exit(1)

lines = [ln for ln in out.strip().splitlines() if ln.strip()]
first = lines[0] if lines else "tesseract (no version output)"
print(first)

m = re.search(r"v?(\d+)\.(\d+)", first)
if not m:
    print("version check: COULD NOT PARSE (treated as too old)")
    sys.exit(1)

major, minor = int(m.group(1)), int(m.group(2))
ok = major > 5 or (major == 5 and minor >= 5)
print("version check: " + ("OK (>= 5.5)" if ok else "TOO OLD (need >= 5.5)"))
sys.exit(0 if ok else 1)
