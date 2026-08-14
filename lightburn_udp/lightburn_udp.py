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
import time

# Single source of truth for the version. pyproject.toml reads this attribute,
# and __init__.py re-exports it, so a release is a one-line change here.
__version__ = "1.1.0"
__author__ = "Urs Helfenstein"
__all__ = ["LightBurnUDPCommunication", "find_lightburn"]

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


_GLOBS = {
    "linux": (
        "/opt/LightBurn*/LightBurn*",
        "~/Applications/LightBurn*.AppImage",
        "~/Applications/LightBurn*/LightBurn*",
        "~/.local/bin/LightBurn*.AppImage",
        "~/Downloads/LightBurn*.AppImage",
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

    key = "win32" if _IS_WINDOWS else ("darwin" if _IS_MACOS else "linux")
    for pattern in _GLOBS[key]:
        for candidate in sorted(glob.glob(_expand(pattern))):
            if _runnable(candidate):
                return candidate

    return None


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
        # On Windows SO_REUSEADDR allows two sockets to genuinely share a port,
        # which would let a second instance silently steal our replies. Use
        # SO_EXCLUSIVEADDRUSE there instead; POSIX needs SO_REUSEADDR to avoid
        # spurious EADDRINUSE.
        try:
            if _IS_WINDOWS:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            else:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        except (AttributeError, OSError):
            pass

        try:
            sock.bind((self.bind_ip, self.udp_in_port))
        except OSError as exc:
            sock.close()
            if exc.errno in (errno.EADDRINUSE, getattr(errno, "WSAEADDRINUSE", None)):
                raise OSError(
                    f"UDP port {self.udp_in_port} on {self.bind_ip} is already in "
                    f"use. Another script or LightBurn instance is likely holding "
                    f"it. Check with: {_port_check_hint(self.udp_in_port)}"
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
        """
        out_sock = None
        try:
            in_sock = self._get_in_socket()
            self._drain(in_sock)

            out_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            out_sock.sendto(message.encode("utf-8"), (self.udp_ip, self.udp_out_port))

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

    def is_process_running(self):
        """
        Check whether a LightBurn process exists, regardless of UDP state.

        Distinguishes "not started" from "started but not answering yet", which
        ping alone cannot do.

        Returns:
            bool: True if a matching process was found.
        """
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

    def start_lightburn(self):
        """
        Start the LightBurn application, detached from this process.

        Returns:
            bool: True if the process was launched, False otherwise.
        """
        kwargs = {
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
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

        try:
            subprocess.Popen([self.lightburn_path], **kwargs)
            print(f"Started LightBurn from: {self.lightburn_path}")
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

    def ensure_lightburn_running(self, startup_timeout=10.0, poll_interval=1.0):
        """
        Ensure LightBurn is running and responding to PING, starting it if not.

        Args:
            startup_timeout (float): Max seconds to wait (default 10.0). First
                launch is often slower than this on modest hardware; 30 or more
                is a safer value when autostarting.
            poll_interval (float): Seconds between pings (default 1.0)

        Returns:
            bool: True if LightBurn is responding.
        """
        if self.ping():
            print("LightBurn is already running and responding")
            return True

        if self.is_process_running():
            print(
                "A LightBurn process exists but is not answering on UDP. "
                "Waiting rather than launching a second copy..."
            )
        else:
            print("LightBurn not responding, attempting to start...")
            if not self.start_lightburn():
                return False

        deadline = time.monotonic() + startup_timeout
        while time.monotonic() < deadline:
            time.sleep(poll_interval)
            print("Sending PING to LightBurn...")
            if self.ping():
                print("LightBurn started successfully and is responding")
                return True

        print(f"Timeout: LightBurn did not respond within {startup_timeout} seconds")
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
