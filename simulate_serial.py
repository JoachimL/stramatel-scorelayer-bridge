#!/usr/bin/env python3
"""
Stream simulated Stramatel frames to a PTY so the bridge can read them
as if they came from real hardware.

Usage
-----
1.  Create a linked PTY pair with socat (run this in a separate terminal):

        socat -d -d pty,raw,echo=0 pty,raw,echo=0

    socat will print two paths like:
        /dev/pts/4  ← write-side  (pass to --pty below)
        /dev/pts/5  ← read-side   (pass as --port to the bridge)

2.  Start the bridge pointing at the read-side:

        python stramatel_scorelayer_bridge.py --port /dev/pts/5 --dry-run --verbose

3.  Start this script pointing at the write-side:

        python simulate_serial.py --pty /dev/pts/4

Options
-------
  --pty PATH        PTY device to write frames to (required)
  --interval SECS   Seconds between frames [default: 1.0]
  --start MM:SS     Starting clock value [default: 19:40]
  --count N         Stop after N frames (0 = run forever) [default: 0]
"""

import argparse
import os
import signal
import sys
import time

START = 0xF8
END = 0x0D
FRAME_LEN = 54

running = True


def _handle_signal(signum, frame):
    global running
    running = False


signal.signal(signal.SIGINT, _handle_signal)
signal.signal(signal.SIGTERM, _handle_signal)


def make_frame(minutes: int, seconds: int, code: int = 0x35) -> bytes:
    frame = bytearray(b" " * FRAME_LEN)
    frame[0] = START
    frame[1] = code
    frame[-1] = END
    frame[4:8] = f"{minutes:02d}{seconds:02d}".encode("ascii")
    frame[10:11] = b"0"
    frame[13:15] = b"01"
    frame[17:21] = b"0001"
    frame[22:23] = b"1"
    return bytes(frame)


def tick(minutes: int, seconds: int) -> tuple[int, int]:
    seconds -= 1
    if seconds < 0:
        seconds = 59
        minutes = max(0, minutes - 1)
    return minutes, seconds


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Stream simulated Stramatel frames to a PTY device.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--pty", required=True, metavar="PATH", help="PTY device to write to")
    parser.add_argument("--interval", type=float, default=1.0, metavar="SECS", help="Seconds between frames")
    parser.add_argument("--start", default="19:40", metavar="MM:SS", help="Starting clock value")
    parser.add_argument("--count", type=int, default=0, metavar="N", help="Stop after N frames (0 = forever)")
    args = parser.parse_args()

    try:
        m_str, s_str = args.start.split(":")
        minutes, seconds = int(m_str), int(s_str)
    except ValueError:
        sys.exit(f"--start must be MM:SS, got: {args.start!r}")

    if not (0 <= minutes <= 99 and 0 <= seconds <= 59):
        sys.exit(f"--start out of range: {args.start!r}")

    try:
        fd = os.open(args.pty, os.O_WRONLY | os.O_NOCTTY)
    except OSError as e:
        sys.exit(f"Cannot open {args.pty}: {e}")

    print(f"Writing to {args.pty}  interval={args.interval}s  start={minutes:02d}:{seconds:02d}", flush=True)

    sent = 0
    try:
        while running:
            frame = make_frame(minutes, seconds)
            try:
                os.write(fd, frame)
            except OSError as e:
                print(f"Write error: {e}", file=sys.stderr)
                break

            sent += 1
            print(f"  [{sent:>5}] {minutes:02d}:{seconds:02d}", flush=True)

            if args.count and sent >= args.count:
                break

            minutes, seconds = tick(minutes, seconds)
            time.sleep(args.interval)
    finally:
        os.close(fd)
        print(f"\nDone. Sent {sent} frame(s).")


if __name__ == "__main__":
    main()
