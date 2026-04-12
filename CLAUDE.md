# Toddler TV — Development Guide

## Security Guardrails

These rules were introduced after a full security audit (2026-04-12).
Each rule cites the finding it prevents and the file where the fix lives.
Do not remove or relax a rule without a documented security review.

---

### 1. HTTP Handler — `web_remote.py`

**Every new route must gate on auth and CSRF before touching any data.**

```python
# REQUIRED pattern for every do_GET / do_POST addition:
def do_GET(self):
    if not self._check_auth():          # CWE-306
        self._send_auth_challenge()
        return
    ...

def do_POST(self):
    if not self._check_auth():          # CWE-306
        self._send_auth_challenge()
        return
    ...
    if not secrets.compare_digest(first("csrf"), _CSRF_TOKEN):   # CWE-352
        self._respond(403, "<h1>403 Forbidden</h1>")
        return
    ...
```

**Every POST body read must be capped before `rfile.read()`.**

```python
# REQUIRED — never read an uncapped body (CWE-400)
length = min(int(self.headers.get("Content-Length", 0) or 0), MAX_BODY)
body = self.rfile.read(length).decode("utf-8", errors="replace")
```

**Every integer derived from a POST field must be bounded.**

```python
# REQUIRED — never pass a raw int from user input to range() (CWE-400)
count = min(int(first("count", "0")), MAX_PERIODS)
```

**Every enum-type field must be whitelisted, not sanitised.**

```python
# REQUIRED — reject unknowns at the gate, never try to clean them (CWE-20)
if scene not in set(SCENES.keys()):
    scene = "sleeping"   # hardcoded safe default
```

**Every time/integer parse from user input must be wrapped and clamped.**

```python
# REQUIRED — bare int() on user input will crash the handler thread (CWE-755)
try:
    sh, sm = int(parts[0]), int(parts[1])
    sh, sm = max(0, min(23, sh)), max(0, min(59, sm))
except (ValueError, IndexError):
    continue
```

**Every HTTP response path must call `_send_security_headers()`.**

```python
# REQUIRED — X-Frame-Options, X-Content-Type-Options, CSP on every response
def _respond(self, code, html):
    self.send_response(code)
    self.send_header("Content-Type", "text/html; charset=utf-8")
    self._send_security_headers()   # never omit this
    self.end_headers()
    self.wfile.write(html.encode("utf-8"))
```

**Never suppress access logs with `pass`.**

```python
# FORBIDDEN
def log_message(self, fmt, *args):
    pass   # silently drops all access events — CWE-778

# REQUIRED
def log_message(self, fmt, *args):
    _log.info(fmt, *args)
```

**Sanitise the peer address before it reaches any log sink.**

```python
# REQUIRED — \n in client_address enables log-line injection
safe_peer = re.sub(r"[^\w.:\[\]-]", "?", self.client_address[0])
_log.warning("... from %s", safe_peer)
```

---

### 2. File Paths — `media.py`

**Every video ID extracted from a URL must pass `_validate_video_id()` before use.**

```python
# REQUIRED — CWE-22 Layer 1: reject at the source
_SAFE_VIDEO_ID = re.compile(r'^[A-Za-z0-9_-]{1,20}$')

def get_video_id(url):
    raw = ...
    return _validate_video_id(raw)   # raises ValueError if unsafe
```

**Every file path constructed from a video ID must pass a `realpath` boundary check.**

```python
# REQUIRED — CWE-22 Layer 2: verify at the sink even if the source was validated
real = os.path.realpath(candidate)
if not real.startswith(_VIDEOS_DIR_REAL + os.sep):
    raise ValueError(f"Path escape: {candidate!r}")
```

**Never concatenate user-supplied strings directly into `os.path.join()`.**

```python
# FORBIDDEN
path = os.path.join(VIDEOS_DIR, user_input + ".mp4")

# REQUIRED
video_id = _validate_video_id(user_input)
path = os.path.join(VIDEOS_DIR, f"{video_id}.mp4")
# then realpath-check path before use
```

---

### 3. Subprocesses — `media.py`

**Always use the list form of `subprocess.run`. Never use `shell=True`.**

```python
# FORBIDDEN — shell=True passes the entire string to cmd.exe / sh
subprocess.run(f"yt-dlp {url}", shell=True)

# REQUIRED — each element is a distinct argv token; no shell expansion
subprocess.run([sys.executable, "-m", "yt_dlp", ..., url], shell=False)
```

**Never add undocumented yt-dlp flags.**
The flags `--js-runtimes` and `--remote-components` were removed in this audit
because they are not in the stable yt-dlp CLI and risked fetching remote code.
Before adding any new yt-dlp flag, verify it exists in the yt-dlp changelog for
the pinned version in `requirements.txt`.

**Never interpolate subprocess `stderr` output directly into `print()` without stripping.**
yt-dlp stderr is third-party output and may contain terminal escape sequences.
See deferred finding N3 in memory for the fix when this becomes a priority.

---

### 4. Dependencies — `requirements.txt`

**Every package must be pinned with `==`. No exceptions.**

```
# FORBIDDEN
yt-dlp
yt-dlp>=2025.1.1
yt-dlp~=2025.1

# REQUIRED
yt-dlp==2026.3.17
```

**Upgrading a package is a security event, not a routine commit.**

Upgrade checklist:
1. Read the changelog for every version between the old and new pin.
2. For `yt-dlp`: treat each release as a third-party code execution event — it downloads and runs extractor scripts.
3. For `Pillow`: check for image-parsing CVEs at [https://pillow.readthedocs.io/en/stable/releasenotes/](https://pillow.readthedocs.io/en/stable/releasenotes/).
4. Run `python -m unittest discover tests/` — `test_security_dependencies.py` will catch pin/installed mismatches.
5. Commit `requirements.txt` in the same PR as the code that required the upgrade.

---

### 5. Secrets & Sensitive Files

**`cookies.txt`, `channels.json`, `schedule.json`, and `videos/` are in `.gitignore`.
Never force-add them.**

```bash
# FORBIDDEN
git add -f cookies.txt
```

`cookies.txt` contains YouTube session credentials. If it is ever accidentally
staged, rotate the YouTube account's cookies immediately.

**The web remote password (`_BOOT_PASSWORD`) is ephemeral — generated fresh on every
process start with `secrets.token_urlsafe(12)`. Never hardcode a password in source.**

---

### 6. Security Test Suite

These test files must stay green before every merge:

| File | What it guards |
|------|----------------|
| `tests/test_security_web_remote.py` | Auth, CSRF, DoS caps, scene whitelist, time parsing, logging, security headers, log injection |
| `tests/test_security_media.py` | Video ID allowlist, path boundary, removed yt-dlp flags |
| `tests/test_security_dependencies.py` | All packages pinned with `==`, installed versions match pins |

Run the full suite:
```bash
python -m unittest discover tests/ -v
```

Adding a new HTTP endpoint, file path operation, or subprocess call requires
a corresponding test in the relevant security test file before the PR is merged.

---

### 7. Deferred Findings

Four low-severity findings were identified in `media.py` but not yet patched.
See memory file `project_deferred_findings_media.md` for full detail.

| ID | Location | Summary |
|----|----------|---------|
| N1 | `media.py` — subprocess calls | Option injection via `--`-prefixed URL |
| N2 | `media.py` → `images.py` | `thumbnail` URL from yt-dlp reaches `urllib.urlopen` without scheme check |
| N3 | `media.py` — stderr print | Terminal escape sequences in yt-dlp stderr |
| N4 | `media.py` — `_find_ffmpeg()` | CWD binary planting at import time |
