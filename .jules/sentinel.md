## 2023-10-27 - [Fix shell=True]
**Vulnerability:** Found `subprocess.Popen` commands with `shell=True` and list arguments in `gui/tray_manager.py`.
**Learning:** `shell=True` with a list argument is an anti-pattern that can lead to command injection or broken execution on POSIX systems.
**Prevention:** Avoid using `shell=True` unless strictly necessary and never with unvalidated input or when passing arguments as a list.
