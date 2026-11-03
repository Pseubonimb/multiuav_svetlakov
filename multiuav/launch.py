"""Запуск попытки целиком: settings.json → игра → запись пульта → полёт → всё закрыть.

  python -m multiuav launch experiments/friday.json --participant P07
  python -m multiuav launch runs/P07/2026-11-06_16-05-01/experiment.json   (так зовёт меню)

Если файл лежит не в папке попытки (нет ключа "out"), папка создаётся:
runs/<код>/<ГГГГ-ММ-ДД_ЧЧ-ММ-СС>/. Остановить: Ctrl+C или SIGINT (кнопка «Стоп» меню);
SIGUSR1 — вернуть дрон к последним воротам (кнопка меню), то же — клавиша R.

Для меню печатаются строки: «STATUS ЧЧ:ММ:СС текст», «FLIGHT t_s duration_s»,
в конце «RESULT {json}».
"""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

from .experiment import (TrialRecorder, local_time, new_attempt_dir, parse_occlusion,
                         write_json)
from .track import Track, expand_cyclic

PORT = 41451


def status(text: str) -> None:
    print(f"STATUS {time.strftime('%H:%M:%S')} {text}", flush=True)


def port_open(port: int = PORT) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def stop_process(proc: subprocess.Popen | None, name: str, sig=signal.SIGTERM,
                 wait_s: float = 10.0) -> None:
    """Закрыть по PID: сначала вежливо (sig), через wait_s — kill -9."""
    if proc is None or proc.poll() is not None:
        return
    try:
        proc.send_signal(sig)
        proc.wait(timeout=wait_s)
    except subprocess.TimeoutExpired:
        status(f"{name} не закрылся за {wait_s:.0f} с — kill -9")
        proc.kill()
        proc.wait(timeout=5)


def unreal_command(cfg: dict) -> list[str]:
    engine = os.path.expanduser(cfg["engine"])
    project = os.path.expanduser(cfg["project"])
    # -OcclusionMs не передаём: затемнением управляет MultiUAV (occlusion.py), триггеры
    # Виталия в карте выключены.
    cmd = [engine, project, cfg["map"], "-game", "-nosplash",
           f"-ExecCmds=scalability {cfg.get('quality', 1)}, DisableAllScreenMessages"]
    if cfg.get("fullscreen", True):
        cmd += ["-fullscreen"]
    else:
        cmd += ["-windowed", "-ResX=1600", "-ResY=900", "-WinX=0", "-WinY=0"]
    return cmd


def apply_occlusion_look(cfg: dict) -> bool:
    """Цвет или картинка затемнения поменялись — записать их в карту (Unreal без окна, ~20 с).

    Возвращает True, если карту пришлось менять.
    """
    track_file = Path(cfg["track"])
    current = json.loads(track_file.read_text(encoding="utf-8")).get("occlusion", {})
    color = str(cfg.get("occlusion_color") or current.get("color") or "#969696").lower()
    image = os.path.abspath(os.path.expanduser(cfg["occlusion_image"])) if cfg.get("occlusion_image") else None
    if image and not Path(image).exists():
        raise RuntimeError(f"нет картинки затемнения {image}")
    if color == str(current.get("color", "")).lower() and image == current.get("image"):
        return False
    status("применяю цвет/картинку затемнения к карте (Unreal без окна, ~20 с)")
    engine = Path(os.path.expanduser(cfg["engine"]))
    cmd_tool = engine.with_name("UnrealEditor-Cmd")
    script = Path(__file__).resolve().parent.parent / "tools" / "unreal" / "friday_track.py"
    args = f"{script} --occlusion-only --color {color} --out {track_file.resolve()}"
    if image:
        args += f" --image '{image}'"
    out = Path(cfg["out"]) / "occlusion_apply.log"
    with open(out, "w", encoding="utf-8") as log:
        code = subprocess.call([str(cmd_tool), os.path.expanduser(cfg["project"]), "-run=pythonscript",
                                f"-script={args}", "-nullrhi", "-unattended", "-nosplash", "-nop4"],
                               stdout=log, stderr=subprocess.STDOUT)
    current = json.loads(track_file.read_text(encoding="utf-8")).get("occlusion", {})
    ok = "Python script executed successfully" in out.read_text(encoding="utf-8", errors="replace")
    if not ok or str(current.get("color", "")).lower() != color:
        raise RuntimeError(f"не удалось применить затемнение к карте, см. {out}")
    return True


def _game_env() -> dict:
    env = dict(os.environ)
    # Те же переменные, что в alias ue5: рисует дискретная NVIDIA (гибридная графика).
    env.update(__NV_PRIME_RENDER_OFFLOAD="1", __GLX_VENDOR_LIBRARY_NAME="nvidia")
    return env


def run_launch(config: Path, participant: str | None = None, backend_name: str = "cosys",
               no_game: bool = False, no_sticks: bool = False) -> dict:
    from .experiment import load_config

    cfg = load_config(config)
    if participant:
        cfg["participant"] = participant
    if cfg.get("out"):
        out = Path(cfg["out"])
    else:
        out = new_attempt_dir(cfg.get("participant", ""))
        cfg["out"] = str(out)
    out.mkdir(parents=True, exist_ok=True)
    cfg["direction"] = cfg.get("direction", "cw")
    track = Track.load(cfg["track"], cfg["direction"]) if cfg.get("track") else None
    cfg["occlusion_ms"] = parse_occlusion(cfg.get("occlusion_ms", []))
    zones = len(track.zones) if track else 0
    cfg["occlusion_ms_by_zone"] = expand_cyclic(cfg["occlusion_ms"], zones)
    cfg["backend"] = backend_name
    cfg["started_ms"] = time.time_ns() // 1_000_000
    cfg["started"] = local_time(cfg["started_ms"])
    cfg["finished"] = cfg["finished_ms"] = None
    cfg["stopped_by"] = None
    write_json(out / "experiment.json", cfg)
    status(f"попытка {cfg.get('participant')}: {out}")

    stop = threading.Event()
    resets = {"menu": 0}

    def on_signal(signum, _frame):
        if signum == signal.SIGUSR1:
            resets["menu"] += 1
        else:
            stop.set()
            raise KeyboardInterrupt

    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGUSR1, on_signal)

    game = sticks = None
    result = {"out": str(out), "ok": False}
    recorder = None
    try:
        sc = _scenario(cfg)
        if backend_name == "cosys":
            from .cosys_settings import write_settings

            write_settings(sc)
            status("settings.json записан")
            if not no_game and track is not None:
                if port_open():
                    raise RuntimeError("порт 41451 уже занят: открыт редактор или другая игра. "
                                       "Закройте её и нажмите Старт снова")
                apply_occlusion_look(cfg)
            if not no_game:
                if port_open():
                    raise RuntimeError("порт 41451 уже занят: открыт редактор или другая игра. "
                                       "Закройте её и нажмите Старт снова")
                log = open(out / "unreal.log", "w", encoding="utf-8")
                game = subprocess.Popen(unreal_command(cfg), stdout=log, stderr=subprocess.STDOUT,
                                        env=_game_env(), start_new_session=True)
                status(f"игра запускается (PID {game.pid})")
            _wait_game(game, int(cfg.get("unreal_timeout_s", 180)), stop)
            status("Cosys ответил")
            if track is not None:
                _park_other_gates(cfg)

        if not no_sticks:
            sticks = _start_sticks(cfg, out)
            status("запись пульта идёт")

        from .manager import SwarmManager
        from .xkeys import GlobalKey

        with GlobalKey(cfg.get("reset_key", "r")) as key:
            if not key.available:
                status(f"клавиша {cfg.get('reset_key', 'r').upper()} не перехвачена: {key.error}; "
                       "сброс — кнопкой меню")

            def reset_requests() -> str:
                if key.available and key.pressed():
                    return "клавиша R"
                if resets["menu"]:
                    resets["menu"] = 0
                    return "меню"
                return ""

            duration = cfg.get("duration_s")

            def on_tick(t_s: float) -> None:
                print(f"FLIGHT {t_s:.0f} {duration or 0}", flush=True)

            def alive() -> bool:
                return game is None or game.poll() is None

            occluder = screen = None
            if track is not None and track.zones:
                from .experiment import _CsvLog
                from .occlusion import EVENT_COLUMNS, CosysScreen, LogScreen, Occluder

                events = _CsvLog(out / "occlusion_events.csv", EVENT_COLUMNS)
                info = json.loads(Path(cfg["track"]).read_text(encoding="utf-8")).get("occlusion", {})
                if backend_name == "cosys":
                    screen = CosysScreen(info.get("volume", "MU_Occlusion"),
                                         info.get("park_ned", [0, 0, 10000]), events)
                    if screen.error:
                        raise RuntimeError(f"затемнение не готово: {screen.error}")
                else:
                    screen = LogScreen(events)
                occluder = Occluder(track, cfg["occlusion_ms_by_zone"], screen)
            recorder = TrialRecorder(out, track, duration, reset_requests, on_tick, alive, occluder)
            backend = _backend(backend_name)
            states = backend.states
            backend.states = lambda: (recorder.stamp(), states())[1]
            status("полёт: управление у пульта" + (", R — к последним воротам" if track else ""))
            manager = SwarmManager(sc, backend, step_hook=recorder, stop=stop)
            try:
                manager.run()
            except KeyboardInterrupt:
                pass
            if not recorder.stopped_by:
                recorder.stopped_by = "Стоп" if stop.is_set() else "полёт закончен"
            recorder.flight_s = recorder.t_s()
            _goodbye(backend)
        result["ok"] = True
    except KeyboardInterrupt:
        result["error"] = "остановлено до начала полёта"
    except Exception as e:  # понятное сообщение в меню, подробности — в логах
        crashed = game is not None and game.poll() is not None
        result["error"] = "игра упала, см. unreal.log" if crashed else str(e)
        status(f"ОШИБКА: {result['error']}")
    finally:
        signal.signal(signal.SIGINT, signal.SIG_IGN)  # второй «Стоп» не должен оборвать уборку
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        if recorder is not None:
            if recorder.occluder is not None:
                recorder.occluder.screen.close()
                recorder.occluder.screen.log.close()
            recorder.close()
        stop_process(sticks, "запись пульта", signal.SIGINT, 5)
        if game is not None:
            status("закрываю игру")
            if game.poll() is not None:
                result.setdefault("warning", f"игра завершилась сама, код {game.returncode}")
            stop_process(game, "игра", signal.SIGTERM, 10)
        cfg["finished_ms"] = time.time_ns() // 1_000_000
        cfg["finished"] = local_time(cfg["finished_ms"])
        cfg["stopped_by"] = (recorder.stopped_by if recorder and recorder.stopped_by
                             else result.get("error", ""))
        write_json(out / "experiment.json", cfg)
        summary = {
            "participant": cfg.get("participant"),
            "started": cfg["started"], "finished": cfg["finished"],
            "flight_s": round(getattr(recorder, "flight_s", recorder.t_s()), 1) if recorder else 0.0,
            "stopped_by": cfg["stopped_by"],
            "rows": recorder.flight.rows if recorder else 0,
            "collisions": recorder.collisions.rows if recorder else 0,
            "resets": recorder.resets.rows if recorder else 0,
        }
        if (out / "summary.json").exists():  # повторный запуск analyze не теряет
            old = json.loads((out / "summary.json").read_text(encoding="utf-8"))
            summary = {**old, **summary}
        write_json(out / "summary.json", summary)
        result.update(summary)
        status("готово")
        print("RESULT " + json.dumps(result, ensure_ascii=False), flush=True)
    return result


def _scenario(cfg: dict):
    from .scenario import AlgorithmSpec, Scenario

    sc = Scenario.load(cfg["scenario"])
    sc.model = str(cfg.get("model", sc.model)).lower()
    sc.rotor_drag = float(cfg.get("rotor_drag", sc.rotor_drag))
    params = {**sc.leader.params, "mode": cfg.get("mode", "acro")}
    if cfg.get("sticks_map"):
        params["sticks_map"] = cfg["sticks_map"]
    sc.leader = AlgorithmSpec(sc.leader.name, params)
    sc.duration_s = None  # конец — по часам, в TrialRecorder
    if cfg.get("camera_tilt_deg") is not None:
        # Наклон FPV-камеры вверх (uptilt), как на гоночных дронах: на скорости дрон наклонён
        # вперёд, а камера смотрит вдоль полёта.
        sc.fpv_camera = {**(sc.fpv_camera or {}), "pitch_deg": float(cfg["camera_tilt_deg"])}
    if cfg.get("track"):
        import math

        sc.heading = Track.load(cfg["track"], cfg.get("direction", "cw")).start_heading
        sc.heading = math.remainder(sc.heading, 2 * math.pi)
    return sc


def _park_other_gates(cfg: dict) -> None:
    """Ворота другого направления — под карту, на всё время игры (рамка едет за точкой)."""
    from .track import gate_actors

    data = json.loads(Path(cfg["track"]).read_text(encoding="utf-8"))
    other = "ccw" if cfg["direction"] == "cw" else "cw"
    names = gate_actors(data, other)
    if not names:
        return
    import cosysairsim as airsim

    client = airsim.MultirotorClient()
    client.confirmConnection()
    for k, name in enumerate(names):
        pose = airsim.Pose(airsim.Vector3r(0.0, 50.0 * k, 9000.0), airsim.Quaternionr())
        if not client.simSetObjectPose(name, pose, True):
            status(f"не удалось убрать ворота {name} другого направления")
    title = data.get("directions", {}).get(cfg["direction"], {}).get("title", cfg["direction"])
    status(f"направление: {title}; ворота другого направления убраны")


def _backend(name: str):
    if name == "kinematic":
        from .backends.kinematic import KinematicBackend

        backend = KinematicBackend()
        backend.realtime = True  # проверка без Unreal, но в реальном времени, как полёт
        return backend
    from .backends.cosys import CosysBackend

    return CosysBackend()


def _wait_game(game: subprocess.Popen | None, timeout_s: int, stop: threading.Event) -> None:
    status(f"жду Cosys (порт {PORT}, до {timeout_s} с)")
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline and not stop.is_set():
        if game is not None and game.poll() is not None:
            raise RuntimeError(f"игра упала при запуске (код {game.returncode}), см. unreal.log")
        if port_open():
            time.sleep(2.0)  # порт открылся раньше, чем дрон появился в мире
            return
        time.sleep(1.0)
    raise RuntimeError(f"Cosys не ответил за {timeout_s} с, см. unreal.log")


def _start_sticks(cfg: dict, out: Path) -> subprocess.Popen:
    cmd = [sys.executable, "-m", "multiuav", "sticks", "log", "--out", str(out),
           "--map", cfg.get("sticks_map", "radio/sticks_map.json"),
           "--profile", cfg.get("edgetx_profile", "не указан"),
           "--adc-filter", cfg.get("adc_filter", "unknown"),
           "--note", f"участник {cfg.get('participant')}"]
    log = open(out / "sticks.log", "w", encoding="utf-8")
    proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    for _ in range(30):  # до 3 с: пульт нашёлся и паспорт записан
        if proc.poll() is not None:
            text = (out / "sticks.log").read_text(encoding="utf-8").strip().splitlines()
            raise RuntimeError("пульт не найден: " + (text[-1] if text else "запись пульта не запустилась"))
        if (out / "passport.json").exists() and (out / "sticks.csv").exists():
            return proc
        time.sleep(0.1)
    return proc


def _goodbye(backend) -> None:
    """Надпись END OF EXPERIMENT перед дроном на 3 с (если игра ещё жива).
    Латиницей: отладочный шрифт Unreal кириллицу не рисует."""
    client = getattr(backend, "client", None)
    if client is None:
        return
    try:
        import math

        import cosysairsim as airsim

        from .backends.cosys import _yaw_from_quaternion

        k = client.simGetGroundTruthKinematics("Drone0")
        yaw = _yaw_from_quaternion(k.orientation)

        p = k.position
        ahead = airsim.Vector3r(p.x_val + 4 * math.cos(yaw), p.y_val + 4 * math.sin(yaw), p.z_val)
        client.simPlotStrings(["END OF EXPERIMENT"], [ahead], 2.0, [1.0, 1.0, 1.0, 1.0], 3.0)
        client.hoverAsync(vehicle_name="Drone0")
        time.sleep(3.0)
    except Exception:
        pass
