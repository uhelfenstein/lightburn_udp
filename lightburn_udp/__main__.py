"""Command-line interface: python -m lightburn_udp --help"""

import argparse
import sys

from .lightburn_udp import LightBurnUDPCommunication, find_lightburn, __version__


def build_parser():
    parser = argparse.ArgumentParser(
        prog="python -m lightburn_udp",
        description="Control LightBurn over UDP.",
    )
    parser.add_argument(
        "--version", action="version", version=f"lightburn-udp {__version__}"
    )
    parser.add_argument(
        "--path",
        default=None,
        help="Path to the LightBurn executable (auto-detected if omitted)",
    )
    parser.add_argument("--ip", default="127.0.0.1", help="LightBurn host")
    parser.add_argument("--out-port", type=int, default=19840)
    parser.add_argument("--in-port", type=int, default=19841)
    parser.add_argument("--bind-ip", default=None)
    parser.add_argument(
        "--timeout", type=float, default=1.0, help="Response timeout in seconds"
    )
    parser.add_argument(
        "--startup-timeout",
        type=float,
        default=30.0,
        help="Seconds to wait for LightBurn to come up (default 30)",
    )
    parser.add_argument(
        "--no-autostart",
        action="store_true",
        help="Fail instead of launching LightBurn if it is not running",
    )

    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("detect", help="Show the auto-detected LightBurn path")
    sub.add_parser("ping", help="Check whether LightBurn responds")
    sub.add_parser("status", help="Print Idle or Running")
    sub.add_parser("start", help="Start the loaded job")

    load = sub.add_parser("load", help="Load a file")
    load.add_argument("file")
    load.add_argument("--force", action="store_true", help="Use FORCELOAD")

    run = sub.add_parser("run", help="Load a file and immediately start it")
    run.add_argument("file")
    run.add_argument("--force", action="store_true")

    close = sub.add_parser("close", help="Close LightBurn")
    close.add_argument("--force", action="store_true", help="Use FORCECLOSE")

    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)

    if args.command == "detect":
        found = find_lightburn()
        if found:
            print(found)
            return 0
        print("LightBurn not found in any of the usual locations.", file=sys.stderr)
        return 1

    try:
        lb = LightBurnUDPCommunication(
            args.path,
            udp_ip=args.ip,
            udp_out_port=args.out_port,
            udp_in_port=args.in_port,
            bind_ip=args.bind_ip,
        )
    except (FileNotFoundError, PermissionError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    with lb:
        try:
            if args.no_autostart:
                if not lb.ping(args.timeout):
                    print("LightBurn is not responding.", file=sys.stderr)
                    return 1
            elif not lb.ensure_lightburn_running(args.startup_timeout):
                print("Could not reach LightBurn.", file=sys.stderr)
                return 1

            if args.command == "ping":
                print("OK")
                return 0

            if args.command == "status":
                status = lb.get_status(args.timeout)
                if status is False:
                    print("No response.", file=sys.stderr)
                    return 1
                print(status)
                return 0

            if args.command in ("load", "run"):
                if lb.load_file(args.file, force=args.force, timeout=args.timeout) is False:
                    print("Failed to load file.", file=sys.stderr)
                    return 1
                print(f"Loaded: {args.file}")
                if args.command == "load":
                    return 0

            if args.command in ("start", "run"):
                if lb.start_job(args.timeout) is False:
                    print("Failed to start job.", file=sys.stderr)
                    return 1
                print("Job started.")
                return 0

            if args.command == "close":
                lb.close_lightburn(force=args.force, timeout=args.timeout)
                print("Close command sent.")
                return 0

        except OSError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
