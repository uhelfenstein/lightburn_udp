# LightBurn UDP Communication

A Python library for communicating with LightBurn software via UDP protocol.

Commands are sent to port **19840**; LightBurn replies on port **19841**.
Runs on Windows, macOS and Linux from the same source, with no third-party
dependencies.

## Features

- Send UDP commands to LightBurn
- Check LightBurn status
- Load files programmatically
- Start jobs
- Automatic LightBurn startup and executable discovery
- Error handling and timeouts
- Command-line interface

## Installation

```bash
pip install lightburn-udp
```

Or from a checkout:

```bash
pip install .
```

Or directly from GitHub:

```bash
pip install git+https://github.com/uhelfenstein/lightburn_udp.git
```

Requires Python 3.8+.

## Quick Start

```python
from lightburn_udp import LightBurnUDPCommunication

# Pass the path explicitly...
lb = LightBurnUDPCommunication(r"C:\Program Files\LightBurn\LightBurn.exe")

# ...or omit it and let the library find LightBurn on any platform.
lb = LightBurnUDPCommunication()

with lb:
    if lb.ensure_lightburn_running(startup_timeout=30.0):
        print("Status:", lb.get_status())
        lb.load_file("designs/box.lbrn2", force=True)
        lb.start_job()
```

The `with` block releases UDP port 19841 on exit. Without it, call `lb.close()`
when finished, or the port stays held for the lifetime of the object.

Paths may be given in whatever form suits the platform — `~`, environment
variables and relative paths are all expanded before being sent, since LightBurn
resolves paths against its own working directory rather than the caller's.

## Locating LightBurn

`LightBurnUDPCommunication()` with no path searches `PATH` and then the standard
install locations:

| Platform | Searched |
|---|---|
| Windows | `%ProgramFiles%`, `%ProgramFiles(x86)%`, `%LOCALAPPDATA%\Programs`, and `\Program Files\LightBurn\` on every drive letter |
| macOS | `/Applications/LightBurn.app`, `~/Applications/LightBurn.app` |
| Linux | `/opt/LightBurn`, `/usr/local/bin`, `~/.local/share/LightBurn`, and AppImages under `~/Applications`, `~/.local/bin`, `~/Downloads` |

Check what it finds with:

```bash
python -m lightburn_udp detect
```

On macOS you may pass the `.app` bundle directly; the inner binary is resolved
automatically. On Linux, an AppImage must be executable — `chmod +x` it, or call
`lb.make_executable()`.

## Command line

```bash
python -m lightburn_udp detect                 # locate the executable
python -m lightburn_udp ping
python -m lightburn_udp status                 # Idle or Running
python -m lightburn_udp load designs/box.lbrn2 --force
python -m lightburn_udp start
python -m lightburn_udp run designs/box.lbrn2  # load, then start
python -m lightburn_udp close --force
```

After installation the same commands are available as `lightburn-udp ...`.

Useful flags: `--path` to override the executable, `--ip` for a LightBurn on
another machine, `--startup-timeout` for slow first launches, and
`--no-autostart` to fail rather than launch LightBurn.

## API Reference

### LightBurnUDPCommunication

`LightBurnUDPCommunication(lightburn_path=None, udp_ip="127.0.0.1", udp_out_port=19840, udp_in_port=19841, bind_ip=None)`

`lightburn_path` may be a full path, a bare name resolved on `PATH`, or `None`
to auto-detect. `bind_ip` defaults to `udp_ip` for loopback and `0.0.0.0`
otherwise, so a LightBurn on another host works without extra configuration.

Raises `FileNotFoundError` if the executable is missing, or `PermissionError` on
POSIX if it exists but is not executable.

#### Methods

- `ping(timeout=1.0)`: Check if LightBurn is responding
- `get_status(timeout=1.0)`: Get current status ("Idle" or "Running")
- `load_file(file_path, force=False, timeout=1.0)`: Load a file
- `start_job(timeout=1.0)`: Start the current job
- `close_lightburn(force=False, timeout=1.0)`: Close LightBurn
- `ensure_lightburn_running(startup_timeout=10.0, poll_interval=1.0)`: Start LightBurn if not running
- `send_message(message, timeout=1.0)`: Send a raw command
- `start_lightburn()`: Launch LightBurn detached from the calling process
- `is_process_running()`: Check for the process itself, not just UDP
- `make_executable()`: Set the executable bit (no-op on Windows)
- `close()`: Release the bound receive socket; also usable as a context manager

#### Module level

- `find_lightburn()`: Return the detected executable path, or None

## Requirements

- Python 3.8+
- LightBurn software

## Testing

`test_lightburn_udp.py` runs the library against a mock LightBurn speaking the
same protocol, so it needs neither a laser nor a LightBurn install. Windows and
macOS branches are covered by patching the platform flags, so the full suite is
meaningful on any single machine.

```bash
python test_lightburn_udp.py     # standalone
pytest test_lightburn_udp.py     # or under pytest
```

## Troubleshooting

**"UDP port 19841 is already in use"** — another script holds it. The error
message includes the right command for your platform (`ss`, `lsof` or `netstat`).

**Ping times out but LightBurn is open** — confirm something is listening on
19840. If nothing is bound, your build may not have the UDP listener active.

**Autostart does nothing over SSH or from a service (Linux)** — LightBurn is a
GUI application and needs `DISPLAY` or `WAYLAND_DISPLAY` set.

**Talking to LightBurn on another machine** — pass `udp_ip="192.168.1.50"` and
allow the reply port through the firewall (`sudo ufw allow 19841/udp`, or an
inbound UDP rule in Windows Defender Firewall).

## License

MIT License
