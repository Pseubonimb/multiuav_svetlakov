"""Журнал стиков пульта RadioMaster через evdev — отдельным процессом от симулятора.

Пульт по USB — обычный джойстик Linux (/dev/input/eventN). Ядро шлёт события
EV_ABS (новое значение оси) и после каждой пачки SYN_REPORT. На каждый
SYN_REPORT, в котором сдвинулся хоть один из четырёх стиков, в sticks.csv
пишется строка — сразу на диск, чтобы при падении данные остались.

Время — общие часы компьютера: t_ms = time.time_ns() // 1_000_000 в момент
чтения пачки (без отсчёта от нуля, чтобы склеивать с другими журналами).
Рядом evdev_us — метка ядра в микросекундах на тех же часах (CLOCK_REALTIME):
когда событие пришло по USB, без задержки самого скрипта.

Пульт отдаёт каналы уже после своего микшера (экспоненты, расходы, кривые
EdgeTX), поэтому профиль модели и фильтр АЦП записываются в паспорт попытки.

  python -m multiuav sticks list                 устройства и их оси (Min/Max/Fuzz/Flat)
  python -m multiuav sticks map                  какой стик какой оси соответствует
  python -m multiuav sticks log --profile ...    записать попытку
"""

from __future__ import annotations

import json
import os
import platform
import select
import sys
import threading
import time
from pathlib import Path

STICKS = ("throttle", "yaw", "pitch", "roll")
STICK_TEXT = {  # что просить сделать при разметке; «вверх/вправо» = 2000
    "throttle": ("газ", "вверх"),
    "yaw": ("рыскание (поворот)", "вправо"),
    "pitch": ("тангаж (вперёд-назад)", "вверх (от себя)"),
    "roll": ("крен (влево-вправо)", "вправо"),
}
DEFAULT_MAP = Path("radio/sticks_map.json")
FSYNC_EVERY_S = 1.0


def _evdev():
    try:
        import evdev
    except ImportError:
        raise SystemExit("Нет модуля evdev: pip install -e \".[sticks]\"") from None
    return evdev


def _open(path: str):
    evdev = _evdev()
    try:
        return evdev.InputDevice(path)
    except PermissionError:
        raise SystemExit(
            f"Нет прав на {path}. Варианты: подключить пульт заново (права на джойстики "
            "даёт systemd вошедшему пользователю), добавить себя в группу input "
            "(sudo usermod -aG input $USER и перелогиниться) или запустить через sudo."
        ) from None


def _abs_devices():
    """Устройства с осями ABS (джойстики, пульты), без тачпадов и мышей."""
    evdev = _evdev()
    found = []
    for path in evdev.list_devices():
        try:
            dev = evdev.InputDevice(path)
        except OSError:
            continue
        axes = dev.capabilities().get(evdev.ecodes.EV_ABS, [])
        if len(axes) >= 4:
            found.append(dev)
    return found


def find_device(path: str | None = None):
    """Пульт по пути или первый джойстик с 4+ осями (RadioMaster/EdgeTX — в приоритете)."""
    if path:
        return _open(path)
    devices = _abs_devices()
    if not devices:
        raise SystemExit("Не нашёл ни одного джойстика с осями. Пульт подключён в режиме "
                         "USB Joystick (HID)? Если evtest видит его только через sudo — "
                         "дело в правах, см. sticks list --device /dev/input/eventN.")
    for dev in devices:
        if any(word in dev.name.lower() for word in ("radiomaster", "edgetx", "opentx")):
            return dev
    if len(devices) > 1:
        names = "\n".join(f"  {d.path}  {d.name}" for d in devices)
        raise SystemExit(f"Несколько джойстиков, укажите --device:\n{names}")
    return devices[0]


def axes_info(dev) -> dict[int, object]:
    """Код оси → AbsInfo(value, min, max, fuzz, flat, resolution)."""
    evdev = _evdev()
    return {code: info for code, info in dev.capabilities().get(evdev.ecodes.EV_ABS, [])}


def axis_name(code: int) -> str:
    name = _evdev().ecodes.ABS.get(code, f"ABS_{code}")
    return name[0] if isinstance(name, list) else name


def zero_fuzz_flat(dev) -> dict[int, tuple[int, int]]:
    """Обнулить Fuzz и Flat у всех осей. Возвращает {код: (fuzz, flat)} до обнуления.

    Fuzz — ядро отбрасывает изменения меньше этого (срезает тремор),
    Flat — мёртвая зона у центра (её учитывают SDL/pygame). Обнуление действует
    до отключения пульта и видно всем программам, включая симулятор.
    """
    before = {}
    for code, info in axes_info(dev).items():
        before[code] = (info.fuzz, info.flat)
        if info.fuzz or info.flat:
            dev.set_absinfo(code, fuzz=0, flat=0)
    return before


def to_us(value: int, lo: int, hi: int, inverted: bool = False) -> float:
    """Сырое значение оси → 1000–2000 (вверх/вправо = 2000)."""
    us = 1000.0 + 1000.0 * (value - lo) / (hi - lo)
    return 3000.0 - us if inverted else us


def device_passport(dev) -> dict:
    info = dev.info
    return {"path": dev.path, "name": dev.name, "phys": dev.phys, "uniq": dev.uniq,
            "vendor": f"{info.vendor:04x}", "product": f"{info.product:04x}",
            "version": f"{info.version:04x}"}


# ---------------------------------------------------------------- list


def run_list(device: str | None) -> None:
    devices = [_open(device)] if device else _abs_devices()
    if not devices:
        print("Джойстиков с осями не найдено (или нет прав на /dev/input/event*).")
    for dev in devices:
        print(f"{dev.path}  {dev.name}")
        for code, info in sorted(axes_info(dev).items()):
            print(f"  код {code:2d} {axis_name(code):10s} Min {info.min:6d}  Max {info.max:6d}  "
                  f"Fuzz {info.fuzz}  Flat {info.flat}  сейчас {info.value}")


# ---------------------------------------------------------------- map


class _AxisWatcher:
    """Фоновое чтение осей: размах каждой оси с момента reset() и текущие значения."""

    def __init__(self, dev) -> None:
        self.dev = dev
        self.value = {code: info.value for code, info in axes_info(dev).items()}
        self.lo = dict(self.value)
        self.hi = dict(self.value)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        evdev = _evdev()
        while not self._stop.is_set():
            if not select.select([self.dev.fd], [], [], 0.1)[0]:
                continue
            for ev in self.dev.read():
                if ev.type == evdev.ecodes.EV_ABS and ev.code in self.value:
                    with self._lock:
                        self.value[ev.code] = ev.value
                        self.lo[ev.code] = min(self.lo[ev.code], ev.value)
                        self.hi[ev.code] = max(self.hi[ev.code], ev.value)

    def reset(self) -> None:
        with self._lock:
            self.lo = dict(self.value)
            self.hi = dict(self.value)

    def stop(self) -> None:
        self._stop.set()


def run_map(device: str | None, save: Path) -> None:
    dev = find_device(device)
    info = axes_info(dev)
    print(f"Пульт: {dev.path}  {dev.name}")
    watcher = _AxisWatcher(dev)
    mapping: dict[str, dict] = {}
    for stick in STICKS:
        text, direction = STICK_TEXT[stick]
        input(f"\nСтик «{text}»: нажмите Enter, подвигайте ТОЛЬКО его от края до края "
              f"2–3 раза, затем держите {direction} и снова нажмите Enter.")
        watcher.reset()
        input("  …двигайте, держите " + direction + " и нажмите Enter ")
        free = [c for c in info if c not in {m["code"] for m in mapping.values()}]
        span = {c: (watcher.hi[c] - watcher.lo[c]) / max(1, info[c].max - info[c].min) for c in free}
        code = max(span, key=span.get)
        if span[code] < 0.3:
            print(f"  Ось почти не двигалась (размах {span[code]:.0%}), попробуйте ещё раз.")
            watcher.stop()
            raise SystemExit(1)
        mid = (info[code].min + info[code].max) / 2
        inverted = watcher.value[code] < mid  # держим вверх/вправо — должно быть у Max
        mapping[stick] = {"code": code, "name": axis_name(code), "inverted": inverted}
        print(f"  {text} → {axis_name(code)} (код {code}), размах {span[code]:.0%}"
              + (", обратная" if inverted else ""))
    watcher.stop()
    save.parent.mkdir(parents=True, exist_ok=True)
    save.write_text(json.dumps({"device_name": dev.name, "axes": mapping},
                               ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\nСоответствие записано: {save}")


# ---------------------------------------------------------------- log


class StickWriter:
    """Пачки событий → строки sticks.csv. Без evdev — удобно проверять тестами."""

    def __init__(self, f, mapping: dict, ranges: dict[int, tuple[int, int]],
                 values: dict[int, int], fsync_each_row: bool = False) -> None:
        self.f = f
        self.sticks = [s for s in STICKS if s in mapping]
        self.code = {s: mapping[s]["code"] for s in self.sticks}
        self.inverted = {s: bool(mapping[s].get("inverted")) for s in self.sticks}
        self.ranges = ranges
        self.values = {self.code[s]: values[self.code[s]] for s in self.sticks}
        self.fsync_each_row = fsync_each_row
        self.rows = 0
        self.dropped = 0
        self._changed = True  # первая строка — исходное положение стиков
        self._in_drop = False
        self._last_sync = time.monotonic()
        f.write(",".join(["t_ms", "evdev_us", *self.sticks, *(s + "_us" for s in self.sticks)]) + "\n")
        f.flush()

    def abs_event(self, code: int, value: int) -> None:
        if code in self.values and not self._in_drop:
            self._changed |= self.values[code] != value
            self.values[code] = value

    def dropped_events(self) -> None:
        """SYN_DROPPED: ядро потеряло события, до следующего SYN_REPORT им не верим."""
        self.dropped += 1
        self._in_drop = True

    def resync(self, values: dict[int, int]) -> None:
        """После потери событий — текущее состояние осей, прочитанное у ядра."""
        for code in self.values:
            self._changed |= self.values[code] != values[code]
            self.values[code] = values[code]
        self._in_drop = False

    def report(self, t_ms: int, evdev_us: int) -> bool:
        if self._in_drop or not self._changed:
            return False
        raw = [self.values[self.code[s]] for s in self.sticks]
        us = [to_us(v, *self.ranges[self.code[s]], self.inverted[s]) for s, v in zip(self.sticks, raw)]
        self.f.write(",".join([str(t_ms), str(evdev_us), *map(str, raw), *(f"{u:.1f}" for u in us)]) + "\n")
        self.f.flush()  # строка ушла из процесса: переживёт падение скрипта
        now = time.monotonic()
        if self.fsync_each_row or now - self._last_sync >= FSYNC_EVERY_S:
            os.fsync(self.f.fileno())  # и физически на диск: переживёт отключение питания
            self._last_sync = now
        self.rows += 1
        self._changed = False
        return True

    def us_now(self) -> dict[str, float]:
        return {s: to_us(self.values[self.code[s]], *self.ranges[self.code[s]], self.inverted[s])
                for s in self.sticks}


def _write_json(path: Path, data: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)  # паспорт не окажется наполовину записанным


def run_log(device: str | None, map_path: Path, out: Path | None, profile: str | None,
            adc_filter: str, note: str, keep_fuzz: bool, fsync_each_row: bool) -> None:
    evdev = _evdev()
    if not map_path.exists():
        raise SystemExit(f"Нет файла соответствия {map_path}: сначала python -m multiuav sticks map")
    mapping = json.loads(map_path.read_text(encoding="utf-8"))["axes"]
    dev = find_device(device)
    if not profile:
        print("Внимание: профиль модели EdgeTX не указан (--profile) — запишу «не указан».")
    if adc_filter != "off":
        print(f"Внимание: фильтр АЦП в EdgeTX — {adc_filter}; для анализа тремора его стоит выключить.")

    before = {} if keep_fuzz else zero_fuzz_flat(dev)
    info = axes_info(dev)  # уже после обнуления
    for stick, m in mapping.items():
        if m["code"] not in info:
            raise SystemExit(f"У {dev.name} нет оси {m['name']} (код {m['code']}) для «{stick}»: "
                             "другой пульт? Переразметьте: sticks map")

    out = out or Path("runs") / time.strftime("sticks_%Y%m%d_%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    passport = {
        "started_ms": time.time_ns() // 1_000_000,
        "finished_ms": None,
        "host": platform.node(),
        "device": device_passport(dev),
        "axes": {
            stick: {"code": m["code"], "name": axis_name(m["code"]), "inverted": bool(m.get("inverted")),
                    "min": info[m["code"]].min, "max": info[m["code"]].max,
                    "fuzz": info[m["code"]].fuzz, "flat": info[m["code"]].flat,
                    "fuzz_before": before.get(m["code"], (info[m["code"]].fuzz, info[m["code"]].flat))[0],
                    "flat_before": before.get(m["code"], (info[m["code"]].fuzz, info[m["code"]].flat))[1],
                    "resolution": info[m["code"]].resolution}
            for stick, m in mapping.items()
        },
        "edgetx": {"model_profile": profile or "не указан", "adc_filter": adc_filter},
        "note": note,
        "csv": {"t_ms": "time.time_ns() // 1e6 при чтении пачки событий",
                "evdev_us": "метка ядра, мкс, CLOCK_REALTIME",
                "<стик>": "сырое значение оси", "<стик>_us": "1000–2000, вверх/вправо = 2000"},
        "rows": 0,
        "syn_dropped": 0,
        "stopped_by": None,
    }
    _write_json(out / "passport.json", passport)

    ranges = {m["code"]: (info[m["code"]].min, info[m["code"]].max) for m in mapping.values()}
    values = {code: i.value for code, i in info.items()}
    print(f"Пульт: {dev.path}  {dev.name}")
    for stick, a in passport["axes"].items():
        print(f"  {stick:8s} {a['name']:8s} Min {a['min']} Max {a['max']}  "
              f"Fuzz {a['fuzz_before']}→{a['fuzz']}  Flat {a['flat_before']}→{a['flat']}")
    print(f"Пишу {out / 'sticks.csv'} — остановить: Ctrl+C")

    show = sys.stdout.isatty()
    last_show = 0.0
    stopped_by = "Ctrl+C"
    with open(out / "sticks.csv", "w", encoding="utf-8", newline="") as f:
        writer = StickWriter(f, mapping, ranges, values, fsync_each_row)
        writer.report(time.time_ns() // 1_000_000, 0)  # исходное положение
        try:
            for ev in dev.read_loop():
                if ev.type == evdev.ecodes.EV_ABS:
                    writer.abs_event(ev.code, ev.value)
                elif ev.type == evdev.ecodes.EV_SYN and ev.code == evdev.ecodes.SYN_DROPPED:
                    writer.dropped_events()
                elif ev.type == evdev.ecodes.EV_SYN and ev.code == evdev.ecodes.SYN_REPORT:
                    if writer._in_drop:
                        writer.resync({code: i.value for code, i in axes_info(dev).items()})
                    writer.report(time.time_ns() // 1_000_000, ev.sec * 1_000_000 + ev.usec)
                    if show and time.monotonic() - last_show > 0.1:
                        last_show = time.monotonic()
                        line = "  ".join(f"{s} {u:6.1f}" for s, u in writer.us_now().items())
                        print(f"\r{line}  строк {writer.rows}  ", end="", flush=True)
        except KeyboardInterrupt:
            pass
        except OSError as e:  # пульт выдернули
            stopped_by = f"устройство пропало: {e}"
        os.fsync(f.fileno())

    passport.update(finished_ms=time.time_ns() // 1_000_000, rows=writer.rows,
                    syn_dropped=writer.dropped, stopped_by=stopped_by)
    _write_json(out / "passport.json", passport)
    print(f"\nЗаписано строк: {writer.rows}, потерь событий (SYN_DROPPED): {writer.dropped}. "
          f"Остановлено: {stopped_by}.\nЖурнал: {out}")
