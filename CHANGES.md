# Changes in 1.1.0

Everything below keeps the existing public API working. The UDP protocol is
untouched — same ports, same command strings, same responses.

Existing Windows behaviour is preserved throughout; the platform-specific code
is dispatched at runtime rather than assuming a target OS.

## Bug fixes

### `__init__.py` circular import

The package previously did:

```python
from lightburn_udp import LightBurnUDPCommunication
```

Inside the package `lightburn_udp`, that is an absolute import resolving to the
package itself rather than the sibling module, so it fails on every platform:

```
ImportError: cannot import name 'LightBurnUDPCommunication' from partially
initialized module 'lightburn_udp' (most likely due to a circular import)
```

This is why the README documented `from lightburn_udp.lightburn_udp import ...`
— that path bypasses `__init__.py`. Now a relative import, so both spellings
work and the short one is documented.

### `NameError` in the `finally` block

`out_sock` and `in_sock` were created inside the `try`. If the first
`socket.socket()` call raised, `finally` referenced an undefined name, and the
bare `except:` inside it swallowed the resulting `NameError` — masking the real
error. Cleanup is now guarded.

### Receive socket bound to a non-local address

`bind()` used `udp_ip`, the address LightBurn listens on. For a LightBurn on
another machine that address is not local, so the bind either fails or the reply
never arrives. `bind_ip` now defaults to `udp_ip` for loopback and `0.0.0.0`
otherwise. This is the failure behind the recurring "UDP Commands Errors"
reports on the LightBurn forum.

### Relative paths in `load_file`

LightBurn resolves paths against its own working directory, not the caller's, so
a relative path silently failed or loaded the wrong file. Paths are now expanded
(`~`, environment variables) and made absolute before sending.

## Socket handling

**Persistent receive socket.** Port 19841 was bound and unbound on every single
call. `ensure_lightburn_running` pings in a loop, so a ten-second startup wait
meant ten bind/unbind cycles, each racing the previous teardown. The socket is
now bound once, lazily, and held — hence the new `close()` and context manager
support, so the port is released at a predictable point rather than at garbage
collection.

**Stale packet draining.** A consequence of the above: a late reply to a
timed-out command would sit in the buffer and be read as the answer to the
*next* command, shifting every subsequent response by one. The buffer is drained
before each send.

**Timeout accounting.** A packet from an unrelated sender consumed the whole
timeout. Responses are now filtered by source address against a monotonic
deadline, so `timeout=1.0` means one second total, not one second per packet.

**Address reuse.** POSIX needs `SO_REUSEADDR` to avoid spurious `EADDRINUSE`.
Windows is deliberately given `SO_EXCLUSIVEADDRUSE` instead — there,
`SO_REUSEADDR` genuinely permits two sockets to share a port, which would let a
second instance silently steal replies. A port already in use now raises a clear
message naming the platform's diagnostic command (`ss`, `lsof` or `netstat`).

## Process launching

`Popen(..., shell=False)` inherits the parent's process group and stdio. On
POSIX that means LightBurn can be killed when the launching script exits, and
its output can block on a full pipe.

Launching is now detached per platform: `start_new_session=True` on POSIX, and
`DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP` on Windows. Worth noting that
`start_new_session` is accepted but *silently ignored* on Windows — it appears in
CPython's Windows `_execute_child` as `unused_start_new_session` — so it could
not simply be passed unconditionally.

Added `is_process_running()`, using `tasklist` on Windows and `pgrep` elsewhere.
Ping alone cannot distinguish "not started" from "started but still loading", so
`ensure_lightburn_running` used to launch a second copy of LightBurn while the
first was still coming up. It now waits instead.

`tasklist` output is decoded with `encoding="oem", errors="replace"`. Its output
is in the console OEM codepage (cp850 on a German system), and decoding that as
the ANSI codepage raises `UnicodeDecodeError` inside subprocess's reader thread.
That exception is swallowed there and simply yields empty output, so
`is_process_running()` would have silently returned False forever on any
non-English Windows — taking `ensure_lightburn_running`'s "don't launch a second
copy" guard down with it. Caught on a German Windows 11 test run.

The image-name match is also case-insensitive and does not depend on the
localised "no tasks are running" notice.

## Executable discovery and validation

`lightburn_path` is now optional. `find_lightburn()` searches `PATH` and the
standard install locations for the running platform, including every drive
letter on Windows, since a secondary-drive install is common — the original
module's own example used `E:\Program Files\LightBurn`.

Validation is platform-aware: the executable bit is only meaningful on POSIX, so
`os.access(X_OK)` is checked there (raising `PermissionError` with the exact
`chmod` command, the usual AppImage snag) and skipped on Windows, where
`os.access(X_OK)` reports nothing useful for regular files.

macOS `.app` bundles are accepted and resolved to the inner
`Contents/MacOS/LightBurn` binary.

## Other

- Replaced the `__main__` demo block, which had a hardcoded
  `E:\Program Files\LightBurn\LightBurn.exe`, with a real CLI:
  `python -m lightburn_udp`, or `lightburn-udp` after installation.
- `time.time()` → `time.monotonic()` for timing, so an NTP step mid-wait cannot
  skew a timeout.
- Responses decoded with `errors="replace"` and stripped of trailing nulls.
- `except Exception` narrowed to `OSError` where appropriate, so genuine bugs
  surface instead of being reported as communication failures.
- `requires-python` raised from 3.7 to 3.8 (3.7 has been end-of-life since June
  2023). Classifiers now list Windows, macOS and Linux explicitly alongside
  "OS Independent".
- Dropped `setup.py`; `pyproject.toml` alone is sufficient for modern pip. If
  you would rather keep it for older tooling, the original still works — just
  bump its `version` and `python_requires` to match.

## Releasing

The version lives in exactly one place: `__version__` in
`lightburn_udp/lightburn_udp.py`. `pyproject.toml` reads it via
`[tool.setuptools.dynamic]`, and `__init__.py` re-exports it, so
`lightburn_udp.__version__`, `lightburn_udp.lightburn_udp.__version__`, the
built wheel and `lightburn-udp --version` can no longer drift apart.

To cut a release:

1. Edit `__version__` in `lightburn_udp/lightburn_udp.py`.
2. `python -m pytest` on Windows and Linux.
3. `python -m build` and confirm the filename carries the expected version.
4. `git tag v1.1.0 && git push --tags`.
5. `twine upload dist/*` if publishing to PyPI.

### Is 1.1.0 the right number?

Under semver, yes — the additions are backwards compatible and the rest are bug
fixes. Two changes are worth a conscious decision, though:

- **The receive socket is now held for the object's lifetime.** Code that
  created two `LightBurnUDPCommunication` instances on the same input port
  previously worked by accident, because the port was released between calls.
  It now raises on the second instance. That is the intended fix — two objects
  racing for the same port silently stole each other's replies — but if you
  consider it breaking, 2.0.0 is defensible.
- **`requires-python` rose from 3.7 to 3.8.** 3.7 has been end-of-life since
  June 2023, so this only matters if you know of users on it.

Everything else — the new optional `lightburn_path`, `close()`, the context
manager, `is_process_running()`, `make_executable()`, `find_lightburn()`, the
CLI — is additive.

## Testing

`test_lightburn_udp.py` — 38 checks against a mock LightBurn, covering
validation, every command, timeout behaviour, bind-address selection, socket
lifecycle and process helpers. The Windows and macOS branches are exercised by
patching the platform flags, so the whole suite runs meaningfully on one
machine. It needs no laser and no LightBurn install.

Runs both as a plain script and under pytest:

```
python test_lightburn_udp.py
pytest test_lightburn_udp.py
```

Earlier revisions only worked as a script — everything executed at import and
called `sys.exit()`, which made pytest abort during collection with an
`INTERNALERROR`. The body now lives in `run_all()`, with a `test_*` wrapper for
pytest and the exit code confined to the `__main__` guard.

The suite also no longer fabricates a fake executable to launch. Writing a shell
script to a `.exe` and running it fails on Windows with `WinError 216`, which
was a defect in the test rather than the library. The launch and process-lookup
checks now use `sys.executable`, which is by definition both running and
launchable on every platform.

Verified passing on Linux (38/38) and on Windows 11 (German locale).
