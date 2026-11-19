#!/usr/bin/env python3
"""Serve one bounded download for an IPv4 egress test on a Linux VPS."""

import argparse
from http.server import BaseHTTPRequestHandler, HTTPServer
from ipaddress import IPv4Address
import re
import secrets
import signal
import sys

MIB = 1024 * 1024


class DeadlineReached(BaseException):
    # The deadline must escape HTTPServer's per-connection Exception handler.
    pass


class QuietHTTPServer(HTTPServer):
    def handle_error(self, request, client_address):
        # Don't put client addresses in the report or log aborted requests.
        pass


def stop_at_deadline(_signum, _frame):
    raise DeadlineReached


def hostname(value):
    label = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    if len(value) > 253 or not all(re.fullmatch(label, x) for x in value.split(".")):
        raise argparse.ArgumentTypeError("use a hostname or IPv4 address without a scheme or port")
    return value


def port_number(value):
    port = int(value)
    if port != 0 and not 1024 <= port <= 65535:
        raise argparse.ArgumentTypeError("use port 1024–65535, or 0 for a random port")
    return port


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("host", type=hostname, help="public hostname for the printed test URL")
    parser.add_argument("--bind", default="0.0.0.0", type=IPv4Address, help="local IPv4 address")
    parser.add_argument("--port", default=18080, type=port_number)
    parser.add_argument("--size-mib", default=64, type=int, metavar="1..128")
    parser.add_argument("--timeout", default=180, type=int, metavar="SECONDS")
    args = parser.parse_args()
    if not 1 <= args.size_mib <= 128:
        parser.error("--size-mib must be between 1 and 128")
    if not 1 <= args.timeout <= 300:
        parser.error("--timeout must be between 1 and 300 seconds")
    return args


def run(args):
    path = "/egress-" + secrets.token_hex(16)
    size = args.size_mib * MIB
    block = secrets.token_bytes(64 * 1024)
    result = "waiting"

    class Handler(BaseHTTPRequestHandler):
        timeout = 10  # socket inactivity, in addition to the whole-test deadline

        def log_message(self, _format, *args):
            pass

        def do_GET(self):
            nonlocal result
            if self.path != path:
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return

            # Consume the token even if this client disconnects midway.
            result = "client_disconnected_or_stalled"
            try:
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Cache-Control", "no-store, no-transform")
                self.send_header("Content-Length", str(size))
                self.end_headers()
                for _ in range(size // len(block)):
                    self.wfile.write(block)
            except OSError:
                return
            result = "sent"

    try:
        with QuietHTTPServer((str(args.bind), args.port), Handler) as server:
            signal.signal(signal.SIGALRM, stop_at_deadline)
            signal.alarm(args.timeout)
            print(f"URL=http://{args.host}:{server.server_port}{path}", flush=True)
            print(f"limit_mib={args.size_mib} timeout_seconds={args.timeout}", flush=True)
            while result == "waiting":
                server.handle_request()
    except DeadlineReached:
        result = "timeout"
    except KeyboardInterrupt:
        result = "cancelled"
    except OSError as error:
        print(f"result=server_error errno={error.errno}: {error.strerror}", file=sys.stderr)
        return 1
    finally:
        signal.alarm(0)

    if result == "sent":
        print(f"result=sent bytes={size}")
        return 0
    print(f"result={result}")
    return 1


if __name__ == "__main__":
    sys.exit(run(parse_args()))
