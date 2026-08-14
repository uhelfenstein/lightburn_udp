"""
Test suite for lightburn_udp.

Runs against a mock LightBurn that speaks the same UDP protocol, so it needs
neither a laser nor a LightBurn install. Platform-specific branches that cannot
execute natively are exercised by patching the platform flags.

Run either way:

    python test_lightburn_udp.py
    pytest test_lightburn_udp.py
"""

import os
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import lightburn_udp.lightburn_udp as mod  # noqa: E402
from lightburn_udp import LightBurnUDPCommunication, find_lightburn  # noqa: E402

OUT_PORT, IN_PORT = 29840, 29841


def _mock_lightburn(stop, received):
    """Listen on OUT_PORT, reply to IN_PORT, exactly as LightBurn does."""
    srv = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", OUT_PORT))
    srv.settimeout(0.2)
    reply = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    while not stop.is_set():
        try:
            data, _ = srv.recvfrom(65535)
        except socket.timeout:
            continue
        received.append(data.decode())
        reply.sendto(b"OK", ("127.0.0.1", IN_PORT))
    srv.close()
    reply.close()


def run_all():
    """Run every check. Returns True if all passed."""
    results = []
    received = []
    stop = threading.Event()
    launched = []

    def check(label, condition):
        print(f"{'PASS' if condition else 'FAIL'}  {label}")
        results.append(bool(condition))
        return condition

    threading.Thread(target=_mock_lightburn, args=(stop, received), daemon=True).start()
    time.sleep(0.3)

    tmp = tempfile.mkdtemp()
    # Unique per run: a stale process from an earlier run would otherwise be
    # matched by is_process_running() and break the negative check below.
    stem = f"LightBurnMock{os.getpid()}"
    placeholder = os.path.join(tmp, f"{stem}.exe" if os.name == "nt" else stem)
    with open(placeholder, "w") as fh:
        fh.write("mock\n")
    if os.name != "nt":
        os.chmod(placeholder, os.stat(placeholder).st_mode | stat.S_IXUSR)

    job = os.path.join(tmp, "job.lbrn2")
    with open(job, "w") as fh:
        fh.write("<xml/>")

    print(f"=== running on {sys.platform} (os.name={os.name}) ===")

    try:
        print("\n--- validation ---")
        try:
            LightBurnUDPCommunication(os.path.join(tmp, "nope", "LightBurn"))
            check("missing exe raises", False)
        except FileNotFoundError:
            check("missing exe raises FileNotFoundError", True)

        if os.name != "nt":
            noexec = os.path.join(tmp, "NotExec")
            with open(noexec, "w") as fh:
                fh.write("x")
            os.chmod(noexec, 0o644)
            try:
                LightBurnUDPCommunication(noexec)
                check("non-executable raises (POSIX)", False)
            except PermissionError:
                check("non-executable raises PermissionError (POSIX)", True)

        print("\n--- protocol ---")
        with LightBurnUDPCommunication(
            placeholder, udp_out_port=OUT_PORT, udp_in_port=IN_PORT
        ) as lb:
            check("ping", lb.ping(timeout=2.0) is True)
            check("get_status -> Idle", lb.get_status(timeout=2.0) == "Idle")
            check("load_file", lb.load_file(job, timeout=2.0) == "OK")
            check(
                "absolute path sent",
                any(
                    m.startswith("LOADFILE:") and os.path.isabs(m.split(":", 1)[1])
                    for m in received
                ),
            )
            check(
                "force uses FORCELOAD",
                lb.load_file(job, force=True, timeout=2.0) == "OK"
                and any(m.startswith("FORCELOAD:") for m in received),
            )
            check("start_job", lb.start_job(timeout=2.0) == "OK")
            check(
                "close_lightburn sends CLOSE",
                lb.close_lightburn(timeout=2.0) == "OK" and "CLOSE" in received,
            )
            check("tilde expansion", not any("~" in m for m in received))
            check(
                "20 rapid sequential commands",
                all(lb.ping(timeout=2.0) for _ in range(20)),
            )
            check("repr is sane", f"in_port={IN_PORT}" in repr(lb))

        print("\n--- socket lifecycle ---")
        lb2 = LightBurnUDPCommunication(
            placeholder, udp_out_port=OUT_PORT, udp_in_port=IN_PORT
        )
        lb2.ping(timeout=2.0)
        lb2.close()
        lb3 = LightBurnUDPCommunication(
            placeholder, udp_out_port=OUT_PORT, udp_in_port=IN_PORT
        )
        check("port reusable after close()", lb3.ping(timeout=2.0) is True)
        lb3.close()

        print("\n--- timeout path ---")
        lb4 = LightBurnUDPCommunication(
            placeholder, udp_out_port=39999, udp_in_port=39998
        )
        t0 = time.monotonic()
        check("ping returns False on timeout", lb4.ping(timeout=0.5) is False)
        elapsed = time.monotonic() - t0
        check(f"timeout respected ({elapsed:.2f}s)", 0.4 < elapsed < 1.5)
        lb4.close()

        print("\n--- bind address selection ---")
        check(
            "loopback target binds loopback",
            LightBurnUDPCommunication(
                placeholder, udp_ip="127.0.0.1", udp_in_port=39997
            ).bind_ip == "127.0.0.1",
        )
        check(
            "remote target binds 0.0.0.0",
            LightBurnUDPCommunication(
                placeholder, udp_ip="192.168.1.50", udp_in_port=39996
            ).bind_ip == "0.0.0.0",
        )

        print("\n--- process helpers (native) ---")
        # Negative case: nothing on this machine is named like the placeholder.
        lb5 = LightBurnUDPCommunication(
            placeholder, udp_out_port=OUT_PORT, udp_in_port=IN_PORT
        )
        check(
            "is_process_running() false for an unused name",
            lb5.is_process_running() is False,
        )
        check("make_executable()", lb5.make_executable() is True)
        lb5.close()

        # Positive case: the running interpreter is both a live process and a
        # genuinely launchable executable on every platform, so this avoids
        # fabricating a binary the OS will actually agree to run.
        lb6 = LightBurnUDPCommunication(sys.executable, udp_in_port=39995)
        check(
            "is_process_running() true for a live process",
            lb6.is_process_running() is True,
        )
        check(
            "start_lightburn() launches a real executable",
            lb6.start_lightburn() is True,
        )
        lb6.close()

        print("\n--- simulated Windows branch ---")
        real_popen = subprocess.Popen
        real_run = subprocess.run
        captured = {}
        run_kwargs = {}

        def spy_popen(args, **kwargs):
            captured.update(kwargs)
            captured["args"] = args
            proc = real_popen(
                [sys.executable, "-c", "import time; time.sleep(30)"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            launched.append(proc)
            return proc

        def spy_run(args, **kwargs):
            run_kwargs.update(kwargs)
            run_kwargs["args"] = args
            raise FileNotFoundError("tasklist is not present on this platform")

        mod._IS_WINDOWS = True
        subprocess.Popen = spy_popen
        subprocess.run = spy_run
        for flag, val in (
            ("DETACHED_PROCESS", 0x8),
            ("CREATE_NEW_PROCESS_GROUP", 0x200),
        ):
            if not hasattr(subprocess, flag):
                setattr(subprocess, flag, val)
        try:
            lbw = LightBurnUDPCommunication(
                placeholder, udp_out_port=OUT_PORT, udp_in_port=39994
            )
            lbw.start_lightburn()
            check("windows uses creationflags", captured.get("creationflags") == 0x208)
            check("windows omits start_new_session", "start_new_session" not in captured)
            check("windows make_executable is a no-op", lbw.make_executable() is True)

            # Regression guard: without an explicit console codepage, tasklist
            # output crashes subprocess's reader thread on localised Windows.
            lbw.is_process_running()
            check("windows tasklist decodes as oem", run_kwargs.get("encoding") == "oem")
            check(
                "windows tasklist tolerates bad bytes",
                run_kwargs.get("errors") == "replace",
            )
            check(
                "windows tasklist uses IMAGENAME filter",
                "/FI" in (run_kwargs.get("args") or []),
            )
            check(
                "windows port hint mentions netstat",
                "netstat" in mod._port_check_hint(19841),
            )
            lbw.close()
        finally:
            mod._IS_WINDOWS = False
            subprocess.Popen = real_popen
            subprocess.run = real_run

        print("\n--- POSIX launch flags ---")
        captured.clear()
        subprocess.Popen = spy_popen
        try:
            lbp = LightBurnUDPCommunication(
                placeholder, udp_out_port=OUT_PORT, udp_in_port=39993
            )
            lbp.start_lightburn()
            check(
                "posix uses start_new_session",
                captured.get("start_new_session") is True,
            )
            check("posix omits creationflags", "creationflags" not in captured)
            check("posix stdio detached", captured.get("stdout") == subprocess.DEVNULL)
            lbp.close()
        finally:
            subprocess.Popen = real_popen

        print("\n--- simulated macOS .app bundle ---")
        bundle = os.path.join(tmp, "LightBurn.app", "Contents", "MacOS")
        os.makedirs(bundle, exist_ok=True)
        inner = os.path.join(bundle, "LightBurn")
        with open(inner, "w") as fh:
            fh.write("mock\n")
        if os.name != "nt":
            os.chmod(inner, os.stat(inner).st_mode | stat.S_IXUSR)
        lbm = LightBurnUDPCommunication(
            os.path.join(tmp, "LightBurn.app"), udp_in_port=39992
        )
        check(".app bundle resolves to inner binary", lbm.lightburn_path == inner)
        lbm.close()

        mod._IS_MACOS = True
        try:
            check("macos port hint mentions lsof", "lsof" in mod._port_check_hint(19841))
        finally:
            mod._IS_MACOS = False
        check("linux port hint mentions ss", "ss " in mod._port_check_hint(19841))

        print("\n--- auto-detect ---")
        found = find_lightburn()
        check(
            "find_lightburn() returns None or an absolute path",
            found is None or os.path.isabs(found),
        )
        check(
            "windows candidate list is non-empty",
            len(list(mod._windows_candidates())) > 0,
        )
        check("macos candidate list is non-empty", len(list(mod._macos_candidates())) > 0)
        check("linux candidate list is non-empty", len(list(mod._linux_candidates())) > 0)

    finally:
        stop.set()
        time.sleep(0.3)
        for proc in launched:
            try:
                proc.kill()
            except OSError:
                pass

    print(f"\n{sum(results)}/{len(results)} passed")
    return all(results)


def test_lightburn_udp_suite():
    """pytest entry point."""
    assert run_all()


if __name__ == "__main__":
    sys.exit(0 if run_all() else 1)
