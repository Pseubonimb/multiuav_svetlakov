"""Командная строка: python -m multiuav <команда> ...

  run       прогнать сценарий (в упрощённом симуляторе или в Cosys-AirSim)
  compare   сравнить алгоритмы и размеры группы на одном сценарии
  settings  создать settings.json для Cosys-AirSim из сценария
  cameras   экраны с камер ведомых поверх окна Unreal
  gamepad   показать оси геймпада, чтобы настроить раскладку
  sticks    журнал стиков пульта через evdev (отдельно от симулятора)
  experiment  меню эксперимента: участник, затемнения, Старт/Стоп, обработка
  launch    одна попытка целиком: игра, запись пульта, полёт (то, что делает «Старт»)
  analyze   обработать папку попытки: ворота, нажатия SE, затемнения
  track     ворота и зоны карты: edit (в редакторе), export (выгрузить), reset (заново)
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
import time
from pathlib import Path

from .scenario import AlgorithmSpec, Scenario
from .types import MODES


def _make_backend(name: str, host: str):
    if name == "kinematic":
        from .backends.kinematic import KinematicBackend

        return KinematicBackend()
    if name == "cosys":
        from .backends.cosys import CosysBackend

        return CosysBackend(host=host)
    raise SystemExit(f"Неизвестный симулятор: {name}")


def _load(args) -> Scenario:
    sc = Scenario.load(args.scenario)
    if getattr(args, "drones", None):
        sc.drones = args.drones
    if getattr(args, "followers", None) is not None:
        sc.drones = args.followers + 1  # ведомые идут колонной позади ведущего
    if getattr(args, "algorithm", None):
        sc.followers = AlgorithmSpec(args.algorithm, sc.followers.params)
    if getattr(args, "leader", None):
        sc.leader = AlgorithmSpec(args.leader, sc.leader.params)
    if getattr(args, "mode", None):
        sc.leader = AlgorithmSpec(sc.leader.name, {**sc.leader.params, "mode": args.mode})
    if getattr(args, "model", None):
        sc.model = args.model.lower()  # generic, fast, fast2.5 …
    if getattr(args, "rotor_drag", None) is not None:
        sc.rotor_drag = args.rotor_drag
    return sc


def cmd_run(args) -> None:
    from .manager import SwarmManager

    sc = _load(args)
    result = SwarmManager(sc, _make_backend(args.backend, args.host)).run()
    print(json.dumps(result.summary(), ensure_ascii=False, indent=2))
    if args.out:
        out = result.save(args.out)
        print(f"Журнал прогона: {out}")
    if args.plot:
        from .plot import plot_run

        print(f"Картинка: {plot_run(sc, result, args.plot)}")


def cmd_compare(args) -> None:
    from .backends.kinematic import KinematicBackend
    from .manager import SwarmManager

    base = Scenario.load(args.scenario)
    sizes = [int(x) for x in args.drones.split(",")]
    biggest = []  # самая большая группа каждого алгоритма — для картинки
    print(f"{'алгоритм':<14}{'дронов':>7}{'дошли':>7}{'столкн.':>9}{'время, с':>10}")
    for algorithm in args.algorithms.split(","):
        for n in sizes:
            sc = dataclasses.replace(base, drones=n,
                                     followers=AlgorithmSpec(algorithm, base.followers.params))
            result = SwarmManager(sc, KinematicBackend()).run()
            r = result.summary()
            group_time = "-" if r["group_time_s"] is None else f"{r['group_time_s']:.1f}"
            print(f"{algorithm:<14}{n:>7}{r['finished']:>7}{r['collided']:>9}{group_time:>10}")
        biggest.append((sc, result))
    if args.plot:
        from .plot import plot_runs

        print(f"Картинка: {plot_runs(biggest, args.plot)}")


def cmd_settings(args) -> None:
    from .cosys_settings import DEFAULT_PATH, build_settings, write_settings

    sc = _load(args)
    if args.write:
        path = Path(args.write) if args.write != "-" else DEFAULT_PATH
        print(f"Записано: {write_settings(sc, path, args.subwindows, args.messages)}")
    else:
        print(json.dumps(build_settings(sc, args.subwindows, args.messages), indent=2))


def cmd_cameras(args) -> None:
    from .cameras import run_cameras

    drones = args.followers + 1 if args.followers is not None else args.drones
    width, height = (int(v) for v in args.tile.lower().split("x"))
    size, _, offset = args.game.lower().partition("+")
    gw, gh = (int(v) for v in size.split("x"))
    gx, gy = (int(v) for v in offset.split("+")) if offset else (0, 0)
    position = (args.x, args.y) if args.x is not None and args.y is not None else None
    run_cameras(drones, host=args.host, camera=args.camera, tile=(width, height),
                columns=args.columns, fps=args.fps, corner=args.corner,
                game=(gx, gy, gw, gh), position=position)


def cmd_gamepad(args) -> None:
    import os

    os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    import pygame

    pygame.init()
    pygame.joystick.init()
    if pygame.joystick.get_count() == 0:
        raise SystemExit("Геймпад не найден")
    js = pygame.joystick.Joystick(args.index)
    js.init()
    print(f"{js.get_name()}: {js.get_numaxes()} осей. Подвигай стики, Ctrl+C — выход.")
    try:
        while True:
            pygame.event.pump()
            axes = " ".join(f"{i}:{js.get_axis(i):+.2f}" for i in range(js.get_numaxes()))
            print("\r" + axes, end="", flush=True)
            time.sleep(0.05)
    except KeyboardInterrupt:
        print()


def cmd_sticks(args) -> None:
    from . import sticklog

    if args.action == "list":
        sticklog.run_list(args.device)
    elif args.action == "map":
        sticklog.run_map(args.device, args.map)
    elif args.action == "button":
        sticklog.run_button(args.device, args.map, args.name)
    else:
        sticklog.run_log(args.device, args.map, args.out, args.profile, args.adc_filter,
                         args.note, args.keep_fuzz, args.fsync)


def cmd_launch(args) -> None:
    from .launch import run_launch

    result = run_launch(Path(args.config), args.participant, args.backend, args.no_game,
                        args.no_sticks)
    if not result.get("ok"):
        raise SystemExit(1)


def cmd_experiment(args) -> None:
    from .menu import run_menu

    run_menu(Path(args.config))


def cmd_analyze(args) -> None:
    from .analyze import analyze

    summary = analyze(Path(args.folder), Path(args.track) if args.track else None,
                      window_s=args.window)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def cmd_track(args) -> None:
    """Ворота и зоны карты: открыть в редакторе, выгрузить после правки, поставить заново,
    эталон, уровни сложности."""
    import os
    import shutil
    import subprocess

    from .experiment import apply_level, load_config
    from .launch import _game_env, port_open

    cfg = apply_level(load_config(args.config), args.level)
    engine = Path(os.path.expanduser(cfg["engine"]))
    project = os.path.expanduser(cfg["project"])
    tools_dir = Path(__file__).resolve().parent.parent / "tools" / "unreal"
    if args.action == "edit":
        print(f"Открываю редактор на карте {cfg['map']}. Ворота — точки MU_GateCW…/MU_GateCCW… в "
              "папке MultiUAV/Gates (двигать W, поворачивать E — рамка идёт за точкой); зоны — "
              "TriggerBox… (двигать W, растягивать R). Потом Ctrl+S, закрыть редактор и: "
              "python -m multiuav track export" + (f" --level {args.level}" if args.level else ""))
        subprocess.Popen([str(engine), project, cfg["map"]], env=_game_env(), start_new_session=True)
        return
    if port_open():
        raise SystemExit("Сначала сохраните карту (Ctrl+S) и закройте редактор/игру")
    if args.action in ("save", "restore"):
        umap = Path(project).parent / "Content" / (cfg["map"].replace("/Game/", "") + ".umap")
        base = Path(os.path.expanduser(cfg.get("track_baseline", "~/Desktop/New_Karta_Vitali_baseline")))
        if args.action == "save":
            base.mkdir(parents=True, exist_ok=True)
            shutil.copy2(umap, base / umap.name)
            shutil.copy2(cfg["track"], base / Path(cfg["track"]).name)
            print(f"Эталон сохранён: {base} ({umap.name} и {Path(cfg['track']).name})")
        else:
            if not (base / umap.name).exists():
                raise SystemExit(f"Нет эталона {umap.name} в {base}: сначала python -m multiuav track save")
            shutil.copy2(base / umap.name, umap)
            shutil.copy2(base / Path(cfg["track"]).name, cfg["track"])
            print(f"Эталон возвращён из {base}")
        return

    def unreal_script(script: str, extra: str, name: str) -> None:
        print(f"Unreal без окна ({name}), ~20–60 с…", flush=True)
        log = Path("runs") / f"track_{name}.log"
        log.parent.mkdir(exist_ok=True)
        with open(log, "w", encoding="utf-8") as f:
            code = subprocess.call([str(engine.with_name("UnrealEditor-Cmd")), project,
                                    "-run=pythonscript", f"-script={tools_dir / script} {extra}",
                                    "-nullrhi", "-unattended", "-nosplash", "-nop4"],
                                   stdout=f, stderr=subprocess.STDOUT)
        text = log.read_text(encoding="utf-8", errors="replace")
        errors = [ln for ln in text.splitlines() if "LogPython: Error" in ln]
        # Код 1 бывает и при успехе: Unreal ругается на старый Level Blueprint Виталия.
        if errors or "Python script executed successfully" not in text:
            print("\n".join(dict.fromkeys(errors)))
            raise SystemExit(f"Не получилось (код {code}), лог: {log}")

    def summary(level_cfg: dict) -> None:
        data = json.loads(Path(level_cfg["track"]).read_text(encoding="utf-8"))
        print(f"Записано {level_cfg['track']} ({level_cfg['map']})")
        for d in data.get("directions", {}).values():
            print(f" {d['title']}:")
            for g in d["gates"]:
                c = g["center"]
                print(f"  {g['name']}: север {c['north']:.1f}, восток {c['east']:.1f}, "
                      f"высота {c['alt']:.1f} м, курс {g['heading_deg']:.0f}°")

    if args.action == "variants":
        # Копии карты «сложного» уровня без препятствий / без «многоэтажки», затем их трассы.
        unreal_script("make_variants.py", "", "variants")
        for level in ("medium",):
            lc = apply_level(load_config(args.config), level)
            unreal_script("friday_track.py", f"--export-only --map {lc['map']} --out {Path(lc['track']).resolve()}",
                          f"export_{level}")
            summary(lc)
        print("Средний уровень пересобран из сложного. Подробности, что убрано: "
              "runs/track_variants.json. Лёгкие трассы — python -m multiuav track easy")
        return
    easy = str(cfg.get("level", "")).startswith("easy")
    if args.action == "easy":
        # Три лёгкие трассы из Track_Easy: 12 ворот по схеме, зеркальная, старт после 7-х.
        unreal_script("easy_tracks.py", "", "easy")
        for level in ("easy1", "easy2", "easy3"):
            summary(apply_level(load_config(args.config), level))
        return
    if easy and args.action == "reset":
        raise SystemExit("У лёгких уровней ворота ставит python -m multiuav track easy "
                         "(все три карты заново из Track_Easy)")
    if easy:
        unreal_script("easy_tracks.py", f"--export-only --map {cfg['map']} --out {Path(cfg['track']).resolve()}",
                      args.action)
        summary(cfg)
        return
    extra = (f"--export-only --map {cfg['map']} --out {Path(cfg['track']).resolve()}"
             if args.action == "export" else
             f"--color {cfg.get('occlusion_color', '#969696')} --map {cfg['map']} "
             f"--out {Path(cfg['track']).resolve()}")
    unreal_script("friday_track.py", extra, args.action)
    summary(cfg)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="multiuav", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="прогнать сценарий")
    run.add_argument("scenario")
    run.add_argument("--backend", choices=["kinematic", "cosys"], default="kinematic")
    run.add_argument("--host", default="127.0.0.1", help="адрес машины с Cosys-AirSim")
    run.add_argument("--drones", type=int, help="число дронов вместо указанного в сценарии")
    run.add_argument("--followers", type=int,
                     help="сколько ведомых лететь за ведущим (0 — один ведущий); "
                          "то же, что --drones N+1")
    run.add_argument("--mode", choices=MODES,
                     help="режим пульта ведущего: althold (как AltHold) или acro")
    run.add_argument("--algorithm", help="алгоритм ведомых вместо указанного в сценарии")
    run.add_argument("--leader", help="кто ведёт: waypoints, gamepad или свой алгоритм")
    run.add_argument("--out", help="папка для журнала прогона")
    run.add_argument("--plot", help="сохранить картинку траекторий (PNG)")
    run.set_defaults(func=cmd_run)

    compare = sub.add_parser("compare", help="сравнить алгоритмы в упрощённом симуляторе")
    compare.add_argument("scenario")
    compare.add_argument("--algorithms", default="single_file,leader_path")
    compare.add_argument("--drones", default="3,5,7,10")
    compare.add_argument("--plot", help="картинка самой большой группы каждого алгоритма (PNG)")
    compare.set_defaults(func=cmd_compare)

    settings = sub.add_parser("settings", help="settings.json для Cosys-AirSim")
    settings.add_argument("scenario")
    settings.add_argument("--drones", type=int)
    settings.add_argument("--followers", type=int, help="сколько ведомых (дронов будет N+1)")
    settings.add_argument("--model", help="модель дрона вместо указанной в сценарии: generic, fast, fast2.5 …")
    settings.add_argument("--rotor-drag", type=float,
                          help="сопротивление винтов, с/м, вместо указанного в сценарии (0 — выключить)")
    settings.add_argument("--subwindows", action="store_true",
                          help="камеры первых 3 ведомых во встроенных подокнах AirSim (внизу окна)")
    settings.add_argument("--messages", action="store_true",
                          help="показывать отладочные строки AirSim с самого старта (иначе — по F2)")
    settings.add_argument("--write", nargs="?", const="-",
                          help="записать в файл (без пути — в ~/Documents/AirSim/settings.json)")
    settings.set_defaults(func=cmd_settings)

    cameras = sub.add_parser("cameras", help="экраны с камер ведомых в левом верхнем углу")
    cameras.add_argument("--drones", type=int, default=5, help="сколько дронов всего (с ведущим)")
    cameras.add_argument("--followers", type=int, help="сколько ведомых (вместо --drones)")
    cameras.add_argument("--host", default="127.0.0.1")
    cameras.add_argument("--camera", default="front_center", help="камера дрона в AirSim")
    cameras.add_argument("--tile", default="256x144", help="размер одного экрана, пиксели")
    cameras.add_argument("--columns", type=int, default=2)
    cameras.add_argument("--fps", type=float, default=20.0, help="как часто перерисовывать окно")
    cameras.add_argument("--corner", default="top-right",
                         choices=["top-right", "top-left", "bottom-right", "bottom-left"],
                         help="в каком углу окна игры (слева сверху — сообщения AirSim)")
    cameras.add_argument("--game", default="1600x900+0+0",
                         help="окно игры: ШИРИНАxВЫСОТА+X+Y (как запускает scripts/demo.sh)")
    cameras.add_argument("--x", type=int, help="своё положение окна камер, пиксели (вместо --corner)")
    cameras.add_argument("--y", type=int)
    cameras.set_defaults(func=cmd_cameras)

    gamepad = sub.add_parser("gamepad", help="показать оси геймпада")
    gamepad.add_argument("--index", type=int, default=0)
    gamepad.set_defaults(func=cmd_gamepad)

    sticks = sub.add_parser("sticks", help="журнал стиков пульта через evdev")
    sticks.add_argument("action", choices=["list", "map", "button", "log"],
                        help="list — оси и диапазоны, map — разметить стики, "
                             "button — разметить кнопку (--name SE), log — записать попытку")
    sticks.add_argument("--name", default="SE", help="имя кнопки для button")
    sticks.add_argument("--device", help="/dev/input/eventN (по умолчанию — найти пульт)")
    sticks.add_argument("--map", type=Path, default=Path("radio/sticks_map.json"),
                        help="файл соответствия стиков и осей")
    sticks.add_argument("--out", type=Path, help="папка попытки (по умолчанию runs/sticks_<время>)")
    sticks.add_argument("--profile", help="профиль модели в EdgeTX: имя, расходы, экспоненты")
    sticks.add_argument("--adc-filter", choices=["off", "on", "unknown"], default="unknown",
                        help="фильтр АЦП в EdgeTX")
    sticks.add_argument("--note", default="", help="заметка в паспорт попытки")
    sticks.add_argument("--keep-fuzz", action="store_true", help="не обнулять Fuzz и Flat")
    sticks.add_argument("--fsync", action="store_true",
                        help="fsync после каждой строки (по умолчанию — раз в секунду)")
    sticks.set_defaults(func=cmd_sticks)

    experiment = sub.add_parser("experiment", help="меню эксперимента (окно)")
    experiment.add_argument("--config", default="experiments/friday.json",
                            help="параметры по умолчанию")
    experiment.set_defaults(func=cmd_experiment)

    launch = sub.add_parser("launch", help="одна попытка: игра, запись пульта, полёт")
    launch.add_argument("config", help="experiment.json (папка попытки) или experiments/friday.json")
    launch.add_argument("--participant", help="код участника вместо указанного в файле")
    launch.add_argument("--backend", choices=["cosys", "kinematic"], default="cosys",
                        help="kinematic — проверка без Unreal")
    launch.add_argument("--no-game", action="store_true",
                        help="игру не запускать: она уже открыта (Play в редакторе)")
    launch.add_argument("--no-sticks", action="store_true", help="без записи пульта (sticks log)")
    launch.set_defaults(func=cmd_launch)

    analyze = sub.add_parser("analyze", help="обработать папку попытки")
    analyze.add_argument("folder")
    analyze.add_argument("--track", help="файл ворот и зон (по умолчанию — из experiment.json)")
    analyze.add_argument("--window", type=float, default=2.0,
                         help="нажатие относится к воротам, если до пролёта не дальше, с")
    analyze.set_defaults(func=cmd_analyze)

    track = sub.add_parser("track", help="ворота и зоны карты эксперимента")
    track.add_argument("action", choices=["edit", "export", "reset", "save", "restore", "variants", "easy"],
                       help="edit — открыть карту в редакторе, export — выгрузить ворота и зоны "
                            "в maps/…json после правки, reset — поставить ворота заново по умолчанию, "
                            "save — запомнить карту и json как эталон, restore — вернуть эталон, "
                            "variants — заново сделать средний уровень из сложного, "
                            "easy — три лёгкие трассы из Track_Easy")
    track.add_argument("--level", choices=["easy1", "easy2", "easy3", "medium", "hard"],
                       help="уровень сложности (по умолчанию — из experiments/friday.json)")
    track.add_argument("--config", default="experiments/friday.json")
    track.set_defaults(func=cmd_track)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main(sys.argv[1:])
