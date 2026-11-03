"""Обработка попытки после полёта: python -m multiuav analyze runs/P07/2026-11-06_16-05-01

Читает drone0.csv, buttons.csv, resets.csv, collisions.csv и геометрию трассы
(maps/<карта>.json), пишет рядом gates.csv, occlusions.csv, presses.csv, contacts.csv,
track.png и раздел "analysis" в summary.json. Исходные файлы не меняет; запускать
можно сколько угодно раз.

1. Пролёт ворот — момент, когда отрезок между соседними точками траектории пересекает
   плоскость ворот по ходу трассы; момент и точка — линейной интерполяцией.
   miss_m — расстояние от этой точки до центра ворот. Пересечение дальше
   ATTEMPT_RADIUS_M от центра — не попытка (пролёт в другом месте коридора).
   Ворота, которые пропущены между двумя попытками, — строка со status=skipped.
2. Затемнение — из occlusion_events.csv (как его включила программа: начало, конец,
   фактическая длительность); если файла нет — по входу дрона в коробку зоны.
3. Нажатие SE — переход 0 → 1; относится к ближайшему по времени пролёту ворот,
   если он не дальше window_s; dt_ms = нажатие − пролёт (минус — раньше, плюс — позже).
4. Касания — начала касаний (collided 0 → 1).
"""

from __future__ import annotations

import csv
import json
import math
import statistics
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .experiment import write_json
from .track import ATTEMPT_RADIUS_M, Track, expand_cyclic

#: Скачок дальше этого за один шаг — телепорт (сброс), а не полёт: отрезок не считаем.
MAX_STEP_M = 15.0


@dataclass
class Flight:
    ms: np.ndarray  # pc_time_ms
    t: np.ndarray  # t_s
    pos: np.ndarray  # (n, 3): north, east, alt
    collided: np.ndarray


def read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, columns: list[str], rows: list[dict]) -> None:
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: _fmt(r.get(k, "")) for k in columns})


def _fmt(v):
    if isinstance(v, float):
        return "" if math.isnan(v) else f"{v:.3f}"
    if isinstance(v, bool):
        return int(v)
    return v


def load_flight(folder: Path) -> Flight:
    rows = read_csv(folder / "drone0.csv")
    if not rows:
        raise SystemExit(f"Нет траектории {folder / 'drone0.csv'}")
    ms = np.array([float(r["pc_time_ms"]) for r in rows])
    t = np.array([float(r["t_s"]) for r in rows])
    pos = np.array([[float(r["north"]), float(r["east"]), float(r["alt_m"])] for r in rows])
    collided = np.array([int(r["collided"] or 0) for r in rows])
    return Flight(ms, t, pos, collided)


def valid_segments(flight: Flight, reset_ms: list[float]) -> np.ndarray:
    """Отрезки i-1 → i, которые были полётом: без сброса между точками и без скачка."""
    n = len(flight.ms)
    ok = np.ones(n, dtype=bool)
    ok[0] = False
    jumps = np.linalg.norm(np.diff(flight.pos, axis=0), axis=1) > MAX_STEP_M
    ok[1:] &= ~jumps
    for r in reset_ms:
        i = int(np.searchsorted(flight.ms, r, side="right"))  # первая точка после сброса
        if 0 < i < n:
            ok[i] = False
    return ok


def find_crossings(track: Track, flight: Flight, ok: np.ndarray) -> list[dict]:
    """Все попытки пройти ворота по времени: и через проём, и рядом с ним."""
    result = []
    for i in np.flatnonzero(ok):
        p0, p1 = flight.pos[i - 1], flight.pos[i]
        for g in track.gates:
            a = g.crossing(p0, p1)
            if a is None:
                continue
            point = p0 + a * (p1 - p0)
            miss = float(np.linalg.norm(point - g.center))
            if miss > ATTEMPT_RADIUS_M:
                continue
            right, up = g.offset(point)
            result.append({
                "gate": g.index, "gate_name": g.name,
                "t_cross_ms": int(round(flight.ms[i - 1] + a * (flight.ms[i] - flight.ms[i - 1]))),
                "t_s": float(flight.t[i - 1] + a * (flight.t[i] - flight.t[i - 1])),
                "miss_m": miss, "right_m": right, "up_m": up,
                "passed": int(g.inside(point)),
            })
    result.sort(key=lambda r: r["t_cross_ms"])
    return result


def number_laps(crossings: list[dict], gates: int) -> list[dict]:
    """Круги и пропущенные ворота: ждём ворота по порядку, круг — когда номер «перескочил» назад."""
    rows, lap, last = [], 1, None
    for c in crossings:
        g = c["gate"]
        if last is not None and g <= last:
            # пропущенные в конце круга, затем новый круг
            for k in range(last + 1, gates):
                rows.append({"lap": lap, "gate": k, "status": "skipped"})
            lap += 1
            start = 0
        else:
            start = 0 if last is None else last + 1
        for k in range(start, g):
            rows.append({"lap": lap, "gate": k, "status": "skipped"})
        rows.append({**c, "lap": lap, "status": "passed" if c["passed"] else "outside"})
        last = g
    return rows


def find_occlusions(track: Track, flight: Flight, ok: np.ndarray, durations: list[int]) -> list[dict]:
    result = []
    for i in np.flatnonzero(ok):
        p0, p1 = flight.pos[i - 1], flight.pos[i]
        for z in track.zones:
            a = z.entry(p0, p1)
            if a is None:
                continue
            start = flight.ms[i - 1] + a * (flight.ms[i] - flight.ms[i - 1])
            dur = durations[z.index] if z.index < len(durations) else 0
            result.append({"zone": z.index, "zone_name": z.label, "t_start_ms": int(round(start)),
                           "t_s": float(flight.t[i - 1] + a * (flight.t[i] - flight.t[i - 1])),
                           "dur_ms": dur, "t_end_ms": int(round(start)) + dur})
    result.sort(key=lambda r: r["t_start_ms"])
    for n, r in enumerate(result):
        r["n"] = n + 1
    return result


def occlusion_at(occlusions: list[dict], ms: float) -> dict | None:
    for o in occlusions:
        if o["t_start_ms"] <= ms <= o["t_end_ms"] and o["dur_ms"] > 0:
            return o
    return None


def mark_gate_occlusions(gate_rows: list[dict], occlusions: list[dict]) -> None:
    """Было ли затемнение перед пролётом: началось после прошлой попытки и до этой."""
    prev = -math.inf
    for r in gate_rows:
        if "t_cross_ms" not in r:
            continue
        t = r["t_cross_ms"]
        before = [o for o in occlusions if prev < o["t_start_ms"] <= t and o["dur_ms"] > 0]
        if before:
            o = before[-1]
            r.update(occlusion_before=1, occlusion_zone=o["zone"], occlusion_ms=o["dur_ms"],
                     ms_after_occlusion_end=t - o["t_end_ms"])
        else:
            r["occlusion_before"] = 0
        r["occluded_at_cross"] = int(occlusion_at(occlusions, t) is not None)
        prev = t


def se_presses(folder: Path, button: str = "SE") -> list[dict]:
    """Нажатия: переходы 0 → 1 кнопки из buttons.csv."""
    result, down = [], False
    for r in read_csv(folder / "buttons.csv"):
        if r["button"] != button:
            continue
        state = int(r["state"])
        if state == 1 and not down:
            result.append({"press_ms": int(r["t_ms"]),
                           "evdev_us": int(r["evdev_us"]) if r.get("evdev_us") else ""})
        down = state == 1
    return result


def match_presses(presses: list[dict], crossings: list[dict], flight: Flight, track: Track,
                  occlusions: list[dict], window_s: float) -> tuple[list[dict], int]:
    rows, unmatched = [], 0
    times = np.array([c["t_cross_ms"] for c in crossings], dtype=float)
    seen: dict[tuple, int] = {}
    for p in presses:
        ms = p["press_ms"]
        if len(times) == 0:
            unmatched += 1
            continue
        k = int(np.argmin(np.abs(times - ms)))
        dt = ms - times[k]
        if abs(dt) > window_s * 1000:
            unmatched += 1
            continue
        c = crossings[k]
        gate = track.gates[c["gate"]]
        where = np.array([np.interp(ms, flight.ms, flight.pos[:, j]) for j in range(3)])
        o = occlusion_at(occlusions, ms)
        key = (c["lap"], c["gate"])
        seen[key] = seen.get(key, 0) + 1
        rows.append({"press_ms": ms, "t_s": float(np.interp(ms, flight.ms, flight.t)),
                     "lap": c["lap"], "gate": c["gate"], "gate_name": c["gate_name"],
                     "t_cross_ms": c["t_cross_ms"], "dt_ms": int(round(dt)),
                     "dist_m": float(np.linalg.norm(where - gate.center)),
                     "occluded": int(o is not None), "occlusion_zone": "" if o is None else o["zone"],
                     "nth_for_gate": seen[key]})
    return rows, unmatched


def contacts(flight: Flight, folder: Path) -> list[dict]:
    objects = {int(r["pc_time_ms"]): r["object"] for r in read_csv(folder / "collisions.csv")}
    rows, prev = [], 0
    for i, c in enumerate(flight.collided):
        if c and not prev:
            ms = int(flight.ms[i])
            rows.append({"t_start_ms": ms, "t_s": float(flight.t[i]), "object": objects.get(ms, ""),
                         "north": float(flight.pos[i, 0]), "east": float(flight.pos[i, 1]),
                         "alt_m": float(flight.pos[i, 2])})
        prev = c
    return rows


def plot(folder: Path, track: Track, flight: Flight, gate_rows: list[dict], contact_rows: list[dict],
         walls: list) -> Path:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle

    fig, ax = plt.subplots(figsize=(9, 12))
    for n0, e0, n1, e1 in walls:
        ax.add_patch(Rectangle((e0, n0), e1 - e0, n1 - n0, color="0.75", lw=0))
    for z in track.zones:
        ax.add_patch(Rectangle((z.center[1] - z.half[1], z.center[0] - z.half[0]), 2 * z.half[1],
                               2 * z.half[0], color="gold", alpha=0.5, lw=0))
    ax.plot(flight.pos[:, 1], flight.pos[:, 0], color="tab:blue", lw=0.8, label="траектория")
    for g in track.gates:
        r = np.array([-math.sin(g.heading), math.cos(g.heading)]) * g.width / 2
        a, b = g.center[:2] - r, g.center[:2] + r
        ax.plot([a[1], b[1]], [a[0], b[0]], color="tab:orange", lw=3)
        ax.annotate(str(g.index + 1), (g.center[1], g.center[0]), (6, 6), textcoords="offset points")
    passed = [r for r in gate_rows if r.get("status") == "passed"]
    if contact_rows:
        ax.plot([c["east"] for c in contact_rows], [c["north"] for c in contact_rows], "x",
                color="red", ms=7, label=f"касания: {len(contact_rows)}")
    ax.set_aspect("equal")
    ax.set_xlabel("восток, м")
    ax.set_ylabel("север, м")
    ax.set_title(f"{folder.parent.name} / {folder.name}: ворот пройдено {len(passed)}")
    ax.legend(loc="upper right")
    fig.tight_layout()
    out = folder / "track.png"
    fig.savefig(out, dpi=90)
    plt.close(fig)
    return out


def _stats(values: list[float]) -> tuple[float | None, float | None]:
    if not values:
        return None, None
    return round(statistics.fmean(values), 3), round(statistics.median(values), 3)


def analyze(folder: Path, track_path: Path | None = None, window_s: float = 2.0) -> dict:
    folder = Path(folder)
    exp_path = folder / "experiment.json"
    exp = json.loads(exp_path.read_text(encoding="utf-8")) if exp_path.exists() else {}
    track_path = track_path or Path(exp.get("track", "maps/FlyingExampleMap.json"))
    track = Track.load(track_path)
    track_data = json.loads(Path(track_path).read_text(encoding="utf-8"))
    durations = exp.get("occlusion_ms_by_zone") or expand_cyclic(
        [int(v) for v in exp.get("occlusion_ms", [])], len(track.zones))

    flight = load_flight(folder)
    reset_ms = [float(r["pc_time_ms"]) for r in read_csv(folder / "resets.csv")]
    ok = valid_segments(flight, reset_ms)
    crossings = find_crossings(track, flight, ok)
    gate_rows = number_laps(crossings, len(track.gates))
    for r in gate_rows:
        r.setdefault("gate_name", track.gates[r["gate"]].name)
    events = read_csv(folder / "occlusion_events.csv")
    if events:  # как затемнение включала программа — точнее, чем по траектории
        occlusions = [{"n": int(e["n"]), "zone": int(e["zone"]), "zone_name": e["zone_name"],
                       "t_start_ms": int(e["t_on_ms"]),
                       "t_s": float(np.interp(int(e["t_on_ms"]), flight.ms, flight.t)),
                       "dur_ms": int(e["dur_ms"]), "t_end_ms": int(e["t_off_ms"]),
                       "actual_ms": int(e["actual_ms"])} for e in events]
    else:
        occlusions = find_occlusions(track, flight, ok, durations)
    mark_gate_occlusions(gate_rows, occlusions)
    attempts = [r for r in gate_rows if "t_cross_ms" in r]
    press_rows, unmatched = match_presses(se_presses(folder), attempts, flight, track, occlusions,
                                          window_s)
    contact_rows = contacts(flight, folder)

    write_csv(folder / "gates.csv",
              ["lap", "gate", "gate_name", "status", "t_cross_ms", "t_s", "miss_m", "right_m", "up_m",
               "occlusion_before", "occlusion_zone", "occlusion_ms", "ms_after_occlusion_end",
               "occluded_at_cross"], gate_rows)
    write_csv(folder / "occlusions.csv",
              ["n", "zone", "zone_name", "t_start_ms", "t_s", "dur_ms", "t_end_ms", "actual_ms"],
              occlusions)
    write_csv(folder / "presses.csv",
              ["press_ms", "t_s", "lap", "gate", "gate_name", "t_cross_ms", "dt_ms", "dist_m",
               "occluded", "occlusion_zone", "nth_for_gate"], press_rows)
    write_csv(folder / "contacts.csv", ["t_start_ms", "t_s", "object", "north", "east", "alt_m"],
              contact_rows)
    plot(folder, track, flight, gate_rows, contact_rows, track_data.get("walls", []))

    passed = [r for r in gate_rows if r["status"] == "passed"]
    miss_mean, miss_median = _stats([r["miss_m"] for r in passed])
    dt_mean, dt_median = _stats([abs(r["dt_ms"]) for r in press_rows])
    analysis = {
        "track": str(track_path),
        "laps": max((r["lap"] for r in gate_rows), default=0),
        "gates_passed": len(passed),
        "gates_outside": sum(r["status"] == "outside" for r in gate_rows),
        "gates_skipped": sum(r["status"] == "skipped" for r in gate_rows),
        "miss_mean_m": miss_mean,
        "miss_median_m": miss_median,
        "presses": len(press_rows) + unmatched,
        "presses_matched": len(press_rows),
        "presses_unmatched": unmatched,
        "press_window_s": window_s,
        "abs_dt_mean_ms": dt_mean,
        "abs_dt_median_ms": dt_median,
        "occlusions": len(occlusions),
        "contacts": len(contact_rows),
        "contact_start_ms": [c["t_start_ms"] for c in contact_rows],
        "resets": len(reset_ms),
    }
    summary_path = folder / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.exists() else {}
    summary["analysis"] = analysis
    write_json(summary_path, summary)
    return analysis
