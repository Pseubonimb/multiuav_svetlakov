"""Меню эксперимента: python -m multiuav experiment

Одно окно на всю попытку: код участника, затемнения, длительность, проверка пульта
(полоски стиков и значок SE), Старт / Стоп / К воротам / Обработать / Открыть папку.
«Старт» запускает `python -m multiuav launch <папка попытки>/experiment.json` отдельным
процессом и показывает его строки STATUS / FLIGHT / RESULT.
"""

from __future__ import annotations

import json
import os
import queue
import select
import signal
import subprocess
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import colorchooser, filedialog, messagebox, ttk

from .experiment import load_config, new_attempt_dir, parse_occlusion, write_json

REPO = Path(__file__).resolve().parent.parent
STICKS = (("roll", "Крен"), ("pitch", "Тангаж"), ("throttle", "Газ"), ("yaw", "Рыскание"))
GREEN, RED, GRAY, YELLOW = "#2e9e44", "#c0392b", "#9a9a9a", "#e0a800"


class PultWatcher:
    """Фоновое чтение пульта через evdev (без захвата): стики в 1000–2000 и кнопка SE."""

    def __init__(self, map_path: Path) -> None:
        self.map = json.loads(map_path.read_text(encoding="utf-8")) if map_path.exists() else {}
        self.name = ""
        self.connected = False
        self.us: dict[str, float] = {}
        self.se_down = False
        self.se_seen = False
        self.error = ""
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        try:
            import evdev
        except ImportError:
            self.error = ("нет Python-модуля evdev в .venv: "
                          ".venv/bin/pip install -e \".[cosys,gamepad,sticks,plot]\"")
            return
        from .sticklog import axes_info, to_us

        se_code = self.map.get("buttons", {}).get("SE", {}).get("code")
        axes = self.map.get("axes", {})
        while True:
            dev = None
            for path in evdev.list_devices():
                try:
                    d = evdev.InputDevice(path)
                except OSError:
                    continue
                if d.name == self.map.get("device_name") or (
                        not self.map and "radiomaster" in d.name.lower()):
                    dev = d
                    break
            if dev is None:
                self.connected = False
                time.sleep(1.0)
                continue
            self.name, self.connected = dev.name, True
            try:
                info = axes_info(dev)
                values = {c: i.value for c, i in info.items()}
                while True:
                    for stick, m in axes.items():
                        c = m["code"]
                        if c in info:
                            self.us[stick] = to_us(values[c], info[c].min, info[c].max,
                                                   bool(m.get("inverted")))
                    if not select.select([dev.fd], [], [], 0.1)[0]:
                        continue
                    for ev in dev.read():
                        if ev.type == evdev.ecodes.EV_ABS:
                            values[ev.code] = ev.value
                        elif ev.type == evdev.ecodes.EV_KEY and ev.code == se_code and ev.value in (0, 1):
                            self.se_down = ev.value == 1
                            self.se_seen = self.se_seen or self.se_down
            except OSError:
                self.connected = False  # пульт выдернули — ищем заново


class Menu:
    def __init__(self, root: tk.Tk, config_path: Path) -> None:
        self.root = root
        self.cfg = load_config(config_path)
        self.proc: subprocess.Popen | None = None
        self.lines: queue.Queue[str] = queue.Queue()
        self.attempt: Path | None = None
        self.pult = PultWatcher(REPO / self.cfg.get("sticks_map", "radio/sticks_map.json"))
        root.title("Эксперимент: оператор БПЛА")
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        self._build()
        self._tick()

    # ---------------------------------------------------------------- окно

    def _build(self) -> None:
        pad = {"padx": 8, "pady": 3}
        top = ttk.Frame(self.root, padding=10)
        top.pack(fill="both", expand=True)

        lights = ttk.Frame(top)
        lights.grid(row=0, column=0, columnspan=2, sticky="w")
        self.lights = {}
        for i, (key, text) in enumerate((("pult", "Пульт"), ("se", "Кнопка SE"),
                                         ("game", "Игра найдена"), ("track", "Ворота и зоны"))):
            c = tk.Canvas(lights, width=16, height=16, highlightthickness=0)
            c.grid(row=i, column=0, **pad)
            dot = c.create_oval(2, 2, 14, 14, fill=GRAY, outline="")
            lbl = ttk.Label(lights, text=text)
            lbl.grid(row=i, column=1, sticky="w")
            self.lights[key] = (c, dot, lbl, text)

        form = ttk.LabelFrame(top, text="Участник и условия", padding=8)
        form.grid(row=1, column=0, sticky="nsew", **pad)
        self.code = tk.StringVar(value=self.cfg.get("participant", ""))
        self.occlusion = tk.StringVar(value=",".join(str(v) for v in self.cfg.get("occlusion_ms", [])))
        self.minutes = tk.StringVar(value=f"{self.cfg.get('duration_s', 1800) / 60:g}")
        for r, (text, var, width) in enumerate((("Код участника", self.code, 16),
                                                ("Затемнение перед воротами, мс", self.occlusion, 24),
                                                ("Длительность, мин", self.minutes, 8))):
            ttk.Label(form, text=text).grid(row=r, column=0, sticky="w", **pad)
            ttk.Entry(form, textvariable=var, width=width).grid(row=r, column=1, sticky="w", **pad)
        self.levels = {k: v["title"] for k, v in self.cfg.get("levels", {}).items()}
        self.level = tk.StringVar(value=self.levels.get(self.cfg.get("level", "hard"), ""))
        ttk.Label(form, text="Уровень сложности").grid(row=7, column=0, sticky="w", **pad)
        ttk.Combobox(form, textvariable=self.level, values=list(self.levels.values()),
                     state="readonly", width=44).grid(row=7, column=1, sticky="w", **pad)
        self.level.trace_add("write", lambda *_: self._on_level())
        self.directions = self._directions()
        self.direction = tk.StringVar(value=self.directions.get(self._direction_key(self.cfg.get("direction", "cw")), ""))
        ttk.Label(form, text="Направление по трассе").grid(row=6, column=0, sticky="w", **pad)
        ttk.Combobox(form, textvariable=self.direction, values=list(self.directions.values()),
                     state="readonly", width=34).grid(row=6, column=1, sticky="w", **pad)
        self.color = self.cfg.get("occlusion_color", "#969696")
        self.image = tk.StringVar(value=self.cfg.get("occlusion_image", ""))
        ttk.Label(form, text="Цвет затемнения").grid(row=3, column=0, sticky="w", **pad)
        look = ttk.Frame(form)
        look.grid(row=3, column=1, sticky="w", **pad)
        self.swatch = tk.Button(look, width=4, bg=self.color, activebackground=self.color,
                                command=self.pick_color)
        self.swatch.pack(side="left")
        self.color_text = ttk.Label(look, text=self.color)
        self.color_text.pack(side="left", padx=6)
        ttk.Label(form, text="Картинка вместо цвета").grid(row=4, column=0, sticky="w", **pad)
        pic = ttk.Frame(form)
        pic.grid(row=4, column=1, sticky="w", **pad)
        ttk.Entry(pic, textvariable=self.image, width=22).pack(side="left")
        ttk.Button(pic, text="Выбрать…", command=self.pick_image).pack(side="left", padx=3)
        ttk.Button(pic, text="Без картинки", command=lambda: self.image.set("")).pack(side="left")
        ttk.Label(form, text="Режим и модель").grid(row=5, column=0, sticky="w", **pad)
        ttk.Label(form, text=f"{self.cfg.get('mode', 'acro').capitalize()} · "
                             f"{self.cfg.get('model', '')} · сопротивление "
                             f"{str(self.cfg.get('rotor_drag', '')).replace('.', ',')}"
                  ).grid(row=5, column=1, sticky="w", **pad)

        extra = ttk.LabelFrame(top, text="Дополнительно", padding=8)
        extra.grid(row=6, column=0, columnspan=2, sticky="ew", **pad)
        self.tilt = tk.StringVar(value=f"{self.cfg.get('camera_tilt_deg', 20):g}")
        ttk.Label(extra, text="Наклон камеры вверх, °").grid(row=0, column=0, sticky="w", **pad)
        ttk.Spinbox(extra, from_=0, to=60, increment=5, textvariable=self.tilt, width=6
                    ).grid(row=0, column=1, sticky="w", **pad)
        ttk.Label(extra, text="0 — камера смотрит прямо; на гоночных дронах обычно 20–40°: "
                              "на скорости дрон наклонён вперёд, а камера смотрит вдоль полёта"
                  ).grid(row=0, column=2, sticky="w", **pad)

        task = ttk.LabelFrame(top, text="Задача пилота", padding=8)
        task.grid(row=1, column=1, sticky="nsew", **pad)
        ttk.Label(task, text=self.cfg.get("task", ""), wraplength=340, justify="left",
                  font=("TkDefaultFont", 11)).pack(anchor="w")

        bars = ttk.LabelFrame(top, text="Проверка пульта", padding=8)
        bars.grid(row=2, column=0, columnspan=2, sticky="ew", **pad)
        self.bars = {}
        for i, (key, text) in enumerate(STICKS):
            ttk.Label(bars, text=text, width=9).grid(row=i, column=0, sticky="w")
            c = tk.Canvas(bars, width=420, height=14, bg="#e8e8e8", highlightthickness=0)
            c.grid(row=i, column=1, pady=2)
            c.create_line(210, 0, 210, 14, fill="#b0b0b0")
            bar = c.create_rectangle(210, 2, 210, 12, fill="#3b7dd8", outline="")
            val = ttk.Label(bars, text="—", width=6)
            val.grid(row=i, column=2, padx=6)
            self.bars[key] = (c, bar, val)

        buttons = ttk.Frame(top)
        buttons.grid(row=3, column=0, columnspan=2, sticky="ew", **pad)
        self.btn_start = ttk.Button(buttons, text="Старт", command=self.start)
        self.btn_stop = ttk.Button(buttons, text="Стоп", command=self.stop, state="disabled")
        self.btn_reset = ttk.Button(buttons, text="К воротам (R)", command=self.reset,
                                    state="disabled")
        self.btn_analyze = ttk.Button(buttons, text="Обработать попытку", command=self.analyze)
        self.btn_open = ttk.Button(buttons, text="Открыть папку", command=self.open_folder)
        for b in (self.btn_start, self.btn_stop, self.btn_reset, self.btn_analyze, self.btn_open):
            b.pack(side="left", padx=4)

        self.flight_time = ttk.Label(top, text="", font=("TkDefaultFont", 14, "bold"))
        self.flight_time.grid(row=4, column=0, columnspan=2, sticky="w", **pad)
        self.log = tk.Text(top, height=9, width=92, state="disabled", wrap="word")
        self.log.grid(row=5, column=0, columnspan=2, sticky="nsew", **pad)
        top.columnconfigure(1, weight=1)
        top.rowconfigure(5, weight=1)

    def _light(self, key: str, color: str, text: str | None = None) -> None:
        c, dot, lbl, default = self.lights[key]
        c.itemconfigure(dot, fill=color)
        lbl.configure(text=text or default)

    def say(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    # ---------------------------------------------------------------- опрос

    def _tick(self) -> None:
        p = self.pult
        if p.connected:
            self._light("pult", GREEN, f"Пульт: {p.name}")
        else:
            self._light("pult", RED, "Пульт не найден — подключите по USB" + (f" ({p.error})" if p.error else ""))
        if p.se_down:
            self._light("se", YELLOW, "Кнопка SE нажата")
        elif p.se_seen:
            self._light("se", GREEN, "Кнопка SE работает")
        else:
            self._light("se", GRAY, "Кнопка SE: нажмите для проверки")
        game_ok = (Path(os.path.expanduser(self.cfg["engine"])).exists()
                   and Path(os.path.expanduser(self.cfg["project"])).exists())
        self._light("game", GREEN if game_ok else RED,
                    "Игра найдена" if game_ok else "Нет движка или проекта (см. experiments/friday.json)")
        track_ok = (REPO / self.cfg.get("track", "")).exists()
        self._light("track", GREEN if track_ok else RED,
                    "Ворота и зоны: " + self.cfg.get("track", "") if track_ok else "Нет файла ворот")
        for key, (c, bar, val) in self.bars.items():
            us = p.us.get(key)
            if us is None:
                continue
            x = 210 + (us - 1500) / 500 * 205
            c.coords(bar, min(210, x), 2, max(210, x), 12)
            val.configure(text=f"{us:.0f}")
        while not self.lines.empty():
            self._line(self.lines.get_nowait())
        if self.proc is not None and self.proc.poll() is not None and self.lines.empty():
            self._finished()
        self.root.after(50, self._tick)

    def _line(self, line: str) -> None:
        if line.startswith("STATUS "):
            self.say(line[7:])
        elif line.startswith("FLIGHT "):
            t, total = (float(v) for v in line.split()[1:3])
            mm = lambda s: f"{int(s) // 60:02d}:{int(s) % 60:02d}"  # noqa: E731
            self.flight_time.configure(text=f"Полёт: {mm(t)} / {mm(total)}")
        elif line.startswith("RESULT "):
            r = json.loads(line[7:])
            if r.get("ok"):
                self.say(f"Попытка записана: {r.get('flight_s')} с полёта, касаний {r.get('collisions')}, "
                         f"сбросов {r.get('resets')}. Остановлено: {r.get('stopped_by')}.")
            else:
                self.say(f"Попытка не удалась: {r.get('error')}")
        elif line.strip():
            self.say("  " + line.rstrip())

    def _finished(self) -> None:
        self.proc = None
        self.btn_start.configure(state="normal")
        self.btn_stop.configure(state="disabled")
        self.btn_reset.configure(state="disabled")
        self.say("Конец. Можно нажать «Обработать попытку».")

    # ---------------------------------------------------------------- кнопки

    def _level_key(self) -> str:
        return next((k for k, v in self.levels.items() if v == self.level.get()),
                    self.cfg.get("level", "hard"))

    def _on_level(self) -> None:
        """Сменили уровень — своя карта и свой файл ворот (и число ворот в подписи)."""
        from .experiment import apply_level

        key = next((k for k, v in self.directions.items() if v == self.direction.get()), "cw")
        self.cfg = apply_level(self.cfg, self._level_key())
        self.directions = self._directions()
        self.direction.set(self.directions.get(self._direction_key(key), ""))

    def _direction_key(self, key: str) -> str:
        """cw/ccw ↔ forward/reverse — то же направление в файле трассы другого уровня."""
        from .track import Track

        try:
            data = json.loads((REPO / self.cfg["track"]).read_text(encoding="utf-8"))
        except (OSError, KeyError, ValueError):
            return key
        return Track.direction_key(data, key)

    def _directions(self) -> dict[str, str]:
        """Направления из файла трассы → подпись (и сколько там ворот)."""
        try:
            data = json.loads((REPO / self.cfg["track"]).read_text(encoding="utf-8"))
        except (OSError, KeyError, ValueError):
            return {"cw": "по часовой", "ccw": "против часовой"}
        return {k: f"{d.get('title', k)} (ворот: {len(d.get('gates', []))})"
                for k, d in data.get("directions", {"cw": {}}).items()}

    def pick_color(self) -> None:
        _, color = colorchooser.askcolor(color=self.color, title="Цвет затемнения")
        if color:
            self.color = color.lower()
            self.swatch.configure(bg=self.color, activebackground=self.color)
            self.color_text.configure(text=self.color)

    def pick_image(self) -> None:
        path = filedialog.askopenfilename(title="Картинка затемнения",
                                          filetypes=[("Картинки", "*.png *.jpg *.jpeg *.bmp *.tga"),
                                                     ("Все файлы", "*")])
        if path:
            self.image.set(path)

    def start(self) -> None:
        if self.proc is not None:
            return
        try:
            occlusion = parse_occlusion(self.occlusion.get())
            minutes = float(self.minutes.get().replace(",", "."))
            if minutes <= 0:
                raise ValueError("длительность должна быть больше нуля")
            tilt = float(self.tilt.get().replace(",", "."))
            if not 0 <= tilt <= 60:
                raise ValueError("наклон камеры — от 0 до 60°")
        except ValueError as e:
            messagebox.showerror("Ошибка", f"Проверьте поля: {e}")
            return
        if not self.code.get().strip():
            messagebox.showerror("Ошибка", "Введите код участника")
            return
        if not self.pult.connected:
            if not messagebox.askyesno("Пульт", "Пульт не найден. Запустить всё равно?"):
                return
        image = self.image.get().strip()
        if image and not Path(os.path.expanduser(image)).exists():
            messagebox.showerror("Ошибка", f"Нет файла картинки: {image}")
            return
        self.attempt = new_attempt_dir(self.code.get(), REPO / "runs")
        cfg = {**self.cfg, "participant": self.code.get().strip(), "occlusion_ms": occlusion,
               "occlusion_color": self.color, "occlusion_image": image, "camera_tilt_deg": tilt,
               "direction": next((k for k, v in self.directions.items() if v == self.direction.get()),
                                 "cw"),
               "level": self._level_key(),
               "duration_s": round(minutes * 60), "out": str(self.attempt)}
        write_json(self.attempt / "experiment.json", cfg)
        self.say(f"Старт: {self.attempt.relative_to(REPO)}")
        self.flight_time.configure(text="")
        self.proc = subprocess.Popen(
            [sys.executable, "-u", "-m", "multiuav", "launch", str(self.attempt / "experiment.json")],
            cwd=REPO, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
            stdin=subprocess.DEVNULL, start_new_session=True)
        threading.Thread(target=self._read, args=(self.proc,), daemon=True).start()
        self.btn_start.configure(state="disabled")
        self.btn_stop.configure(state="normal")
        self.btn_reset.configure(state="normal")

    def _read(self, proc: subprocess.Popen) -> None:
        for line in proc.stdout:
            self.lines.put(line.rstrip("\n"))

    def stop(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            self.say("Стоп: заканчиваю полёт и закрываю игру…")
            self.proc.send_signal(signal.SIGINT)

    def reset(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            self.proc.send_signal(signal.SIGUSR1)

    def _last_attempt(self) -> Path | None:
        if self.attempt is not None:
            return self.attempt
        runs = sorted((REPO / "runs").glob("*/*/experiment.json"), key=lambda p: p.stat().st_mtime)
        return runs[-1].parent if runs else None

    def analyze(self) -> None:
        folder = self._last_attempt()
        if folder is None or not (folder / "drone0.csv").exists():
            messagebox.showinfo("Обработка", "Нет записанной попытки с drone0.csv")
            return
        self.say(f"Обработка {folder.relative_to(REPO)}…")

        def work():
            from .analyze import analyze

            try:
                cwd = os.getcwd()
                os.chdir(REPO)  # пути в experiment.json — от корня репозитория
                try:
                    a = analyze(folder)
                finally:
                    os.chdir(cwd)
                text = (f"Итог: кругов {a['laps']}, ворот пройдено {a['gates_passed']} "
                        f"(мимо проёма {a['gates_outside']}, пропущено {a['gates_skipped']}), "
                        f"промах средний {a['miss_mean_m']} м, медиана {a['miss_median_m']} м; "
                        f"нажатий SE {a['presses']} (у ворот {a['presses_matched']}), "
                        f"средний |dt| {a['abs_dt_mean_ms']} мс; касаний {a['contacts']}, "
                        f"сбросов {a['resets']}, затемнений {a['occlusions']}.")
            except Exception as e:
                text = f"Обработка не удалась: {e}"
            self.lines.put("STATUS " + time.strftime("%H:%M:%S") + " " + text)

        threading.Thread(target=work, daemon=True).start()

    def open_folder(self) -> None:
        folder = self._last_attempt() or (REPO / "runs")
        subprocess.Popen(["xdg-open", str(folder)])

    def on_close(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            if not messagebox.askyesno("Выход", "Идёт попытка. Остановить её и выйти?"):
                return
            self.stop()
            try:
                self.proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                pass
        self.root.destroy()


def run_menu(config: Path) -> None:
    config = config.resolve()
    os.chdir(REPO)
    root = tk.Tk()
    Menu(root, config)
    root.lift()  # поверх остальных окон при запуске, дальше — как обычное окно
    root.attributes("-topmost", True)
    root.after(800, lambda: root.attributes("-topmost", False))
    root.mainloop()
