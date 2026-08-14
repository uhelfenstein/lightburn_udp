"""
LightBurn UDP Communication Module

Communicates with LightBurn via its UDP command interface: commands are sent to
port 19840 and LightBurn replies to port 19841.

Runs on Windows, macOS and Linux from the same source. Platform differences are
confined to executable discovery, process launching and process lookup; the
protocol itself is identical everywhere.
"""

from __future__ import annotations

import errno
import glob
import os
import shutil
import socket
import stat
import string
import subprocess
import sys
import tempfile
import threading
import time

# Single source of truth for the version. pyproject.toml reads this attribute,
# and __init__.py re-exports it, so a release is a one-line change here.
__version__ = "1.2.0"
__author__ = "Urs Helfenstein"
__all__ = ["LightBurnUDPCommunication", "find_lightburn", "PortInUseError"]

_IS_WINDOWS = os.name == "nt"
_IS_MACOS = sys.platform == "darwin"

#: Executable name to look for on $PATH, per platform.
_EXE_NAMES = ("LightBurn.exe",) if _IS_WINDOWS else (
    "LightBurn",
    "lightburn",
    "LightBurn.AppImage",
)


def _expand(path):
    """Expand ~ and environment variables, then make the path absolute."""
    return os.path.abspath(os.path.expanduser(os.path.expandvars(str(path))))


def _windows_candidates():
    """Standard install locations on Windows, across all drive letters."""
    for var in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)", "LOCALAPPDATA"):
        root = os.environ.get(var)
        if root:
            yield os.path.join(root, "LightBurn", "LightBurn.exe")
            yield os.path.join(root, "Programs", "LightBurn", "LightBurn.exe")

    # LightBurn is often installed to a secondary drive.
    for drive in string.ascii_uppercase:
        yield f"{drive}:\\Program Files\\LightBurn\\LightBurn.exe"
        yield f"{drive}:\\Program Files (x86)\\LightBurn\\LightBurn.exe"


def _macos_candidates():
    yield "/Applications/LightBurn.app/Contents/MacOS/LightBurn"
    yield "~/Applications/LightBurn.app/Contents/MacOS/LightBurn"


def _linux_candidates():
    # The Linux build ships as an archive or AppImage rather than a package, so
    # there is no single canonical location.
    yield from (
        "/opt/LightBurn/LightBurn",
        "/opt/lightburn/LightBurn",
        "/usr/local/bin/LightBurn",
        "/usr/bin/LightBurn",
        "~/LightBurn/LightBurn",
        "~/.local/share/LightBurn/LightBurn",
        "~/Applications/LightBurn",
    )


#: An extracted AppImage is a directory whose entry point is a shell stub named
#: AppRun; the real binary sits at usr/bin/LightBurn and is only reached via
#: exec. Searching for a file called "LightBurn" therefore misses these trees
#: entirely, which is why an explicit path used to be mandatory.
_APPDIR_ENTRY = "AppRun"


def _appdir_entry(path):
    """
    If path is an extracted-AppImage directory, return its AppRun stub.

    Returns:
        str or None: Absolute path to the AppRun, or None if this is not an
        extracted AppImage directory.
    """
    if not os.path.isdir(path):
        return None
    entry = os.path.join(path, _APPDIR_ENTRY)
    return entry if _runnable(entry) else None


_GLOBS = {
    "linux": (
        "/opt/LightBurn*/LightBurn*",
        "~/Applications/LightBurn*.AppImage",
        "~/Applications/LightBurn*/LightBurn*",
        "~/.local/bin/LightBurn*.AppImage",
        "~/Downloads/LightBurn*.AppImage",
        # Extracted AppImages, by their AppRun entry point. Kept last so a real
        # binary always wins if both are present.
        "/opt/LightBurn*/AppRun",
        "~/Applications/LightBurn*/AppRun",
        "~/Programme/LightBurn*/AppRun",
        "~/Programs/LightBurn*/AppRun",
        "~/Downloads/LightBurn*/AppRun",
        "~/LightBurn*/AppRun",
    ),
    "darwin": ("/Applications/LightBurn*.app/Contents/MacOS/LightBurn",),
    "win32": (),
}


def _runnable(path):
    """True if path is a file this platform can execute."""
    if not os.path.isfile(path):
        return False
    # On Windows os.access(X_OK) is not meaningful; extension is what matters.
    return True if _IS_WINDOWS else os.access(path, os.X_OK)


def find_lightburn():
    """
    Try to locate the LightBurn executable on this machine.

    Searches PATH, then the standard install locations for the current
    platform, then common alternative locations.

    Returns:
        str or None: Absolute path to the executable, or None if not found.
    """
    for name in _EXE_NAMES:
        found = shutil.which(name)
        if found:
            return os.path.abspath(found)

    if _IS_WINDOWS:
        candidates = _windows_candidates()
    elif _IS_MACOS:
        candidates = _macos_candidates()
    else:
        candidates = _linux_candidates()

    for candidate in candidates:
        candidate = _expand(candidate)
        if _runnable(candidate):
            return candidate
        entry = _appdir_entry(candidate)
        if entry:
            return entry

    key = "win32" if _IS_WINDOWS else ("darwin" if _IS_MACOS else "linux")
    for pattern in _GLOBS[key]:
        for candidate in sorted(glob.glob(_expand(pattern))):
            if _runnable(candidate):
                return candidate
            entry = _appdir_entry(candidate)
            if entry:
                return entry

    return None


class PortInUseError(OSError):
    """
    The receive port could not be bound because something else holds it.

    A distinct type because it means something categorically different from a
    timeout: the message never left, so retrying or waiting longer cannot help.
    Previously this surfaced as a plain False, indistinguishable from "LightBurn
    did not answer", which sent debugging down the wrong path.
    """


def _port_check_hint(port):
    """Platform-appropriate command for finding what holds a UDP port."""
    if _IS_WINDOWS:
        return f'netstat -ano -p UDP | findstr ":{port}"'
    if _IS_MACOS:
        return f"lsof -nP -iUDP:{port}"
    return f"ss -lunp 'sport = :{port}'"


def _is_loopback(ip):
    try:
        return socket.inet_aton(ip).startswith(b"\x7f")
    except OSError:
        return False


class LightBurnUDPCommunication:
    """
    Handles UDP communication with LightBurn software.

    Provides methods to send messages, ping, and automatically start LightBurn
    if needed. Works identically on Windows, macOS and Linux.

    The receiving socket is bound lazily on first use and then held open for the
    lifetime of the object, so only one instance per input port can exist at a
    time. Use close(), or the object as a context manager, to release it.
    """

    def __init__(
        self,
        lightburn_path=None,
        udp_ip="127.0.0.1",
        udp_out_port=19840,
        udp_in_port=19841,
        bind_ip=None,
    ):
        """
        Initialize the LightBurn UDP communication handler.

        Args:
            lightburn_path (str or None): Path to the LightBurn executable, or a
                bare name to look up on PATH. If None, auto-detection is
                attempted. Accepts a macOS .app bundle or a Linux AppImage.
            udp_ip (str): Address LightBurn is listening on (default "127.0.0.1")
            udp_out_port (int): Port for outgoing messages (default 19840)
            udp_in_port (int): Port for incoming messages (default 19841)
            bind_ip (str or None): Local address to bind the receive socket to.
                Defaults to udp_ip for loopback, or "0.0.0.0" when talking to a
                LightBurn on another host.

        Raises:
            FileNotFoundError: If the executable cannot be found.
            PermissionError: If the file exists but is not executable (POSIX).
        """
        self.lightburn_path = self._resolve_executable(lightburn_path)
        self.udp_ip = udp_ip
        self.udp_out_port = int(udp_out_port)
        self.udp_in_port = int(udp_in_port)
        self.bind_ip = bind_ip if bind_ip is not None else (
            udp_ip if _is_loopback(udp_ip) else "0.0.0.0"
        )
        self._in_sock = None
        # One socket is shared by every command, and each command drains it
        # first. Two threads overlapping would let one discard the other's
        # reply, so commands are serialised. Reentrant because
        # ensure_lightburn_running calls ping() while already holding nothing,
        # but callers may reasonably wrap their own sequences in the same lock.
        self._lock = threading.RLock()
        #: Path of the log file the last start_lightburn() redirected output to.
        self.launch_log_path = None

    # ------------------------------------------------------------------ #
    # Executable handling
    # ------------------------------------------------------------------ #

    @staticmethod
    def _resolve_executable(lightburn_path):
        """Resolve, validate, and return the path to the LightBurn binary."""
        if lightburn_path is None:
            found = find_lightburn()
            if found is None:
                raise FileNotFoundError(
                    "Could not auto-detect LightBurn. Pass the path explicitly, "
                    "e.g. LightBurnUDPCommunication(r'C:\\Program Files\\"
                    "LightBurn\\LightBurn.exe') or "
                    "LightBurnUDPCommunication('/opt/LightBurn/LightBurn')."
                )
            return found

        raw = str(lightburn_path)

        # Allow a bare command name to be resolved via PATH.
        if not os.path.dirname(raw.replace("\\", os.sep).replace("/", os.sep)):
            found = shutil.which(raw)
            if found:
                return os.path.abspath(found)

        path = _expand(raw)

        # A macOS .app bundle is a directory; the binary lives inside it.
        if path.endswith(".app") and os.path.isdir(path):
            inner = os.path.join(path, "Contents", "MacOS", "LightBurn")
            if os.path.isfile(inner):
                path = inner

        # Likewise an extracted AppImage on Linux: accept the directory and
        # resolve to its AppRun stub.
        if not _IS_WINDOWS and os.path.isdir(path):
            entry = _appdir_entry(path)
            if entry:
                path = entry

        if not os.path.exists(path):
            raise FileNotFoundError(f"LightBurn executable not found at: {path}")

        if not os.path.isfile(path):
            raise FileNotFoundError(f"Not a file: {path}")

        # The executable bit only means something on POSIX.
        if not _IS_WINDOWS and not os.access(path, os.X_OK):
            raise PermissionError(f"{path} is not executable. Run: chmod +x '{path}'")

        return path

    def make_executable(self):
        """
        Set the executable bit on the LightBurn binary.

        Useful for Linux AppImages, which usually arrive without it. A no-op on
        Windows. Safe to call repeatedly.

        Returns:
            bool: True on success, False otherwise.
        """
        if _IS_WINDOWS:
            return True
        try:
            current = os.stat(self.lightburn_path).st_mode
            os.chmod(
                self.lightburn_path,
                current | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH,
            )
            return True
        except OSError as exc:
            print(f"Could not set executable bit on {self.lightburn_path}: {exc}")
            return False

    # ------------------------------------------------------------------ #
    # Socket handling
    # ------------------------------------------------------------------ #

    def _get_in_socket(self):
        """Return the bound receive socket, creating it on first use."""
        if self._in_sock is not None:
            return self._in_sock

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # This port must be held exclusively. SO_REUSEADDR is habit carried over
        # from TCP, where it only sidesteps TIME_WAIT; UDP has no TIME_WAIT, so
        # it buys nothing here and does real damage: on Linux two datagram
        # sockets with SO_REUSEADDR may both bind the same address, and the
        # kernel then delivers each datagram to exactly one of them — the one
        # bound most recently. The older instance goes deaf while every
        # diagnostic still looks healthy, since the port is bound and LightBurn
        # is replying, just to somebody else. Binding without it turns that
        # silent theft back into an immediate, honest PortInUseError.
        #
        # On Windows SO_REUSEADDR is worse still (it lets sockets genuinely
        # share a port), so ask for exclusivity explicitly there.
        if _IS_WINDOWS:
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            except (AttributeError, OSError):
                pass

        try:
            sock.bind((self.bind_ip, self.udp_in_port))
        except OSError as exc:
            sock.close()
            if exc.errno in (errno.EADDRINUSE, getattr(errno, "WSAEADDRINUSE", None)):
                raise PortInUseError(
                    f"UDP port {self.udp_in_port} on {self.bind_ip} is already in "
                    f"use. Another script or a previous instance of this object "
                    f"is likely holding it — call close() on the old one. "
                    f"Check with: {_port_check_hint(self.udp_in_port)}"
                ) from exc
            raise

        self._in_sock = sock
        return sock

    def _drain(self, sock):
        """Discard any stale datagrams queued from an earlier command."""
        sock.setblocking(False)
        try:
            while True:
                try:
                    sock.recvfrom(65535)
                except BlockingIOError:
                    return
                except OSError:
                    return
        finally:
            sock.setblocking(True)

    def send_message(self, message, timeout=1.0):
        """
        Send a UDP message to LightBurn and wait for a response.

        Args:
            message (str): The message to send to LightBurn
            timeout (float): Seconds to wait for a response (default 1.0)

        Returns:
            str or False: The response text, or False on timeout or error.

        Raises:
            PortInUseError: If the receive port is held by something else. This
                is deliberately not swallowed into a False: no message was ever
                sent, so it is a configuration problem, not a silent laser that
                needs waiting out.
        """
        out_sock = None
        with self._lock:
            try:
                in_sock = self._get_in_socket()
                self._drain(in_sock)

                out_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                out_sock.sendto(
                    message.encode("utf-8"), (self.udp_ip, self.udp_out_port)
                )

                deadline = time.monotonic() + timeout
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return False
                    in_sock.settimeout(remaining)
                    try:
                        data, addr = in_sock.recvfrom(65535)
                    except (socket.timeout, TimeoutError):
                        return False

                    # Ignore anything that did not come from the LightBurn host.
                    if self.udp_ip not in ("0.0.0.0", "") and addr[0] != self.udp_ip:
                        continue

                    return data.decode("utf-8", errors="replace").strip("\x00").strip()

            except PortInUseError:
                raise
            except OSError as exc:
                print(f"Error in UDP communication: {exc}")
                return False
            finally:
                if out_sock is not None:
                    out_sock.close()

    def close(self):
        """Release the bound receive socket."""
        if self._in_sock is not None:
            try:
                self._in_sock.close()
            finally:
                self._in_sock = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    # Commands
    # ------------------------------------------------------------------ #

    def ping(self, timeout=1.0):
        """
        Send a PING message to LightBurn.

        Returns:
            bool: True if LightBurn responds with 'OK'.
        """
        return self.send_message("PING", timeout) == "OK"

    def get_status(self, timeout=1.0):
        """
        Get the status from LightBurn.

        Returns:
            str or False: "Running" if a job is executing, "Idle" if LightBurn
            is up but idle, otherwise the raw response, or False on no response.
        """
        response = self.send_message("STATUS", timeout)
        if response == "!":
            return "Running"
        if response == "OK":
            return "Idle"
        return response

    def load_file(self, file_path, force=False, timeout=1.0):
        """
        Load a file in LightBurn.

        The path is expanded and made absolute before sending, since LightBurn
        resolves it relative to its own working directory, not the caller's.

        Args:
            file_path (str): Path to the file to load
            force (bool): Close any open file first (default False)
            timeout (float): Seconds to wait for a response (default 1.0)

        Returns:
            str or False: The response, or False on timeout or error.
        """
        resolved = _expand(file_path)
        if not os.path.exists(resolved):
            print(f"Warning: file does not exist locally: {resolved}")

        command = "FORCELOAD" if force else "LOADFILE"
        return self.send_message(f"{command}:{resolved}", timeout)

    def start_job(self, timeout=1.0):
        """Start the currently loaded job in LightBurn."""
        return self.send_message("START", timeout)

    def close_lightburn(self, force=False, timeout=1.0):
        """
        Close LightBurn.

        Args:
            force (bool): Skip prompts about unsaved work (default False)
        """
        return self.send_message("FORCECLOSE" if force else "CLOSE", timeout)

    # ------------------------------------------------------------------ #
    # Process management
    # ------------------------------------------------------------------ #

    def _proc_pids(self):
        """
        PIDs whose /proc/<pid>/exe belongs to this LightBurn installation.

        Reading the exec target rather than matching the command line fixes two
        opposite failures of `pgrep -f <basename>`: it no longer matches an
        unrelated process that merely mentions the path (a diagnostic script
        invoked with --path .../AppRun matched itself), and it still finds
        LightBurn after an AppRun stub execs usr/bin/LightBurn, at which point
        the string "AppRun" is gone from the command line entirely.

        Returns:
            list[int]: Matching PIDs, empty if none or if /proc is unavailable.
        """
        target = os.path.realpath(self.lightburn_path)
        # For an extracted AppImage the stub and the real binary live in the
        # same tree, so match on the tree rather than the exact file.
        root = os.path.dirname(target)
        appdir = os.path.dirname(root) if os.path.basename(root) == "bin" else root

        pids = []
        try:
            entries = os.listdir("/proc")
        except OSError:
            return pids

        for entry in entries:
            if not entry.isdigit():
                continue
            try:
                exe = os.path.realpath(os.readlink(f"/proc/{entry}/exe"))
            except OSError:
                # Vanished between listdir and readlink, or owned by another
                # user — neither is an error worth reporting.
                continue
            if exe == target or exe.startswith(appdir + os.sep):
                pids.append(int(entry))
        return pids

    def is_process_running(self):
        """
        Check whether a LightBurn process exists, regardless of UDP state.

        Distinguishes "not started" from "started but not answering yet", which
        ping alone cannot do.

        Returns:
            bool: True if a matching process was found.
        """
        if not _IS_WINDOWS and not _IS_MACOS and os.path.isdir("/proc"):
            return bool(self._proc_pids())

        name = os.path.basename(self.lightburn_path)
        try:
            if _IS_WINDOWS:
                result = subprocess.run(
                    ["tasklist", "/FI", f"IMAGENAME eq {name}", "/NH"],
                    capture_output=True,
                    text=True,
                    # tasklist writes in the console OEM codepage (cp850 on a
                    # German system, etc). Decoding that as the ANSI codepage
                    # raises UnicodeDecodeError inside subprocess's reader
                    # thread, which silently yields empty output — so this
                    # would always report False on non-English Windows.
                    encoding="oem",
                    errors="replace",
                    check=False,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                # tasklist exits 0 even when nothing matched, so check output.
                # The "no tasks" notice is localised; matching on the image
                # name avoids depending on the system language.
                return name.lower() in (result.stdout or "").lower()

            result = subprocess.run(
                ["pgrep", "-f", name],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            return result.returncode == 0
        except (FileNotFoundError, OSError, LookupError, ValueError):
            # pgrep lives in procps, present on stock systems but absent in
            # some slim containers. LookupError guards the "oem" codec, which
            # only exists on Windows.
            return False

    def _open_launch_log(self):
        """
        Open a file to capture the launched process's output.

        Sending stdout and stderr to DEVNULL makes a refusal to start
        indistinguishable from a slow start: the process is simply gone, with
        no missing-library or no-display message anywhere. Falls back to
        DEVNULL only if the log file itself cannot be opened.

        Returns:
            file object: An open file, or subprocess.DEVNULL as a fallback.
        """
        try:
            path = os.path.join(
                tempfile.gettempdir(),
                f"lightburn_launch_{os.getpid()}.log",
            )
            handle = open(path, "ab", buffering=0)
            handle.write(
                f"\n--- launch at {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n".encode()
            )
            self.launch_log_path = path
            return handle
        except OSError:
            self.launch_log_path = None
            return subprocess.DEVNULL

    def start_lightburn(self, extra_args=None):
        """
        Start the LightBurn application, detached from this process.

        Args:
            extra_args (list[str] or None): Additional command-line arguments,
                e.g. ["--prefsdir", "/home/me/.config/LightBurn"].

        Returns:
            bool: True if the process was launched, False otherwise.
        """
        log = self._open_launch_log()
        kwargs = {
            "stdin": subprocess.DEVNULL,
            "stdout": log,
            "stderr": subprocess.STDOUT,
            "cwd": os.path.dirname(self.lightburn_path) or None,
        }

        if _IS_WINDOWS:
            # start_new_session is ignored on Windows; creationflags is the
            # equivalent, so LightBurn survives this script exiting.
            kwargs["creationflags"] = getattr(
                subprocess, "DETACHED_PROCESS", 0
            ) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        else:
            kwargs["start_new_session"] = True

        cmd = [self.lightburn_path] + [str(a) for a in (extra_args or [])]

        try:
            subprocess.Popen(cmd, **kwargs)
            print(f"Started LightBurn from: {self.lightburn_path}")
            if self.launch_log_path:
                print(f"Launch output is being written to: {self.launch_log_path}")
            return True
        except OSError as exc:
            print(f"Error starting LightBurn: {exc}")
            if _IS_WINDOWS and getattr(exc, "winerror", None) == 216:
                print(
                    f"{self.lightburn_path} is not a valid Windows executable "
                    "(wrong architecture, or not a real .exe)."
                )
            if (
                not _IS_WINDOWS
                and not _IS_MACOS
                and not os.environ.get("DISPLAY")
                and not os.environ.get("WAYLAND_DISPLAY")
            ):
                print(
                    "No DISPLAY or WAYLAND_DISPLAY is set. LightBurn is a GUI "
                    "application and needs a graphical session."
                )
            return False
        finally:
            # The child holds its own duplicate of the descriptor; keeping the
            # parent's copy open would leak one per launch.
            if log is not subprocess.DEVNULL:
                try:
                    log.close()
                except OSError:
                    pass

    def ensure_lightburn_running(
        self, startup_timeout=30.0, poll_interval=1.0, extra_args=None
    ):
        """
        Ensure LightBurn is running and responding to PING, starting it if not.

        Args:
            startup_timeout (float): Max seconds to wait (default 30.0). A cold
                first launch on modest hardware regularly exceeds ten seconds,
                which is why that is no longer the default.
            poll_interval (float): Seconds between pings (default 1.0)
            extra_args (list[str] or None): Extra arguments for the launch.

        Returns:
            bool: True if LightBurn is responding.
        """
        if self.ping():
            print("LightBurn is already running and responding")
            return True

        was_already_running = self.is_process_running()
        if was_already_running:
            print(
                "A LightBurn process exists but is not answering on UDP. "
                "Waiting rather than launching a second copy..."
            )
        else:
            print("LightBurn not responding, attempting to start...")
            if not self.start_lightburn(extra_args=extra_args):
                return False

        deadline = time.monotonic() + startup_timeout
        while time.monotonic() < deadline:
            time.sleep(poll_interval)
            print("Sending PING to LightBurn...")
            if self.ping():
                print("LightBurn started successfully and is responding")
                return True

        print(f"Timeout: LightBurn did not respond within {startup_timeout} seconds")

        # A process that is alive but silent for the whole window is a
        # different failure from one that never started, and the usual cause is
        # a modal dialog — unsaved-changes, file recovery, licence or update
        # prompt — which blocks the UI thread that services UDP. Nothing here
        # can clear it, but saying so beats reporting a bare timeout.
        if self.is_process_running():
            print(
                "A LightBurn process is still running but never answered. It is "
                "most likely blocked on a modal dialog (unsaved changes, file "
                "recovery, licence or update prompt). Check the screen — the "
                "dialog may be hidden behind another window — or use "
                "load_file(..., force=True) to avoid the save prompt."
            )
        elif not was_already_running:
            print(
                "The launched process is no longer running. "
                + (
                    f"See {self.launch_log_path} for its output."
                    if self.launch_log_path
                    else "No launch log was captured."
                )
            )
        return False

    # ------------------------------------------------------------------ #

    def __str__(self):
        return (
            f"LightBurnUDPCommunication(path='{self.lightburn_path}', "
            f"ip='{self.udp_ip}', out_port={self.udp_out_port}, "
            f"in_port={self.udp_in_port}, bind_ip='{self.bind_ip}')"
        )

    __repr__ = __str__


# Run the CLI with:  python -m lightburn_udp --help