"""Shared HTTP helper for the test suites.

Retries on HTTP 429 (the app's login burst rate limit) so suites can run
back-to-back — in CI and locally — without manual sleeps between them.
"""
import json
import time
import urllib.error
import urllib.request


def http(BASE, method, path, tok=None, data=None, raw=False, headers=None,
         retries=4, wait=65):
    """urllib request with 429 back-off. Returns (status, parsed_or_bytes)."""
    status, parsed = 0, None
    for _attempt in range(retries):
        if isinstance(data, (bytes, bytearray)):
            body = bytes(data)  # already-encoded payload (e.g. multipart upload)
        else:
            body = json.dumps(data).encode() if data is not None else None
        h = dict(headers or {})
        if data is not None and not isinstance(data, (bytes, bytearray)):
            h.setdefault("Content-Type", "application/json")
        if tok:
            h.setdefault("Authorization", "Bearer " + tok)
        r = urllib.request.Request(BASE + path, data=body, method=method, headers=h)
        try:
            with urllib.request.urlopen(r, timeout=300) as resp:
                b = resp.read()
                return resp.status, (b if raw else (json.loads(b) if b else None))
        except urllib.error.HTTPError as e:
            b = e.read()
            try:
                parsed = json.loads(b)
            except Exception:
                parsed = b[:200].decode(errors="replace")
            status = e.code
            if status == 429 and _attempt < retries - 1:
                time.sleep(wait)  # the limiter's window is 1 minute
                continue
            return status, parsed
    return status, parsed
