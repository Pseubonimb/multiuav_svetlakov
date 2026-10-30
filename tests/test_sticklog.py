import io
import os
import tempfile

from multiuav.sticklog import StickWriter, to_us

MAPPING = {"throttle": {"code": 2}, "yaw": {"code": 0}, "pitch": {"code": 1, "inverted": True},
           "roll": {"code": 3}}
RANGES = {c: (0, 2047) for c in range(4)}


def _writer(f):
    return StickWriter(f, MAPPING, RANGES, {0: 1024, 1: 1024, 2: 0, 3: 1024})


def test_to_us():
    assert to_us(0, 0, 2047) == 1000.0
    assert to_us(2047, 0, 2047) == 2000.0
    assert to_us(2047, 0, 2047, inverted=True) == 1000.0


def test_rows_only_on_change_and_flushed():
    with tempfile.TemporaryFile("w+", encoding="utf-8") as f:
        w = _writer(f)
        assert w.report(1_000, 0)  # исходное положение
        assert not w.report(1_002, 0)  # ничего не сдвинулось
        w.abs_event(2, 104)
        w.abs_event(5, 777)  # не стик — не в журнал
        assert w.report(1_004, 1_003_900)
        os.lseek(f.fileno(), 0, os.SEEK_SET)  # прочитать то, что уже на диске
        lines = os.read(f.fileno(), 4096).decode().splitlines()
    assert lines[0] == "t_ms,evdev_us,throttle,yaw,pitch,roll,throttle_us,yaw_us,pitch_us,roll_us"
    assert lines[2].startswith("1004,1003900,104,1024,1024,1024,1050.8,")
    assert w.rows == 2


def test_syn_dropped_resyncs():
    w = _writer(io.StringIO())
    w.report(1, 0)
    w.dropped_events()
    w.abs_event(2, 500)  # после потери не верим
    assert not w.report(2, 0)
    w.resync({0: 1024, 1: 1024, 2: 600, 3: 1024})
    assert w.report(3, 0)
    assert w.values[2] == 600 and w.dropped == 1
