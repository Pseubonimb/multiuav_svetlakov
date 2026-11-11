"""Уровни сложности трассы — копии карты Виталия без части объектов.

  hard   /Game/FlyingCPP/Maps/FlyingExampleMap — исходная карта (с заглушками тупиков)
  medium /Game/FlyingCPP/Maps/Track_Medium     — без препятствий, с перепадами высоты
  (лёгкие уровни — tools/unreal/easy_tracks.py)

Запуск (Unreal без окна; обычно через python -m multiuav track variants):

  UnrealEditor-Cmd …/Blocks.uproject -run=pythonscript -script="tools/unreal/make_variants.py" -nullrhi

Каждый раз копии делаются заново из FlyingExampleMap: правки трассы — только в ней,
затем снова variants. Что считается чем — по положению вдоль кольца (осевая линия, как в
friday_track.py) и по папкам Виталия в Outliner:
  «многоэтажка» — участок LEVELS_M (правый коридор): всё, кроме стен коридора у пола;
  препятствия — колонны, плиты-слалом вне «многоэтажки», балки loners, которые не
                перекрывают коридор от пола до верха или не на всю ширину (окошки);
  остаётся всегда — стены коридора, угловые блоки и заглушки на всю высоту и ширину,
                диагональный поворот turn_1 вне «многоэтажки», ворота, зоны, свет.
Итог по каждой карте пишется в лог Unreal строками «MULTIUAV: …».
"""

import json
import math
import os

import unreal

SOURCE = "/Game/FlyingCPP/Maps/FlyingExampleMap"
VARIANTS = {
    "medium": "/Game/FlyingCPP/Maps/Track_Medium",
}
# Лёгкие уровни (Track_Easy, …_Mirror, …_Reverse) делает tools/unreal/easy_tracks.py
# из Track_Easy, которую правили вручную, — здесь их не трогаем.
#: Тонкие стены на всю высоту, но не на всю ширину — куски стен стартовой коробки и внешних
#: стен углов, а не окошки (без них в стенах были дыры, 11.11).
KEEP_LABELS = {"Cube778", "Cube746", "Cube756", "Cube738", "Cube757", "Cube530", "Cube137"}
CENTERLINE = [(0, -15), (365, -15), (365, 555), (-545, 555), (-545, 200), (-15, 200),
              (-15, -15), (0, -15)]
LEVELS_M = (995.0, 1335.0)   # «многоэтажка» вдоль кольца по часовой, м от старта
CORRIDOR_M = 25.0            # дальше от осевой — не коридор (фон, небо), не трогаем
FULL_HEIGHT_M = 19.0         # «от пола до верха»: стены коридора 22 м
FULL_WIDTH_M = 17.0          # «на всю ширину»: коридор 19 м
REPORT = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                      "runs", "track_variants.json")

les = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
eas = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)


def log(text):
    unreal.log(f"MULTIUAV: {text}")


def progress(n, e):
    """(м от осевой, м вдоль кольца, (dn, de) хода там)."""
    best, walked = None, 0.0
    for (n0, e0), (n1, e1) in zip(CENTERLINE, CENTERLINE[1:]):
        dn, de = n1 - n0, e1 - e0
        length = math.hypot(dn, de)
        a = max(0.0, min(1.0, ((n - n0) * dn + (e - e0) * de) / length ** 2))
        dist = math.hypot(n - n0 - a * dn, e - e0 - a * de)
        if best is None or dist < best[0]:
            best = (dist, walked + a * length, (dn / length, de / length))
        walked += length
    return best


def classify(actor, start, floor):
    """'levels', 'obstacle' или None (оставить)."""
    if not isinstance(actor, unreal.StaticMeshActor):
        return None
    folder = str(actor.get_folder_path())
    if folder.startswith("MultiUAV"):
        return None  # ворота, затемнение
    o, e = actor.get_actor_bounds(False)
    if max(e.x, e.y) > 20000:
        return None  # пол, большие плиты
    n, ea = (o.x - start.x) / 100, (o.y - start.y) / 100
    dist, along, (dn, de) = progress(n, ea)
    if dist > CORRIDOR_M:
        return None
    z0, z1 = (o.z - e.z - floor) / 100, (o.z + e.z - floor) / 100
    thick = (abs(dn) * e.x + abs(de) * e.y) * 2 / 100   # вдоль хода
    width = (abs(de) * e.x + abs(dn) * e.y) * 2 / 100   # поперёк хода
    in_levels = LEVELS_M[0] <= along <= LEVELS_M[1]
    if "snake" in folder:
        return "levels" if in_levels and z0 > 1.0 else None
    if in_levels:
        return "levels"
    if "cylinders" in folder or folder.endswith("Ground/Gates"):
        return "obstacle"
    if actor.get_actor_label() in KEEP_LABELS:
        return None
    if "loners" in folder:
        full_height = z0 < 1.0 and z1 > FULL_HEIGHT_M
        full_width = width >= FULL_WIDTH_M or thick > 1.5
        return None if full_height and full_width else "obstacle"
    return None  # turn_1 вне «многоэтажки» (поворот) и прочее


def build(level, path):
    if unreal.EditorAssetLibrary.does_asset_exist(path):
        unreal.EditorAssetLibrary.delete_asset(path)
    if not unreal.EditorAssetLibrary.duplicate_asset(SOURCE, path):
        raise RuntimeError(f"не удалось скопировать {SOURCE} → {path}")
    les.load_level(path)
    actors = eas.get_all_level_actors()
    start = next(a for a in actors if isinstance(a, unreal.PlayerStart))
    s = start.get_actor_location()
    floor = s.z - start.get_component_by_class(unreal.CapsuleComponent).get_scaled_capsule_half_height()
    remove = {"obstacle"} if level == "medium" else {"obstacle", "levels"}
    counts, removed = {"obstacle": 0, "levels": 0}, []
    for a in actors:
        kind = classify(a, s, floor)
        if kind:
            counts[kind] += 1
        if kind in remove:
            removed.append(f"{a.get_actor_label()} ({kind}, {a.get_folder_path()})")
            a.destroy_actor()
    les.save_current_level()
    log(f"{level}: {path} — убрано {len(removed)} (препятствий {counts['obstacle']}, "
        f"«многоэтажки» {counts['levels']}, убраны: {', '.join(sorted(remove))})")
    return {"map": path, "removed": removed, "counts": counts}


report = {level: build(level, path) for level, path in VARIANTS.items()}
os.makedirs(os.path.dirname(REPORT), exist_ok=True)
with open(REPORT, "w", encoding="utf-8") as f:
    json.dump(report, f, ensure_ascii=False, indent=1)
log(f"записано {REPORT}")
