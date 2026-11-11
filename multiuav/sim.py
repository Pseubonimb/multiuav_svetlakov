"""Один исследовательский прогон целиком: settings.json → Unreal → камеры → полёт → всё закрыть.

  python -m multiuav sim scenarios/rc_group.yaml --world city --followers 4 --out runs/city1
  python -m multiuav sim scenarios/corner.yaml --backend kinematic --leader waypoints
  python -m multiuav sim scenarios/x.yaml --project ~/UE/MyWorld/MyWorld.uproject --map /Game/Maps/Field
  python -m multiuav sim --list-worlds | --list-maps ПРОЕКТ.uproject | --save-world ИМЯ --project … --map …

Мир — это проект Unreal (.uproject, где угодно на диске) + карта в нём (/Game/…). Готовые миры —
в experiments/worlds.json, свои — в experiments/local.json (ключ "worlds"). В проекте должен
быть плагин AirSim, а у карты — GameMode AirSimGameMode.

Режим игры (по умолчанию): Unreal открывается с -game и закрывается в конце. --editor: открыть
редактор (если порт 41451 ещё не занят) и ждать, пока человек нажмёт Play; редактор не закрывается.

Всё складывается в одну папку (--out или runs/sim_<время>/): track.csv, summary.json, track.png,
sim.json (с какими настройками запускали), копии сценария и settings.json, unreal.log,
cameras.log, sticks.csv/passport.json (если --sticks). Для меню печатаются строки
«STATUS ЧЧ:ММ:СС текст» и в конце «RESULT {json}». Остановить: Ctrl+C или SIGINT/SIGTERM.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from .unreal import (PORT, editor_command, game_command, game_env, port_open, status,
                     stop_process, wait_game)

def local_time() -> str:
    """Время компьютера: «2026-11-12 16:05:01.123»."""
    ms = time.time_ns() // 1_000_000
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ms / 1000)) + f".{ms % 1000:03d}"


def write_json(path: Path, data: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)  # файл не окажется записанным наполовину


WORLDS = Path("experiments/worlds.json")
LOCAL = Path("experiments/local.json")


def load_worlds(path: Path = WORLDS, local: Path = LOCAL) -> dict:
    """{"engine": …, "worlds": {имя: {title, project, map, scenario}}}; local.json перекрывает."""
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    data.setdefault("worlds", {})
    if local.exists():
        mine = json.loads(local.read_text(encoding="utf-8"))
        data["worlds"].update(mine.get("worlds", {}))
        if mine.get("engine"):
            data["engine"] = mine["engine"]
    data.setdefault("engine", "~/Downloads/UnrealEngine5/Engine/Binaries/Linux/UnrealEditor")
    return data


def save_world(name: str, project: str, map_path: str, title: str = "", scenario: str = "",
               local: Path = LOCAL) -> dict:
    """Запомнить свой мир на этом компьютере (experiments/local.json, не в git)."""
    project = str(Path(os.path.expanduser(project)).resolve())
    if not Path(project).is_file() or not project.endswith(".uproject"):
        raise SystemExit(f"нет файла проекта {project}")
    if map_path not in project_maps(project):
        raise SystemExit(f"в проекте нет карты {map_path}; есть: {', '.join(project_maps(project))}")
    data = json.loads(local.read_text(encoding="utf-8")) if local.exists() else {}
    world = {"title": title or name, "project": project, "map": map_path}
    if scenario:
        world["scenario"] = scenario
    data.setdefault("worlds", {})[name] = world
    local.parent.mkdir(parents=True, exist_ok=True)
    write_json(local, data)
    return world


def project_maps(project: str | Path) -> list[str]:
    """Карты проекта: Content/A/B.umap → /Game/A/B (без служебных папок World Partition)."""
    content = Path(os.path.expanduser(str(project))).parent / "Content"
    maps = []
    for f in sorted(content.rglob("*.umap")):
        rel = f.relative_to(content).with_suffix("")
        if any(part.startswith("__") for part in rel.parts):
            continue
        maps.append("/Game/" + rel.as_posix())
    return maps


def resolve_world(world: str | None, project: str | None, map_path: str | None,
                  worlds: dict) -> dict:
    """--world ИМЯ или --project + --map → {"title", "project", "map", "scenario"?}."""
    if world:
        if world not in worlds["worlds"]:
            raise SystemExit(f"нет мира «{world}»; есть: {', '.join(worlds['worlds'])} "
                             "(свой — sim --save-world)")
        w = dict(worlds["worlds"][world])
        w["name"] = world
    else:
        if not project or not map_path:
            raise SystemExit("для Unreal нужен --world ИМЯ или --project ПРОЕКТ.uproject --map /Game/…")
        w = {"name": "", "title": Path(map_path).name, "project": project, "map": map_path}
    if project:
        w["project"] = project
    if map_path:
        w["map"] = map_path
    w["project"] = str(Path(os.path.expanduser(w["project"])))
    if not Path(w["project"]).is_file():
        raise SystemExit(f"нет файла проекта {w['project']}")
    maps = project_maps(w["project"])
    if w["map"] not in maps:
        raise SystemExit(f"в проекте {w['project']} нет карты {w['map']}; есть: {', '.join(maps)}")
    return w


def screen_size() -> tuple[int, int]:
    """Размер экрана для окна камер при игре на весь экран."""
    try:
        out = subprocess.run(["xrandr", "--current"], capture_output=True, text=True, timeout=3).stdout
        m = re.search(r"current (\d+) x (\d+)", out)
        if m:
            return int(m.group(1)), int(m.group(2))
    except (OSError, subprocess.SubprocessError):
        pass
    return 1920, 1080


def _apply(sc, args) -> None:
    """Флаги поверх сценария — те же, что у run."""
    from .scenario import AlgorithmSpec

    if args.followers is not None:
        sc.drones = args.followers + 1
    if args.algorithm:
        sc.followers = AlgorithmSpec(args.algorithm, sc.followers.params)
    if args.leader:
        sc.leader = AlgorithmSpec(args.leader, sc.leader.params)
    if args.mode:
        sc.leader = AlgorithmSpec(sc.leader.name, {**sc.leader.params, "mode": args.mode})
    if args.model:
        sc.model = args.model.lower()
    if args.rotor_drag is not None:
        sc.rotor_drag = args.rotor_drag
    if args.duration is not None:
        sc.duration_s = args.duration


def _start_logged(cmd: list[str], log_path: Path, env: dict | None = None) -> subprocess.Popen:
    log = open(log_path, "w", encoding="utf-8")
    return subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, env=env,
                            start_new_session=True)


def run_sim(args) -> dict:
    from .scenario import Scenario

    out = Path(args.out or f"runs/sim_{time.strftime('%Y%m%d_%H%M%S')}")
    out.mkdir(parents=True, exist_ok=True)
    sc = Scenario.load(args.scenario)
    _apply(sc, args)
    cosys = args.backend == "cosys"
    worlds = load_worlds()
    world = resolve_world(args.world, args.project, args.map, worlds) if cosys else None
    engine = os.path.expanduser(args.engine or worlds["engine"])

    info = {
        "started": local_time(), "finished": None, "stopped_by": None,
        "command": [sys.executable, "-m", "multiuav", *sys.argv[1:]],
        "scenario": args.scenario, "backend": args.backend,
        "unreal_mode": ("editor" if args.editor else "game") if cosys else None,
        "world": world, "engine": engine if cosys else None,
        "quality": args.quality, "fullscreen": args.fullscreen,
        "drones": sc.drones, "leader": sc.leader.name, "leader_params": sc.leader.params,
        "followers_algorithm": sc.followers.name, "model": sc.model, "rotor_drag": sc.rotor_drag,
        "cameras": cosys and not args.no_cameras and sc.drones > 1, "sticks": args.sticks,
    }
    write_json(out / "sim.json", info)
    shutil.copy(args.scenario, out / "scenario.yaml")
    status(f"прогон: {out}")

    stop = threading.Event()

    def on_signal(_signum, _frame):
        stop.set()
        raise KeyboardInterrupt

    old_handlers = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
    signal.signal(signal.SIGTERM, on_signal)

    game = cameras = sticks = None
    result_info = {"out": str(out), "ok": False}
    try:
        if cosys:
            from .cosys_settings import DEFAULT_PATH, write_settings

            busy = port_open()
            if busy and not args.editor:
                raise RuntimeError(f"порт {PORT} уже занят: открыт редактор или другая игра. "
                                   "Закройте её или запустите с --editor")
            write_settings(sc, messages=args.messages)
            shutil.copy(DEFAULT_PATH, out / "settings.json")
            status(f"settings.json записан, дронов: {sc.drones}")
            if busy:
                status("Unreal уже запущен: settings.json вступит в силу только после нового Play")
            else:
                cmd = (editor_command(engine, world["project"], world["map"]) if args.editor
                       else game_command(engine, world["project"], world["map"], args.quality,
                                         args.fullscreen))
                game = _start_logged(cmd, out / "unreal.log", game_env())
                status(f"Unreal запускается: {world['title']} (PID {game.pid})"
                       + (" — в редакторе нажмите Play" if args.editor else ""))
            wait_game(game, args.timeout or (900 if args.editor else 180), stop)
            status("Cosys ответил")
            if info["cameras"]:
                w, h = screen_size() if args.fullscreen else (1600, 900)
                cameras = _start_logged([sys.executable, "-m", "multiuav", "cameras",
                                         "--followers", str(sc.drones - 1),
                                         "--game", f"{w}x{h}+0+0"], out / "cameras.log")
                status("экраны камер ведомых запущены")

        if args.sticks:
            sticks = _start_logged([sys.executable, "-m", "multiuav", "sticks", "log",
                                    "--out", str(out), "--profile", args.sticks_profile,
                                    "--adc-filter", "unknown"], out / "sticks.log")
            status("запись стиков пульта идёт")

        from .manager import SwarmManager

        backend = _backend(args.backend, args.host)
        status(f"полёт: ведущий {sc.leader.name}, ведомые {sc.followers.name} × {sc.drones - 1}")
        result = SwarmManager(sc, backend, stop=stop).run()
        info["stopped_by"] = "Стоп" if stop.is_set() else "полёт закончен"
        result.save(out)
        if not args.no_plot:
            from .plot import plot_run

            plot_run(sc, result, out / "track.png")
        result_info.update(result.summary())
        result_info["ok"] = True
    except KeyboardInterrupt:
        info["stopped_by"] = result_info["error"] = "остановлено до начала полёта"
    except Exception as e:  # понятное сообщение в меню, подробности — в логах
        crashed = game is not None and game.poll() is not None
        info["stopped_by"] = result_info["error"] = ("Unreal упал, см. unreal.log" if crashed
                                                     else str(e))
        status(f"ОШИБКА: {result_info['error']}")
    finally:
        signal.signal(signal.SIGINT, signal.SIG_IGN)  # второй Ctrl+C не должен оборвать уборку
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        stop_process(cameras, "камеры", signal.SIGTERM, 5)
        stop_process(sticks, "запись стиков", signal.SIGINT, 5)
        if game is not None and not args.editor:
            status("закрываю Unreal")
            stop_process(game, "Unreal", signal.SIGTERM, 10)
        info["finished"] = local_time()
        write_json(out / "sim.json", info)
        for sig, handler in old_handlers.items():
            signal.signal(sig, handler)
        status("готово")
        print("RESULT " + json.dumps(result_info, ensure_ascii=False), flush=True)
    return result_info


def _backend(name: str, host: str):
    if name == "kinematic":
        from .backends.kinematic import KinematicBackend

        return KinematicBackend()
    from .backends.cosys import CosysBackend

    return CosysBackend(host=host)


def cmd_sim(args) -> None:
    if args.list_maps:
        print("\n".join(project_maps(args.list_maps)) or "карт нет (нет папки Content?)")
        return
    if args.save_world:
        if not args.project or not args.map:
            raise SystemExit("--save-world нужны --project и --map")
        w = save_world(args.save_world, args.project, args.map, args.title or "", args.scenario or "")
        print(f"мир «{args.save_world}» запомнен в {LOCAL}: {w['project']} {w['map']}")
        return
    if args.list_worlds:
        data = load_worlds()
        for name, w in data["worlds"].items():
            print(f"{name:10} {w.get('title', '')}\n{'':10} {w['project']}  {w['map']}")
        return
    if not args.scenario:
        raise SystemExit("нужен сценарий: python -m multiuav sim scenarios/….yaml …")
    result = run_sim(args)
    if not result.get("ok"):
        sys.exit(1)


def add_parser(sub, modes) -> None:
    p = sub.add_parser("sim", help="исследовательский прогон целиком: Unreal, камеры, полёт, журналы")
    p.add_argument("scenario", nargs="?")
    p.add_argument("--backend", choices=["kinematic", "cosys"], default="cosys")
    p.add_argument("--host", default="127.0.0.1", help="адрес машины с Cosys-AirSim")
    world = p.add_argument_group("мир Unreal")
    world.add_argument("--world", help="имя мира из experiments/worlds.json или local.json")
    world.add_argument("--project", help="свой проект: путь к .uproject где угодно на диске")
    world.add_argument("--map", help="карта в проекте: /Game/… (список — --list-maps)")
    world.add_argument("--engine", help="UnrealEditor (по умолчанию — из worlds.json/local.json)")
    world.add_argument("--editor", action="store_true",
                       help="редактор вместо игры: Play нажимаете сами, редактор не закрывается")
    world.add_argument("--quality", type=int, default=1, choices=range(5),
                       help="графика 0 Low … 3 Epic, 4 Cinematic (по умолчанию 1 — Medium)")
    world.add_argument("--fullscreen", action="store_true", help="игра на весь экран")
    world.add_argument("--timeout", type=int, help="сколько ждать Cosys, с (игра 180, редактор 900)")
    world.add_argument("--messages", action="store_true", help="отладочные строки AirSim сразу")
    group = p.add_argument_group("группа и ведущий")
    group.add_argument("--followers", type=int, help="сколько ведомых (дронов будет N+1)")
    group.add_argument("--algorithm", help="алгоритм ведомых или свой файл.py:Класс")
    group.add_argument("--leader", help="ведущий: waypoints (автопилот), gamepad (пульт) или свой")
    group.add_argument("--mode", choices=modes, help="режим пульта ведущего")
    group.add_argument("--model", help="модель дрона: generic, fast2.5 … (только Cosys)")
    group.add_argument("--rotor-drag", type=float, help="сопротивление винтов k, с/м")
    group.add_argument("--duration", type=float, help="длительность прогона, с")
    outp = p.add_argument_group("журналы")
    outp.add_argument("--out", help="папка прогона (по умолчанию runs/sim_<время>)")
    outp.add_argument("--no-plot", action="store_true", help="без картинки track.png")
    outp.add_argument("--no-cameras", action="store_true", help="без экранов камер ведомых")
    outp.add_argument("--sticks", action="store_true", help="писать стики пульта (sticks log)")
    outp.add_argument("--sticks-profile", default="не указан", help="профиль модели EdgeTX")
    tools = p.add_argument_group("миры")
    tools.add_argument("--list-worlds", action="store_true", help="показать известные миры")
    tools.add_argument("--list-maps", metavar="ПРОЕКТ", help="карты проекта .uproject")
    tools.add_argument("--save-world", metavar="ИМЯ", help="запомнить --project + --map как мир")
    tools.add_argument("--title", help="название мира для --save-world")
    p.set_defaults(func=cmd_sim)
