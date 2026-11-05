"""Синхронизация часов с компьютером датчиков: снимок `chronyc tracking` в начале и в конце попытки.

Часы Ubuntu сверяются службой chrony с Windows-компьютером датчиков (docs/DEPLOY_STAND.md,
раздел 10). Здесь — только запись, насколько они совпадали: в experiment.json → clock_sync.
"""

from __future__ import annotations

import shutil
import subprocess
import time

#: Поля `chronyc -c tracking` по порядку.
TRACKING_FIELDS = ["ref_id", "ref_name", "stratum", "ref_time_s", "system_time_s", "last_offset_s",
                   "rms_offset_s", "frequency_ppm", "residual_freq_ppm", "skew_ppm", "root_delay_s",
                   "root_dispersion_s", "update_interval_s", "leap_status"]
#: Расхождение больше этого — предупредить в меню.
WARN_OFFSET_S = 0.002


def parse_tracking(line: str) -> dict:
    values = line.strip().split(",")
    if len(values) < len(TRACKING_FIELDS):
        raise ValueError(f"не похоже на chronyc -c tracking: {line!r}")
    result: dict = {}
    for name, value in zip(TRACKING_FIELDS, values):
        if name in ("ref_id", "ref_name", "leap_status"):
            result[name] = value
        elif name == "stratum":
            result[name] = int(value)
        else:
            result[name] = float(value)
    return result


def snapshot(expected_server: str | None = None) -> dict:
    """Состояние часов сейчас и короткий вывод: ok и текст для меню."""
    shot: dict = {"pc_time_ms": time.time_ns() // 1_000_000}
    if not shutil.which("chronyc"):
        shot.update(ok=False, text="chrony не установлен — время не сверяется с компьютером датчиков")
        return shot
    try:
        out = subprocess.run(["chronyc", "-c", "tracking"], capture_output=True, text=True,
                             timeout=5).stdout
        shot.update(parse_tracking(out.splitlines()[0]))
    except (subprocess.SubprocessError, ValueError, IndexError) as e:
        shot.update(ok=False, text=f"не удалось спросить chrony: {e}")
        return shot
    offset_ms = shot["system_time_s"] * 1000
    # В chrony положительное System time — часы этого компьютера отстают от источника.
    where = "отстают" if offset_ms > 0 else "спешат"
    problems = []
    if shot["leap_status"].lower() != "normal":
        problems.append(f"часы не синхронизированы ({shot['leap_status']})")
    if expected_server and shot["ref_name"] != expected_server:
        problems.append(f"время берётся не с {expected_server}, а с {shot['ref_name'] or 'никого'}")
    if abs(shot["system_time_s"]) > WARN_OFFSET_S:
        problems.append(f"расхождение {abs(offset_ms):.3f} мс")
    shot["ok"] = not problems
    shot["text"] = ("часы: " + "; ".join(problems) if problems else
                    f"часы сверены с {shot['ref_name']}: {where} на {abs(offset_ms):.3f} мс")
    return shot
