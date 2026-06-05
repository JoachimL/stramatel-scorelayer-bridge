#!/usr/bin/env python3

import argparse
import json
import os
import signal
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import requests
import serial
from serial.serialutil import SerialException


START = 0xF8
END = 0x0D
FRAME_LEN = 54

DEFAULT_BAUDRATE = 19200

running = True


def shutdown_handler(signum, frame):
    global running
    running = False


signal.signal(signal.SIGINT, shutdown_handler)
signal.signal(signal.SIGTERM, shutdown_handler)


def local_ts() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def utc_ts() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def log(message: str):
    print(f"[{local_ts()}] {message}", flush=True)


def printable_ascii(frame: bytes) -> str:
    return "".join(chr(b) if 32 <= b <= 126 else "." for b in frame)


def decode_cp437(frame: bytes) -> str:
    return frame.decode("cp437", errors="replace")


def frame_tokens(frame: bytes) -> list[str]:
    text = decode_cp437(frame)
    return text.strip("\r").split()


def byte_to_digit(b: int) -> Optional[int]:
    if 0x30 <= b <= 0x39:
        return b - 0x30
    return None


@dataclass
class ClockParseResult:
    raw: str
    display: str
    current_time_ms: Optional[int]
    mode: str


@dataclass
class ScoreboardState:
    valid: bool
    captured_at: str
    captured_at_utc: str
    code_hex: str
    code_ascii: str
    clock_raw: str
    clock_display: str
    current_time_ms: Optional[int]
    clock_mode: str
    tokens: list[str]
    raw_ascii: str
    raw_cp437: str
    raw_hex: str


class FrameReader:
    def __init__(self):
        self.buffer = bytearray()
        self.frames_seen = 0
        self.bytes_seen = 0
        self.misaligned_frames = 0

    def feed(self, chunk: bytes):
        self.bytes_seen += len(chunk)
        self.buffer.extend(chunk)

        if len(self.buffer) > FRAME_LEN * 8:
            log(f"Buffer cap exceeded ({len(self.buffer)} bytes), discarding")
            self.buffer.clear()
            return

        while True:
            start = self.buffer.find(bytes([START]))

            if start == -1:
                if len(self.buffer) > 0:
                    log(f"Discarding {len(self.buffer)} bytes before any START byte")
                self.buffer.clear()
                return

            if start > 0:
                log(f"Discarding {start} bytes before START byte")
                del self.buffer[:start]

            if len(self.buffer) < FRAME_LEN:
                return

            candidate = bytes(self.buffer[:FRAME_LEN])

            if candidate[-1] != END:
                self.misaligned_frames += 1
                next_start = self.buffer.find(bytes([START]), 1)
                if next_start == -1:
                    log(
                        "Misaligned candidate frame: "
                        f"expected END=0x{END:02X} at byte {FRAME_LEN - 1}, "
                        f"got 0x{candidate[-1]:02X}. No next START found, discarding buffer."
                    )
                    self.buffer.clear()
                    return
                log(
                    "Misaligned candidate frame: "
                    f"expected END=0x{END:02X} at byte {FRAME_LEN - 1}, "
                    f"got 0x{candidate[-1]:02X}. Seeking next START at +{next_start}."
                )
                del self.buffer[:next_start]
                continue

            del self.buffer[:FRAME_LEN]
            self.frames_seen += 1
            yield candidate


def parse_clock(d1: int, d2: int, d3: int, d4: int) -> ClockParseResult:
    raw_bytes = bytes([d1, d2, d3, d4])
    raw = raw_bytes.decode("ascii", errors="replace")
    digits = [byte_to_digit(b) for b in raw_bytes]

    # Normal mode: MMSS, e.g. "1941" => 19:41
    if all(d is not None for d in digits):
        minutes = digits[0] * 10 + digits[1]
        seconds = digits[2] * 10 + digits[3]

        if seconds <= 59:
            total_ms = ((minutes * 60) + seconds) * 1000
            return ClockParseResult(
                raw=raw,
                display=f"{minutes:02d}:{seconds:02d}",
                current_time_ms=total_ms,
                mode="MMSS",
            )

        return ClockParseResult(
            raw=raw,
            display=raw,
            current_time_ms=None,
            mode="MMSS_INVALID_SECONDS",
        )

    # Sub-10-minute mode: space + M + SS, e.g. " 839" => 8:39
    if d1 == 0x20:
        m = byte_to_digit(d2)
        s_tens = byte_to_digit(d3)
        s_ones = byte_to_digit(d4)
        if m is not None and s_tens is not None and s_ones is not None:
            seconds = s_tens * 10 + s_ones
            if seconds <= 59:
                total_ms = ((m * 60) + seconds) * 1000
                return ClockParseResult(
                    raw=raw,
                    display=f"0{m}:{seconds:02d}",
                    current_time_ms=total_ms,
                    mode="MMSS",
                )

    # Under-one-minute mode from known Stramatel parser:
    # S S t space, e.g. "560 " => 56.0 seconds
    if d4 == 0x20:
        a = byte_to_digit(d1)
        b = byte_to_digit(d2)
        tenths = byte_to_digit(d3)

        if a is not None and b is not None and tenths is not None:
            seconds = a * 10 + b
            if seconds <= 59:
                total_ms = (seconds * 1000) + (tenths * 100)
                return ClockParseResult(
                    raw=raw,
                    display=f"00:{seconds:02d}.{tenths}",
                    current_time_ms=total_ms,
                    mode="SS_TENTHS",
                )

    return ClockParseResult(
        raw=raw,
        display=raw.strip(),
        current_time_ms=None,
        mode="UNKNOWN",
    )


def parse_frame(frame: bytes) -> ScoreboardState:
    captured_at = local_ts()
    captured_at_utc = utc_ts()

    raw_ascii = printable_ascii(frame)
    raw_cp437 = decode_cp437(frame)
    raw_hex = frame.hex(" ")
    tokens = frame_tokens(frame)

    valid = len(frame) == FRAME_LEN and frame[0] == START and frame[-1] == END

    if not valid:
        return ScoreboardState(
            valid=False,
            captured_at=captured_at,
            captured_at_utc=captured_at_utc,
            code_hex="",
            code_ascii="",
            clock_raw="",
            clock_display="",
            current_time_ms=None,
            clock_mode="INVALID_FRAME",
            tokens=tokens,
            raw_ascii=raw_ascii,
            raw_cp437=raw_cp437,
            raw_hex=raw_hex,
        )

    code = frame[1]
    clock = parse_clock(frame[4], frame[5], frame[6], frame[7])

    return ScoreboardState(
        valid=True,
        captured_at=captured_at,
        captured_at_utc=captured_at_utc,
        code_hex=f"0x{code:02X}",
        code_ascii=chr(code) if 32 <= code <= 126 else "",
        clock_raw=clock.raw,
        clock_display=clock.display,
        current_time_ms=clock.current_time_ms,
        clock_mode=clock.mode,
        tokens=tokens,
        raw_ascii=raw_ascii,
        raw_cp437=raw_cp437,
        raw_hex=raw_hex,
    )


def state_key(state: ScoreboardState):
    return (
        state.valid,
        state.code_hex,
        state.clock_raw,
        state.clock_display,
        state.current_time_ms,
        state.clock_mode,
    )


class ScorelayerClient:
    def __init__(
        self,
        api_url: Optional[str],
        api_token: Optional[str],
        timeout_seconds: float,
        dry_run: bool,
    ):
        self.api_url = api_url
        self.api_token = api_token
        self.timeout_seconds = timeout_seconds
        self.dry_run = dry_run
        self.success_count = 0
        self.error_count = 0

    def build_payload(self, state: ScoreboardState) -> dict:
        # Running is currently assumed true.
        # Later, once we identify the Stramatel running/stopped byte,
        # replace this with parsed running state.
        return {
            "clock": {
                "running": True,
                #"currentTimeMs": state.current_time_ms,
                "remainingMs": state.current_time_ms,
            }
        }

    def push(self, state: ScoreboardState) -> bool:
        if state.current_time_ms is None:
            log(
                "Skipping API push: current_time_ms is None "
                f"(clock_raw={state.clock_raw!r}, mode={state.clock_mode})"
            )
            return False

        payload = self.build_payload(state)

        if self.dry_run:
            log("[DRY RUN] Would PATCH Scorelayer:")
            print(json.dumps(payload, indent=2), flush=True)
            return True

        if not self.api_url:
            log("Skipping API push: SCORELAYER_API_URL is not set")
            return False

        if not self.api_token:
            log("Skipping API push: SCORELAYER_API_TOKEN is not set")
            return False

        headers = {
            "Authorization": f"Bearer {self.api_token}",
            "Content-Type": "application/json",
            "User-Agent": "stramatel-scorelayer-bridge/0.1",
        }

        try:
            response = requests.patch(
                self.api_url,
                headers=headers,
                json=payload,
                timeout=self.timeout_seconds,
            )

            if 200 <= response.status_code < 300:
                self.success_count += 1
                log(
                    "PATCH success "
                    f"status={response.status_code} "
                    f"success_count={self.success_count}"
                )
                return True

            self.error_count += 1
            log(
                "PATCH failed "
                f"status={response.status_code} "
                f"error_count={self.error_count} "
                f"body={response.text[:500]!r}"
            )
            return False

        except requests.RequestException as e:
            self.error_count += 1
            log(f"PATCH exception error_count={self.error_count} error={e}")
            return False


def log_state(state: ScoreboardState, verbose: bool):
    log(
        "Parsed frame: "
        f"valid={state.valid} "
        f"code={state.code_hex}/{state.code_ascii!r} "
        f"clock_raw={state.clock_raw!r} "
        f"clock={state.clock_display!r} "
        f"currentTimeMs={state.current_time_ms} "
        f"mode={state.clock_mode} "
        f"tokens={state.tokens}"
    )

    if verbose:
        log(f"raw_ascii={state.raw_ascii!r}")
        log(f"raw_cp437={state.raw_cp437!r}")
        log(f"raw_hex={state.raw_hex}")


def make_simulated_frame(
    minutes: int,
    seconds: int,
    code: int = 0x35,
) -> bytes:
    if not (0 <= minutes <= 99 and 0 <= seconds <= 59):
        raise ValueError(f"Invalid clock value: {minutes:02d}:{seconds:02d}")
    frame = bytearray(b" " * FRAME_LEN)
    frame[0] = START
    frame[1] = code
    frame[-1] = END

    frame[4:8] = f"{minutes:02d}{seconds:02d}".encode("ascii")

    # Fill some token-like values similar to observed output:
    # °5  1941  0  01  0001 1
    frame[10:11] = b"0"
    frame[13:15] = b"01"
    frame[17:21] = b"0001"
    frame[22:23] = b"1"

    return bytes(frame)


def simulate_loop(
    client: ScorelayerClient,
    interval_seconds: float,
    heartbeat_seconds: float,
    verbose: bool,
):
    log("Starting SIMULATE mode")
    log("No serial port will be read")
    log(f"Simulation interval: {interval_seconds}s")
    log(f"Heartbeat: {heartbeat_seconds}s")

    last_key = None
    last_push_at = 0.0

    minutes = 19
    seconds = 40

    while running:
        frame = make_simulated_frame(minutes, seconds)
        state = parse_frame(frame)

        key = state_key(state)
        now_monotonic = time.monotonic()

        changed = key != last_key
        heartbeat_due = now_monotonic - last_push_at >= heartbeat_seconds

        if changed or heartbeat_due:
            reason = "changed" if changed else "heartbeat"
            log(f"Emitting simulated state because {reason}")
            log_state(state, verbose=verbose)
            pushed = client.push(state)

            last_key = key
            if pushed:
                last_push_at = now_monotonic

        seconds += 1
        if seconds >= 60:
            seconds = 0
            minutes = (minutes + 1) % 100

        time.sleep(interval_seconds)

    log("Simulation stopped")


def serial_loop(
    port: str,
    baudrate: int,
    client: ScorelayerClient,
    reconnect_delay_seconds: float,
    heartbeat_seconds: float,
    serial_timeout_seconds: float,
    verbose: bool,
):
    log("Starting SERIAL mode")
    log(f"Port: {port}")
    log(f"Baudrate: {baudrate}")
    log(f"Frame length: {FRAME_LEN}")
    log(f"START byte: 0x{START:02X}")
    log(f"END byte: 0x{END:02X}")
    log(f"Heartbeat: {heartbeat_seconds}s")

    ser = None
    reader = FrameReader()

    last_key = None
    last_push_at = 0.0
    last_stats_at = time.monotonic()

    while running:
        try:
            if ser is None or not ser.is_open:
                log(f"Opening serial port {port}")

                ser = serial.Serial(
                    port=port,
                    baudrate=baudrate,
                    timeout=serial_timeout_seconds,
                    bytesize=serial.EIGHTBITS,
                    parity=serial.PARITY_NONE,
                    stopbits=serial.STOPBITS_ONE,
                    xonxoff=False,
                    rtscts=False,
                    dsrdtr=False,
                )

                ser.dtr = False
                ser.rts = False

                log("Serial connected")

            chunk = ser.read(256)

            if not chunk:
                now_monotonic = time.monotonic()
                if now_monotonic - last_stats_at >= 10:
                    log(
                        "Serial waiting: "
                        f"bytes_seen={reader.bytes_seen} "
                        f"frames_seen={reader.frames_seen} "
                        f"buffer_len={len(reader.buffer)} "
                        f"misaligned={reader.misaligned_frames}"
                    )
                    last_stats_at = now_monotonic
                continue

            if verbose:
                log(f"Read {len(chunk)} bytes: {chunk.hex(' ')}")

            for raw_frame in reader.feed(chunk):
                state = parse_frame(raw_frame)

                key = state_key(state)
                now_monotonic = time.monotonic()

                changed = key != last_key
                heartbeat_due = now_monotonic - last_push_at >= heartbeat_seconds

                if changed or heartbeat_due:
                    reason = "changed" if changed else "heartbeat"
                    log(f"Emitting state because {reason}")
                    log_state(state, verbose=verbose)
                    pushed = client.push(state)

                    last_key = key
                    if pushed:
                        last_push_at = now_monotonic
                elif verbose:
                    log(
                        "Frame parsed but skipped because unchanged "
                        f"clock={state.clock_display} currentTimeMs={state.current_time_ms}"
                    )

        except (SerialException, OSError) as e:
            log(f"Serial error: {e}")

            if ser:
                try:
                    ser.close()
                except Exception:
                    pass

            ser = None
            reader = FrameReader()
            log(f"Reconnecting in {reconnect_delay_seconds}s")
            time.sleep(reconnect_delay_seconds)

    if ser and ser.is_open:
        ser.close()

    log("Serial loop stopped")


def main():
    parser = argparse.ArgumentParser(
        description="Bridge Stramatel scoreboard serial output to Scorelayer."
    )

    parser.add_argument(
        "--port",
        default="/dev/ttyUSB0",
        help="Serial port, preferably /dev/serial/by-id/...",
    )

    parser.add_argument(
        "--baudrate",
        type=int,
        default=DEFAULT_BAUDRATE,
        help="Serial baudrate",
    )

    parser.add_argument(
        "--api-url",
        default=os.getenv("SCORELAYER_API_URL"),
        help="Scorelayer PATCH endpoint. Can also be set with SCORELAYER_API_URL.",
    )

    parser.add_argument(
        "--api-token",
        default=os.getenv("SCORELAYER_API_TOKEN"),
        help="Bearer token. Can also be set with SCORELAYER_API_TOKEN.",
    )

    parser.add_argument(
        "--api-timeout",
        type=float,
        default=2.0,
        help="API timeout in seconds",
    )

    parser.add_argument(
        "--heartbeat-seconds",
        type=float,
        default=15.0,
        help="Push unchanged state at this interval",
    )

    parser.add_argument(
        "--reconnect-delay",
        type=float,
        default=3.0,
        help="Delay before reconnecting serial port after error",
    )

    parser.add_argument(
        "--serial-timeout",
        type=float,
        default=1.0,
        help="Serial read timeout in seconds",
    )

    parser.add_argument(
        "--simulate",
        action="store_true",
        help="Run without serial input using simulated Stramatel frames",
    )

    parser.add_argument(
        "--simulate-interval",
        type=float,
        default=1.0,
        help="Seconds between simulated clock updates",
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print PATCH payload instead of sending to Scorelayer",
    )

    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print raw bytes, raw frame data and skipped frames",
    )

    args = parser.parse_args()

    log("Stramatel Scorelayer bridge starting")
    log(f"API URL set: {bool(args.api_url)}")
    log(f"API token set: {bool(args.api_token)}")
    log(f"Dry run: {args.dry_run}")
    log(f"Verbose: {args.verbose}")

    client = ScorelayerClient(
        api_url=args.api_url,
        api_token=args.api_token,
        timeout_seconds=args.api_timeout,
        dry_run=args.dry_run,
    )

    if args.simulate:
        simulate_loop(
            client=client,
            interval_seconds=args.simulate_interval,
            heartbeat_seconds=args.heartbeat_seconds,
            verbose=args.verbose,
        )
    else:
        serial_loop(
            port=args.port,
            baudrate=args.baudrate,
            client=client,
            reconnect_delay_seconds=args.reconnect_delay,
            heartbeat_seconds=args.heartbeat_seconds,
            serial_timeout_seconds=args.serial_timeout,
            verbose=args.verbose,
        )

    log("Stramatel Scorelayer bridge stopped")


if __name__ == "__main__":
    main()