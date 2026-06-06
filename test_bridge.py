import pytest
from stramatel_scorelayer_bridge import (
    byte_to_digit,
    parse_clock,
    parse_frame,
    FrameReader,
    START,
    END,
    FRAME_LEN,
)


# ---------------------------------------------------------------------------
# byte_to_digit
# ---------------------------------------------------------------------------

def test_byte_to_digit_ascii_digits():
    for i, ch in enumerate("0123456789"):
        assert byte_to_digit(ord(ch)) == i


def test_byte_to_digit_non_digit():
    assert byte_to_digit(0x20) is None  # space
    assert byte_to_digit(0x2F) is None  # '/' — just below '0'
    assert byte_to_digit(0x3A) is None  # ':' — just above '9'


# ---------------------------------------------------------------------------
# parse_clock — MMSS (≥ 10 minutes)
# ---------------------------------------------------------------------------

def test_parse_clock_mmss_normal():
    r = parse_clock(0x31, 0x39, 0x34, 0x31)  # "1941"
    assert r.mode == "MMSS"
    assert r.display == "19:41"
    assert r.current_time_ms == (19 * 60 + 41) * 1000


def test_parse_clock_mmss_zero():
    r = parse_clock(0x30, 0x30, 0x30, 0x30)  # "0000"
    assert r.mode == "MMSS"
    assert r.current_time_ms == 0


def test_parse_clock_mmss_max_valid_seconds():
    r = parse_clock(0x30, 0x30, 0x35, 0x39)  # "0059"
    assert r.mode == "MMSS"
    assert r.current_time_ms == 59_000


def test_parse_clock_mmss_invalid_seconds():
    r = parse_clock(0x30, 0x30, 0x36, 0x30)  # "0060"
    assert r.mode == "MMSS_INVALID_SECONDS"
    assert r.current_time_ms is None


# ---------------------------------------------------------------------------
# parse_clock — sub-10-minute (leading space): the bug that was fixed
# ---------------------------------------------------------------------------

def test_parse_clock_sub10_minute_839():
    """Regression test for the original bug: ' 839' was classified UNKNOWN."""
    r = parse_clock(0x20, 0x38, 0x33, 0x39)  # " 839"
    assert r.mode == "MMSS"
    assert r.display == "08:39"
    assert r.current_time_ms == (8 * 60 + 39) * 1000


def test_parse_clock_sub10_minute_zero():
    r = parse_clock(0x20, 0x30, 0x30, 0x30)  # " 000"
    assert r.mode == "MMSS"
    assert r.display == "00:00"
    assert r.current_time_ms == 0


def test_parse_clock_sub10_minute_max():
    r = parse_clock(0x20, 0x39, 0x35, 0x39)  # " 959"
    assert r.mode == "MMSS"
    assert r.display == "09:59"
    assert r.current_time_ms == (9 * 60 + 59) * 1000


def test_parse_clock_sub10_minute_invalid_seconds():
    r = parse_clock(0x20, 0x39, 0x36, 0x30)  # " 960"
    assert r.current_time_ms is None


# ---------------------------------------------------------------------------
# parse_clock — SS_TENTHS (trailing space, under one minute)
# ---------------------------------------------------------------------------

def test_parse_clock_ss_tenths_normal():
    r = parse_clock(0x35, 0x36, 0x30, 0x20)  # "560 "
    assert r.mode == "SS_TENTHS"
    assert r.display == "00:56.0"
    assert r.current_time_ms == 56_000


def test_parse_clock_ss_tenths_zero():
    r = parse_clock(0x30, 0x30, 0x30, 0x20)  # "000 "
    assert r.mode == "SS_TENTHS"
    assert r.display == "00:00.0"
    assert r.current_time_ms == 0


def test_parse_clock_ss_tenths_with_tenths():
    r = parse_clock(0x30, 0x35, 0x37, 0x20)  # "057 "
    assert r.mode == "SS_TENTHS"
    assert r.display == "00:05.7"
    assert r.current_time_ms == 5_700


def test_parse_clock_ss_tenths_from_log_346():
    """Real log example: clock_raw='346 ' → 00:34.6, currentTimeMs=34600."""
    r = parse_clock(0x33, 0x34, 0x36, 0x20)  # "346 "
    assert r.mode == "SS_TENTHS"
    assert r.display == "00:34.6"
    assert r.current_time_ms == 34_600


def test_parse_clock_ss_tenths_from_log_345():
    r = parse_clock(0x33, 0x34, 0x35, 0x20)  # "345 "
    assert r.mode == "SS_TENTHS"
    assert r.display == "00:34.5"
    assert r.current_time_ms == 34_500


def test_parse_clock_ss_tenths_max():
    r = parse_clock(0x35, 0x39, 0x39, 0x20)  # "599 "
    assert r.mode == "SS_TENTHS"
    assert r.display == "00:59.9"
    assert r.current_time_ms == 59_900


def test_parse_clock_ss_tenths_sub10_seconds_066():
    """Real log example: clock_raw='066 ' → 00:06.6, currentTimeMs=6600."""
    r = parse_clock(0x30, 0x36, 0x36, 0x20)  # "066 "
    assert r.mode == "SS_TENTHS"
    assert r.display == "00:06.6"
    assert r.current_time_ms == 6_600


def test_parse_clock_ss_tenths_sub10_seconds_065():
    r = parse_clock(0x30, 0x36, 0x35, 0x20)  # "065 "
    assert r.mode == "SS_TENTHS"
    assert r.display == "00:06.5"
    assert r.current_time_ms == 6_500


def test_parse_clock_ss_tenths_sub1_second():
    r = parse_clock(0x30, 0x30, 0x39, 0x20)  # "009 " — 0.9 s
    assert r.mode == "SS_TENTHS"
    assert r.display == "00:00.9"
    assert r.current_time_ms == 900


def test_parse_clock_ss_tenths_invalid_seconds():
    r = parse_clock(0x36, 0x30, 0x30, 0x20)  # "600 " — 60s is invalid
    assert r.current_time_ms is None


# ---------------------------------------------------------------------------
# parse_clock — UNKNOWN
# ---------------------------------------------------------------------------

def test_parse_clock_unknown_all_spaces():
    r = parse_clock(0x20, 0x20, 0x20, 0x20)
    assert r.mode == "UNKNOWN"
    assert r.current_time_ms is None


# ---------------------------------------------------------------------------
# parse_frame
# ---------------------------------------------------------------------------

def _make_frame(code: int = 0x35, clock: bytes = b"1941") -> bytes:
    """Build a minimal valid 54-byte frame (zero-filled interior)."""
    frame = bytearray(FRAME_LEN)
    frame[0] = START
    frame[1] = code
    frame[4:8] = clock
    frame[FRAME_LEN - 1] = END
    return bytes(frame)


def _make_score_frame(
    clock: bytes,
    home_raw: bytes,
    away_raw: bytes,
    code: int = 0x35,
) -> bytes:
    """
    Build a space-filled 54-byte frame suitable for token tests.

    Layout (interior bytes 2-52 are 0x20 / space):
      0-1   : START + code
      2-3   : spaces
      4-7   : clock bytes
      8     : space
      9..   : home_raw bytes
      +1    : space separator
      ..    : away_raw bytes
      rest  : spaces
      53    : END

    This mirrors the CP437-decoded token structure seen in real hardware logs.
    """
    frame = bytearray(b"\x20" * FRAME_LEN)
    frame[0] = START
    frame[1] = code
    frame[4:8] = clock
    home_start = 9
    frame[home_start : home_start + len(home_raw)] = home_raw
    away_start = home_start + len(home_raw) + 1
    frame[away_start : away_start + len(away_raw)] = away_raw
    frame[FRAME_LEN - 1] = END
    return bytes(frame)


def test_parse_frame_valid():
    state = parse_frame(_make_frame(clock=b"1941"))
    assert state.valid is True
    assert state.clock_mode == "MMSS"
    assert state.current_time_ms == (19 * 60 + 41) * 1000
    assert state.clock_display == "19:41"


def test_parse_frame_sub10_minute():
    state = parse_frame(_make_frame(clock=b" 839"))
    assert state.valid is True
    assert state.clock_mode == "MMSS"
    assert state.current_time_ms == (8 * 60 + 39) * 1000


def test_parse_frame_invalid_start():
    frame = bytearray(_make_frame())
    frame[0] = 0x00
    state = parse_frame(bytes(frame))
    assert state.valid is False


def test_parse_frame_invalid_end():
    frame = bytearray(_make_frame())
    frame[FRAME_LEN - 1] = 0x00
    state = parse_frame(bytes(frame))
    assert state.valid is False


def test_parse_frame_wrong_length():
    state = parse_frame(b"\xF8" + b"\x00" * 10)
    assert state.valid is False


def test_parse_frame_code_hex():
    state = parse_frame(_make_frame(code=0x35))
    assert state.code_hex == "0x35"
    assert state.code_ascii == "5"


# ---------------------------------------------------------------------------
# FrameReader
# ---------------------------------------------------------------------------

def test_frame_reader_single_frame():
    reader = FrameReader()
    frame = _make_frame()
    frames = list(reader.feed(frame))
    assert len(frames) == 1
    assert frames[0] == frame


def test_frame_reader_two_frames():
    reader = FrameReader()
    frames = list(reader.feed(_make_frame() * 2))
    assert len(frames) == 2


def test_frame_reader_chunked_delivery():
    reader = FrameReader()
    frame = _make_frame()
    frames = []
    for byte in frame:
        frames.extend(reader.feed(bytes([byte])))
    assert len(frames) == 1
    assert frames[0] == frame


def test_frame_reader_leading_garbage():
    reader = FrameReader()
    frame = _make_frame()
    frames = list(reader.feed(b"\x00\x01\x02" + frame))
    assert len(frames) == 1
    assert frames[0] == frame


def test_frame_reader_misaligned_frame():
    """A fake START inside garbage should be skipped; the real frame is found."""
    reader = FrameReader()
    # 10 bytes of noise starting with 0xF8 (fake START), then the real frame
    noise = bytes([START] + [0x00] * 9)
    real = _make_frame()
    frames = list(reader.feed(noise + real))
    assert len(frames) == 1
    assert frames[0] == real


def test_frame_reader_stats():
    reader = FrameReader()
    frame = _make_frame()
    list(reader.feed(frame))
    assert reader.frames_seen == 1
    assert reader.bytes_seen == FRAME_LEN


# ---------------------------------------------------------------------------
# Scores — token extraction
#
# Scores are not parsed into dedicated state fields yet; they appear in the
# `tokens` list produced from the CP437-decoded frame:
#   tokens[2] = home score as decimal ASCII  (e.g. ' 3' → '3', '13' → '13')
#   tokens[3] = away score as hex ASCII      (e.g. b'\x30\x45' → '0E')
#
# Away score encoding hypothesis (unary/thermometer):
#   bin(int(token, 16)).count('1') gives the score.
#   0x0E = 0b00001110 → 3 set bits → score 3
#   0x1E = 0b00011110 → 4 set bits → score 4
#   0x3E = 0b00111110 → 5 set bits → score 5
#   (needs further confirmation with more real-world data)
# ---------------------------------------------------------------------------

# Clock bytes used in the score log examples (sub-10-min format ' MMM')
_CLK_151 = b"\x20\x31\x35\x31"  # ' 151' → 01:51
_CLK_148 = b"\x20\x31\x34\x38"  # ' 148' → 01:48
_CLK_141 = b"\x20\x31\x34\x31"  # ' 141' → 01:41
_CLK_140 = b"\x20\x31\x34\x30"  # ' 140' → 01:40


def test_score_tokens_home3_away_0E():
    """Real log: home=3, away='0E' (score 3 in thermometer encoding)."""
    state = parse_frame(_make_score_frame(
        clock=_CLK_151,
        home_raw=b"\x33",        # '3'
        away_raw=b"\x30\x45",   # '0E'
    ))
    assert state.valid
    assert state.tokens[2] == "3"
    assert state.tokens[3] == "0E"


def test_score_tokens_home5_away_0E():
    """Real log: home increased to 5, away still '0E'."""
    state = parse_frame(_make_score_frame(
        clock=_CLK_148,
        home_raw=b"\x35",        # '5'
        away_raw=b"\x30\x45",   # '0E'
    ))
    assert state.tokens[2] == "5"
    assert state.tokens[3] == "0E"


def test_score_tokens_home13_away_0E():
    """Real log: home reached 13, away still '0E'."""
    state = parse_frame(_make_score_frame(
        clock=_CLK_141,
        home_raw=b"\x31\x33",   # '13' (two-digit score)
        away_raw=b"\x30\x45",   # '0E'
    ))
    assert state.tokens[2] == "13"
    assert state.tokens[3] == "0E"


def test_score_tokens_home13_away_1E():
    """Real log: away score increased to '1E' (score 4 in thermometer encoding)."""
    state = parse_frame(_make_score_frame(
        clock=_CLK_141,
        home_raw=b"\x31\x33",   # '13'
        away_raw=b"\x31\x45",   # '1E'
    ))
    assert state.tokens[2] == "13"
    assert state.tokens[3] == "1E"


def test_score_tokens_home13_away_3E():
    """Real log: away score increased to '3E' (score 5 in thermometer encoding)."""
    state = parse_frame(_make_score_frame(
        clock=_CLK_140,
        home_raw=b"\x31\x33",   # '13'
        away_raw=b"\x33\x45",   # '3E'
    ))
    assert state.tokens[2] == "13"
    assert state.tokens[3] == "3E"


def test_score_home_single_to_double_digit():
    """Home score transition from single-digit to two-digit produces correct tokens."""
    for home_bytes, expected in [
        (b"\x39", "9"),    # single digit
        (b"\x31\x30", "10"),  # two digits
    ]:
        state = parse_frame(_make_score_frame(
            clock=_CLK_148,
            home_raw=home_bytes,
            away_raw=b"\x30\x45",
        ))
        assert state.tokens[2] == expected, f"home_raw={home_bytes!r}"


def test_score_away_thermometer_encoding():
    """Away score thermometer encoding: set-bit count of int(token, 16) = score."""
    cases = [
        (b"\x30\x45", "0E", 3),  # 0x0E = 0b00001110 → 3 set bits
        (b"\x31\x45", "1E", 4),  # 0x1E = 0b00011110 → 4 set bits
        (b"\x33\x45", "3E", 5),  # 0x3E = 0b00111110 → 5 set bits
    ]
    for away_raw, expected_token, expected_score in cases:
        state = parse_frame(_make_score_frame(
            clock=_CLK_148,
            home_raw=b"\x33",
            away_raw=away_raw,
        ))
        assert state.tokens[3] == expected_token
        assert bin(int(state.tokens[3], 16)).count("1") == expected_score
