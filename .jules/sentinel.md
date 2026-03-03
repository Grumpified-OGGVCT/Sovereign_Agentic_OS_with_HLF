## 2023-10-27 - [Fix shell=True]
**Vulnerability:** Found `subprocess.Popen` commands with `shell=True` and list arguments in `gui/tray_manager.py`.
**Learning:** `shell=True` with a list argument is an anti-pattern that can lead to command injection or broken execution on POSIX systems.
**Prevention:** Avoid using `shell=True` unless strictly necessary and never with unvalidated input or when passing arguments as a list.
## 2026-03-02 - SSRF and Confinement Bypass Discovery in Host Function Dispatcher

**Vulnerability:** The newly added `_dapr_http` function uses `httpx.get(query_or_url, timeout=30.0, follow_redirects=True)` without any input validation, IP blocklisting, or protocol restriction, leading to a Critical Server-Side Request Forgery (SSRF) vulnerability. The `_acfs_path` function uses `resolve()` which resolves symlinks, allowing a Path Traversal vulnerability if an attacker creates a malicious symlink within `BASE_DIR`.

**Learning:** When reviewing code that handles user-provided URLs or paths, especially in low-level dispatcher or host-function boundaries, always ensure strict isolation is enforced. Python's `Path.resolve()` is dangerous when confining paths because it follows symlinks before checking `is_relative_to()`. HTTP requests MUST blocklist internal IPs (`127.0.0.1`, `169.254.169.254`, `10.x.x.x`) to prevent internal network compromise.

**Prevention:**
1. For HTTP requests, use a hardened proxy, blocklist internal IPs/hostnames, restrict allowed protocols (only `http`/`https`), and disable `follow_redirects` unless strictly necessary (and if necessary, validate the redirect target).
2. For ACFS confinement, use `os.path.realpath` securely, or manually check for symlinks in the path hierarchy before resolution, ensuring the final target physically resides within `BASE_DIR` without traversing outside via links.
