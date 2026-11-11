"""Три лёгкие трассы (плоский коридор) с 12 воротами — из Track_Easy, которую правили вручную.

  easy1  /Game/FlyingCPP/Maps/Track_Easy          базовая: старт в коробке, по часовой — на север
  easy2  /Game/FlyingCPP/Maps/Track_Easy_Mirror   зеркальная (восток ↔ запад): повороты наоборот
  easy3  /Game/FlyingCPP/Maps/Track_Easy_Reverse  та же, что базовая, старт после 7-х ворот,
                                                   полёт против часовой (7 → 6 → … → 1 → 12 → … → 8)

  UnrealEditor-Cmd …/Blocks.uproject -run=pythonscript -script="tools/unreal/easy_tracks.py" -nullrhi
  … -script="tools/unreal/easy_tracks.py --export-only --map /Game/FlyingCPP/Maps/Track_Easy"

Сборка: в Track_Easy убираются зоны (TriggerBox — затемнение теперь «по времени до ворот») и
старые ворота, заделываются две дыры в стенах, ставятся ворота MU_Gate01…12 (одна рамка на
оба направления; номер — порядок по ходу). Затем из неё — зеркальная и обратная копии.
--export-only: ничего не меняет, выгружает ворота, как они стоят (после правки в редакторе),
в maps/<карта>.json. Ворота — точки MU_GateNN, порядок — по номеру в имени.
"""

import argparse
import json
import math
import os
import re
import shlex
import sys

import unreal

sys._multiuav_import = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import friday_track as ft  # noqa: E402

BASE = "/Game/FlyingCPP/Maps/Track_Easy"
MIRROR = "/Game/FlyingCPP/Maps/Track_Easy_Mirror"
REVERSE = "/Game/FlyingCPP/Maps/Track_Easy_Reverse"
START_UE = (1591.64, -17928.43)  # PlayerStart базовой карты, см — от него все координаты ниже
GATE_RE = re.compile(r"^MU_Gate(\d\d)$")

# Ворота по схеме пользователя (11.11): (север, восток — м от старта; курс по часовой, °).
# Кроме 11-х — центр ставится посередине между стенами коридора. 11-е — на диагональном
# повороте (стенки под курсом 310°, 19,6 м друг от друга), центр посчитан по стенкам.
GATES = [
    (190, -15, 0), (365, 25, 90), (365, 276, 90), (365, 478, 90),
    (243, 555, 180), (-96, 555, 180), (-488, 555, 180), (-545, 380, 270),
    (-504, 200, 0), (-255, 200, 0), (-30.6, 184.0, 310.4), (-15, 47, 270),
]
# easy3: старт после 7-х ворот по часовой, нос — на север, к 7-м.
REVERSE_START = (-515, 555)
REVERSE_ORDER = [7, 6, 5, 4, 3, 2, 1, 12, 11, 10, 9, 8]
# Направления: прямое — ворота 1 → 12, обратное — 12 → 1; курс носа на старте.
DIRECTIONS = {
    BASE: {"forward": ("прямое: от старта на север, ворота 1 → 12", 0.0),
           "reverse": ("обратное: от старта на восток, ворота 12 → 1", 90.0)},
    MIRROR: {"forward": ("прямое: от старта на север, ворота 1 → 12", 0.0),
             "reverse": ("обратное: от старта на запад, ворота 12 → 1", 270.0)},
    REVERSE: {"forward": ("прямое: от старта на север, ворота 1 → 12", 0.0),
              "reverse": ("обратное: от старта на юг, ворота 12 → 1", 180.0)},
}
# Дыры в стенах Track_Easy и Track_Medium: make_variants когда-то убрал эти куски стен (тонкие,
# на всю высоту, ~10 м) как «окошки»: стены стартовой коробки и внешние стены двух углов.
# Центр и полуразмеры в см — как у этих кубов в FlyingExampleMap.
HOLES = [
    ("Cube778", (38896.1, -19513.8, 1044.3), (50.0, 487.9, 1089.6)),
    ("Cube746", (-874.9, -19532.0, 1147.1), (50.0, 487.9, 1089.6)),
    ("Cube756", (6683.4, -17992.8, 1096.2), (50.0, 487.9, 1089.6)),
    ("Cube738", (6683.4, -17484.2, 1096.2), (50.0, 487.9, 1089.6)),
    ("Cube757", (1642.8, -14033.0, 1147.1), (487.9, 50.0, 1089.6)),
    ("Cube530", (38571.4, 38522.4, 1109.6), (487.9, 50.0, 1089.6)),
    ("Cube137", (-54085.5, 1485.7, 1109.6), (50.0, 487.9, 1089.6)),
]
PATCH_TAG = "MultiUAV_Patch"


def log(text):
    unreal.log(f"MULTIUAV: {text}")


def actors():
    return ft.eas.get_all_level_actors()


def snake_walls():
    walls = []
    for a in actors():
        if isinstance(a, unreal.StaticMeshActor) and "snake" in str(a.get_folder_path()):
            walls.append(a.get_actor_bounds(False))
    return walls


def floor_z():
    start = next(a for a in actors() if isinstance(a, unreal.PlayerStart))
    cap = start.get_component_by_class(unreal.CapsuleComponent)
    return start.get_actor_location().z - cap.get_scaled_capsule_half_height()


def remove_gates_and_zones():
    for a in actors():
        if unreal.Name(ft.TAG) in a.tags or isinstance(a, unreal.TriggerBox):
            a.destroy_actor()


def patch_holes():
    """Заделать дыры кусками стены той же формы и материала, что соседняя стена."""
    walls = [a for a in actors() if isinstance(a, unreal.StaticMeshActor)
             and "snake" in str(a.get_folder_path())]
    cube = unreal.load_asset("/Engine/BasicShapes/Cube.Cube")
    for label, (x, y, z), (ex, ey, ez) in HOLES:
        if any(a.get_actor_label() in (label, f"MU_Patch_{label}") for a in actors()):
            continue  # стена на месте или уже заделано
        near = min(walls, key=lambda w: (w.get_actor_location().x - x) ** 2 + (w.get_actor_location().y - y) ** 2)
        a = ft.eas.spawn_actor_from_class(unreal.StaticMeshActor, unreal.Vector(x, y, z))
        a.static_mesh_component.set_static_mesh(cube)
        a.static_mesh_component.set_material(0, near.static_mesh_component.get_material(0))
        a.set_actor_scale3d(unreal.Vector(ex / 50, ey / 50, ez / 50))
        a.set_actor_label(f"MU_Patch_{label}")
        a.set_folder_path("Geometry/Ground/snake")
        a.tags = [unreal.Name(PATCH_TAG)]
        log(f"заделана дыра на месте {label}")


def gate_center(north, east, heading, walls, floor, start=START_UE, mirror=False):
    x = start[0] + north * 100
    y = start[1] + (-east if mirror else east) * 100
    if abs(heading % 90) < 1e-6:  # прямой участок: по центру между стенами
        d = (round(math.cos(math.radians(heading))), round(math.sin(math.radians(heading))))
        if mirror:
            d = (d[0], -d[1])
        (x, y), _ = ft.corridor_center((x, y), d, walls)
    return (x, y, floor + ft.GATE_HEIGHT_CM)


def place_gates(order, mirror=False, flip=False):
    """order — номера ворот из GATES (1…12) по ходу; flip — курс +180° (полёт против часовой)."""
    frame = unreal.load_asset(f"{ft.MAT_DIR}/M_MultiUAV_GateFrame")
    dot = unreal.load_asset(f"{ft.MAT_DIR}/M_MultiUAV_GateDot")
    walls, floor = snake_walls(), floor_z()
    for k, number in enumerate(order):
        north, east, heading = GATES[number - 1]
        center = gate_center(north, east, heading, walls, floor, mirror=mirror)
        yaw = (-heading if mirror else heading) + (180 if flip else 0)
        ft.place_gate(f"MU_Gate{k + 1:02d}", center, yaw, frame, dot)


def mirror_level():
    """Отразить всё, что стоит на карте, относительно линии север–юг через старт (восток ↔ запад)."""
    axis = START_UE[1]
    n = 0
    for a in actors():
        if not isinstance(a, unreal.StaticMeshActor) or a.get_attach_parent_actor():
            continue
        o, e = a.get_actor_bounds(False)
        if max(e.x, e.y) > 20000:
            continue  # пол
        loc, rot = a.get_actor_location(), a.get_actor_rotation()
        a.set_actor_location(unreal.Vector(loc.x, 2 * axis - loc.y, loc.z), False, True)
        a.set_actor_rotation(unreal.Rotator(roll=-rot.roll, pitch=rot.pitch, yaw=-rot.yaw), True)
        n += 1
    log(f"отражено объектов: {n}")


def copy_level(src, dst):
    if unreal.EditorAssetLibrary.does_asset_exist(dst):
        unreal.EditorAssetLibrary.delete_asset(dst)
    if not unreal.EditorAssetLibrary.duplicate_asset(src, dst):
        raise RuntimeError(f"не удалось скопировать {src} → {dst}")
    ft.les.load_level(dst)


def export(path, out):
    """Ворота MU_GateNN этой карты (по номеру), стены, старт → maps/<карта>.json."""
    start = next(a for a in actors() if isinstance(a, unreal.PlayerStart))
    s = start.get_actor_location()
    floor = floor_z()

    def ned(x, y, z):
        return {"north": round((x - s.x) / 100, 3), "east": round((y - s.y) / 100, 3),
                "alt": round((z - s.z) / 100, 3)}

    dots = sorted(((int(GATE_RE.match(a.get_actor_label()).group(1)), a) for a in actors()
                   if GATE_RE.match(a.get_actor_label())), key=lambda p: p[0])
    gates = []
    for k, (_, a) in enumerate(dots):
        loc = a.get_actor_location()
        gates.append({"index": k, "name": f"MU_Gate{k + 1:02d}", "actor": a.get_name(),
                      "center": ned(loc.x, loc.y, loc.z),
                      "heading_deg": round(a.get_actor_rotation().yaw % 360, 1),
                      "width_m": ft.OPENING_CM / 100, "height_m": ft.OPENING_CM / 100})
    reverse = [dict(g, index=k, heading_deg=round((g["heading_deg"] + 180) % 360, 1))
               for k, g in enumerate(reversed(gates))]
    titles = DIRECTIONS.get(path, DIRECTIONS[BASE])
    walls = []
    for o, e in snake_walls():
        n0, e0 = (o.x - e.x - s.x) / 100, (o.y - e.y - s.y) / 100
        walls.append([round(n0, 2), round(e0, 2), round(n0 + 2 * e.x / 100, 2), round(e0 + 2 * e.y / 100, 2)])
    old = json.loads(open(out, encoding="utf-8").read()) if os.path.exists(out) else {}
    vol = next(a for a in actors() if a.get_actor_label() == ft.OCCLUSION_LABEL)
    occlusion = {**old.get("occlusion", {}), "volume": vol.get_name(), "label": ft.OCCLUSION_LABEL,
                 "size_m": ft.OCCLUSION_SIZE_CM / 100,
                 "park_ned": [0.0, 0.0, -ft.OCCLUSION_PARK_CM[2] / 100]}
    data = {
        "map": path,
        "frame": "NED: north, east — м от PlayerStart; alt — м вверх от PlayerStart; "
                 "heading_deg — курс полёта через ворота, 0 — север, по часовой",
        "player_start_ue_cm": [round(s.x, 1), round(s.y, 1), round(s.z, 1)],
        "floor_alt": round((floor - s.z) / 100, 3),
        "occlusion": occlusion,
        "walls": walls,
        "zones": [],
        "directions": {
            "forward": {"title": titles["forward"][0], "start_heading_deg": titles["forward"][1],
                        "zones": [], "gates": gates},
            "reverse": {"title": titles["reverse"][0], "start_heading_deg": titles["reverse"][1],
                        "zones": [], "gates": reverse},
        },
    }
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    log(f"{path}: ворот {len(gates)} → {out}")


def out_for(path):
    return os.path.join(ft.REPO, "maps", path.rsplit("/", 1)[-1] + ".json")


def main(argv):
    p = argparse.ArgumentParser()
    p.add_argument("--export-only", action="store_true")
    p.add_argument("--patch-only", action="store_true", help="только заделать дыры в стенах --map")
    p.add_argument("--map", default=BASE)
    p.add_argument("--out")
    args = p.parse_args(argv)
    if args.patch_only:
        ft.les.load_level(args.map)
        patch_holes()
        ft.les.save_current_level()
        return
    if args.export_only:
        ft.les.load_level(args.map)
        export(args.map, args.out or out_for(args.map))
        return

    ft.les.load_level(BASE)
    remove_gates_and_zones()
    patch_holes()
    place_gates(list(range(1, 13)))
    ft.les.save_current_level()
    export(BASE, out_for(BASE))

    copy_level(BASE, MIRROR)
    remove_gates_and_zones()
    mirror_level()
    place_gates(list(range(1, 13)), mirror=True)
    ft.les.save_current_level()
    export(MIRROR, out_for(MIRROR))

    copy_level(BASE, REVERSE)
    remove_gates_and_zones()
    start = next(a for a in actors() if isinstance(a, unreal.PlayerStart))
    z = start.get_actor_location().z
    start.set_actor_location(unreal.Vector(START_UE[0] + REVERSE_START[0] * 100,
                                           START_UE[1] + REVERSE_START[1] * 100, z), False, True)
    start.set_actor_rotation(unreal.Rotator(0, 0, 0), True)
    place_gates(REVERSE_ORDER, flip=True)
    ft.les.save_current_level()
    export(REVERSE, out_for(REVERSE))


argv = sys.argv[1:]
if len(argv) == 1 and " " in argv[0]:
    argv = shlex.split(argv[0])
main(argv)
