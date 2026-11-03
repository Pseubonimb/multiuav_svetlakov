import csv
import io
import json
import os
import tempfile
from pathlib import Path

import pytest

from multiuav.analyze import analyze
from multiuav.experiment import FLIGHT_COLUMNS, RESET_COLUMNS, local_time, parse_occlusion, safe_name
from multiuav.sticklog import ButtonWriter
from multiuav.track import Track, expand_cyclic

# Прямая трасса на восток: зона затемнения 40–60 м, ворота на 70 и 150 м, высота 5 м.
TRACK = {
    "map": "/Game/Test", "player_start_yaw_deg": 90,
    "gates": [
        {"index": 0, "name": "G1", "center": {"north": 0, "east": 70, "alt": 5}, "heading_deg": 90,
         "width_m": 4, "height_m": 4},
        {"index": 1, "name": "G2", "center": {"north": 0, "east": 150, "alt": 5}, "heading_deg": 90,
         "width_m": 4, "height_m": 4},
    ],
    "zones": [
        {"index": 0, "name": "Z1", "label": "Z1", "center": {"north": 0, "east": 50, "alt": 5},
         "half_size": {"north": 10, "east": 10, "up": 10}},
    ],
}
T0 = 1_800_000_000_000  # pc_time_ms начала
SPEED = 10.0  # м/с на восток, точка каждые 50 мс


def _flight(folder: Path, north_offset: float = 0.0, seconds: float = 20.0, resets=()):
    rows = []
    for i in range(int(seconds * 20) + 1):
        t = i * 0.05
        rows.append([T0 + i * 50, f"{t:.3f}", f"{north_offset:.3f}", f"{SPEED * t:.3f}", "5.000",
                     "90", "0", "0", 0, 1500, 1500, 1500, 1500, "acro"])
    with open(folder / "drone0.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(FLIGHT_COLUMNS)
        w.writerows(rows)
    with open(folder / "resets.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(RESET_COLUMNS)
        w.writerows(resets)


def _buttons(folder: Path, presses_ms):
    with open(folder / "buttons.csv", "w", encoding="utf-8") as f:
        w = ButtonWriter(f, {304: "SE"})
        for ms in presses_ms:
            w.key_event(ms, ms * 1000, 304, 1)
            w.key_event(ms + 80, (ms + 80) * 1000, 304, 0)


@pytest.fixture
def run(tmp_path):
    track = tmp_path / "track.json"
    track.write_text(json.dumps(TRACK), encoding="utf-8")
    (tmp_path / "experiment.json").write_text(json.dumps(
        {"track": str(track), "occlusion_ms": [500]}), encoding="utf-8")
    return tmp_path


def _rows(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_gate_crossing_interpolated(run):
    _flight(run, north_offset=1.0)
    _buttons(run, [])
    summary = analyze(run)
    gates = _rows(run / "gates.csv")
    assert [g["gate"] for g in gates] == ["0", "1"]
    # 70 м при 10 м/с — ровно 7 с: между точками интерполяция даёт точное время
    assert int(gates[0]["t_cross_ms"]) == T0 + 7000
    assert float(gates[0]["miss_m"]) == pytest.approx(1.0)
    assert gates[0]["status"] == "passed"
    assert gates[0]["occlusion_before"] == "1" and gates[0]["occlusion_ms"] == "500"
    assert gates[1]["occlusion_before"] == "0"
    occ = _rows(run / "occlusions.csv")
    assert len(occ) == 1 and int(occ[0]["t_start_ms"]) == T0 + 4000  # вход в зону на 40 м
    assert summary["gates_passed"] == 2 and summary["miss_mean_m"] == pytest.approx(1.0)


def test_presses_before_after_and_window(run):
    _flight(run)
    # 35 мс раньше ворот 1, 120 мс позже ворот 2, и одно — за 3 с до всех ворот
    _buttons(run, [T0 + 7000 - 35, T0 + 15000 + 120, T0 + 3000])
    summary = analyze(run)
    presses = _rows(run / "presses.csv")
    assert [int(p["dt_ms"]) for p in presses] == [-35, 120]
    assert [p["gate"] for p in presses] == ["0", "1"]
    assert float(presses[0]["dist_m"]) == pytest.approx(0.35, abs=1e-3)  # 35 мс × 10 м/с
    assert summary["presses"] == 3 and summary["presses_unmatched"] == 1
    assert summary["abs_dt_mean_ms"] == pytest.approx(77.5)


def test_press_inside_occlusion(run):
    _flight(run)
    _buttons(run, [T0 + 4200])  # затемнение 4000–4500 мс, ворота в 7000 → дальше 2 с
    summary = analyze(run, window_s=3.0)
    p = _rows(run / "presses.csv")[0]
    assert p["occluded"] == "1" and int(p["dt_ms"]) == -2800
    assert summary["presses_matched"] == 1


def test_outside_opening_and_skipped(run):
    _flight(run, north_offset=5.0)  # мимо проёма 4 м, но рядом с воротами
    _buttons(run, [])
    analyze(run)
    gates = _rows(run / "gates.csv")
    assert [g["status"] for g in gates] == ["outside", "outside"]


def test_reset_segment_ignored(run):
    _flight(run, resets=[[T0 + 7000 - 10, "6.990", -1, "старт", "клавиша R", 0, 69, 5, 0, 0, 5]])
    _buttons(run, [])
    summary = analyze(run)
    gates = _rows(run / "gates.csv")
    # отрезок с пролётом ворот 1 совпал со сбросом — не считается, ворота 1 пропущены
    assert [(g["gate"], g["status"]) for g in gates] == [("0", "skipped"), ("1", "passed")]
    assert summary["resets"] == 1


def test_analyze_is_repeatable(run):
    _flight(run)
    _buttons(run, [T0 + 7000])
    a = analyze(run)
    before = (run / "drone0.csv").read_bytes()
    b = analyze(run)
    assert a == b and (run / "drone0.csv").read_bytes() == before
    assert (run / "track.png").exists()


def test_laps_count():
    from multiuav.analyze import number_laps

    rows = number_laps([{"gate": g, "passed": 1, "t_cross_ms": i} for i, g in enumerate([0, 1, 2, 0, 2])], 3)
    assert [(r["lap"], r["gate"], r["status"]) for r in rows] == [
        (1, 0, "passed"), (1, 1, "passed"), (1, 2, "passed"),
        (2, 0, "passed"), (2, 1, "skipped"), (2, 2, "passed")]


def test_button_writer_rows_and_autorepeat():
    with tempfile.TemporaryFile("w+", encoding="utf-8") as f:
        w = ButtonWriter(f, {304: "SE"})
        assert w.key_event(1000, 999_500, 304, 1)
        assert not w.key_event(1001, 999_600, 304, 2)  # автоповтор — не событие
        assert w.key_event(1100, 1_099_500, 304, 0)
        assert w.key_event(1200, 1_199_500, 305, 1, "BTN_B")
        os.lseek(f.fileno(), 0, os.SEEK_SET)
        lines = os.read(f.fileno(), 4096).decode().splitlines()
    assert lines == ["t_ms,evdev_us,button,code,state", "1000,999500,SE,304,1",
                     "1100,1099500,SE,304,0", "1200,1199500,BTN_B,305,1"]


def test_local_time_format():
    from datetime import datetime

    ms = int(datetime(2026, 11, 6, 16, 5, 1).timestamp() * 1000) + 123
    assert local_time(ms) == "2026-11-06, пятница, 16:05:01.123"


def test_names_and_occlusion_list():
    assert safe_name(" Иванов П. 07/2 ") == "Иванов_П_07_2"
    assert safe_name("../../etc") == "etc"
    assert parse_occlusion("75,100, 500 1000") == [75, 100, 500, 1000]
    assert expand_cyclic([75, 500], 5) == [75, 500, 75, 500, 75]


def test_track_zone_entry_and_gate():
    track = Track.from_dict(TRACK)
    import numpy as np

    z = track.zones[0]
    assert z.entry(np.array([0, 30, 5.0]), np.array([0, 45, 5.0])) == pytest.approx(10 / 15)
    assert track.gate_passed(np.array([0, 69, 5.0]), np.array([0, 71, 5.0]))[0][0].name == "G1"
    assert track.gate_passed(np.array([0, 71, 5.0]), np.array([0, 69, 5.0])) == []  # назад
