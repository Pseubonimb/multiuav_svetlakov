"""Карта Виталия для пробного эксперимента: ворота, серое затемнение, выгрузка геометрии.

Запускается внутри Unreal (Python редактора), без окна:

  UnrealEditor-Cmd ~/Desktop/New_Karta_Vitali/Blocks.uproject -run=pythonscript \
      -script="tools/unreal/friday_track.py --color '#969696'" -nullrhi -unattended

Ворота и зоны можно двигать в редакторе: точка MU_GateCW01… / MU_GateCCW01… (папка MultiUAV/Gates) —
вместе с рамкой, коробки TriggerBox… — двигать и растягивать; затем выгрузить:
  в редакторе, Output Log → Cmd:  py "<путь>/tools/unreal/friday_track.py" --export-only
  или с закрытым редактором:       python -m multiuav track export

Что делает (повторный запуск без --export-only ставит ворота заново на места по умолчанию):
  1. на выходе из каждой зоны затемнения (TriggerBox) ставит ворота-«окошко»:
     рамку из 4 брусьев с яркой точкой в центре (точка без столкновений);
  2. затемнение: материал M_MultiUAV_Occlusion (постобработка после тонмаппинга: цвет
     Color или картинка Image на весь экран) и экземпляр MI_MultiUAV_Occlusion; свой объём
     MU_Occlusion (куб 400 м под картой) — MultiUAV на время затемнения переносит его к
     дрону. Затемнение Виталия (Level Blueprint + TriggerBox) выключается: там секунды
     вместо мс, перепутан порядок зон, первая зона не гаснет обратно;
     --occlusion-only --color #RRGGBB [--image файл] — только поменять цвет/картинку;
  3. сохраняет карту и пишет maps/<карта>.json: ворота, зоны и точку старта
     в осях MultiUAV (NED: север, восток, высота вверх; метры от PlayerStart).

Оси Unreal: X — север, Y — восток, Z — вверх, сантиметры; рысканье 0 — на север, по часовой.
"""

import argparse
import json
import math
import os
import shlex
import sys

import unreal

MAP = "/Game/FlyingCPP/Maps/FlyingExampleMap"
REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Осевая линия кольца (север, восток — м от PlayerStart) по часовой стрелке на карте сверху:
# от старта на север по левому коридору, на восток по верхнему, на юг по правому, на запад
# по нижнему, на север по среднему и на запад обратно к старту. Порядок зон и ворот — по
# расстоянию вдоль этой линии. Против часовой — то же в обратную сторону.
CENTERLINE = [(0, -15), (365, -15), (365, 555), (-545, 555), (-545, 200), (-15, 200),
              (-15, -15), (0, -15)]
DIRECTIONS = {
    "cw": {"title": "по часовой: от старта на север", "start_heading_deg": 0.0},
    "ccw": {"title": "против часовой: от старта на восток", "start_heading_deg": 90.0},
}
ZONE_WALL_OVERLAP_CM = 20  # зона заходит в стены, чтобы между ними не было щели
GATE_AFTER_CM = 150      # ворота на столько дальше выхода из зоны
GATE_HEIGHT_CM = 500     # центр проёма над полом (как центр зон Виталия)
OPENING_CM = 400         # проём ворот, квадрат
BAR_CM = 30              # толщина брусьев рамки
DOT_CM = 20              # точка в центре
TAG = "MultiUAV_Gate"
FOLDER = "MultiUAV/Gates"
MAT_DIR = "/Game/MultiUAV"
OCCLUSION_LABEL = "MU_Occlusion"
OCCLUSION_SIZE_CM = 40000           # куб 400 м: дрон за 2 с затемнения из него не вылетит
OCCLUSION_PARK_CM = (0.0, 0.0, -1.0e6)  # «стоянка» — 10 км под картой

les = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
eas = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
mel = unreal.MaterialEditingLibrary
tools = unreal.AssetToolsHelpers.get_asset_tools()


def log(text):
    unreal.log(f"MULTIUAV: {text}")


def actors():
    return eas.get_all_level_actors()


def corridor_center(point, direction, walls):
    """Середина между ближайшими стенами слева и справа от точки (поперёк хода), см."""
    dx, dy = direction
    side = (-dy, dx)  # влево от направления
    left, right = math.inf, math.inf
    for origin, extent in walls:
        along = (origin.x - point[0]) * dx + (origin.y - point[1]) * dy
        half_along = extent.x * abs(dx) + extent.y * abs(dy)
        if abs(along) > half_along:
            continue  # стена не напротив точки
        lat = (origin.x - point[0]) * side[0] + (origin.y - point[1]) * side[1]
        half_lat = extent.x * abs(side[0]) + extent.y * abs(side[1])
        if lat > 0:
            left = min(left, lat - half_lat)
        else:
            right = min(right, -lat - half_lat)
    if math.isinf(left) or math.isinf(right):
        return point, None
    shift = (left - right) / 2
    return (point[0] + side[0] * shift, point[1] + side[1] * shift), left + right


def material(name, color, emissive, domain_pp=False):
    path = f"{MAT_DIR}/{name}"
    if unreal.EditorAssetLibrary.does_asset_exist(path):
        unreal.EditorAssetLibrary.delete_asset(path)
    mat = tools.create_asset(name, MAT_DIR, unreal.Material, unreal.MaterialFactoryNew())
    param = mel.create_material_expression(mat, unreal.MaterialExpressionVectorParameter, -400, 0)
    param.set_editor_property("parameter_name", "Color")
    param.set_editor_property("default_value", unreal.LinearColor(*color, 1.0))
    if domain_pp:
        mat.set_editor_property("material_domain", unreal.MaterialDomain.MD_POST_PROCESS)
        mat.set_editor_property("blendable_location",
                                unreal.BlendableLocation.BL_SCENE_COLOR_AFTER_TONEMAPPING)
        mel.connect_material_property(param, "", unreal.MaterialProperty.MP_EMISSIVE_COLOR)
    else:
        mel.connect_material_property(param, "", unreal.MaterialProperty.MP_BASE_COLOR)
        if emissive:
            mul = mel.create_material_expression(mat, unreal.MaterialExpressionMultiply, -200, 200)
            mul.set_editor_property("const_b", emissive)
            mel.connect_material_expressions(param, "", mul, "A")
            mel.connect_material_property(mul, "", unreal.MaterialProperty.MP_EMISSIVE_COLOR)
    mel.recompile_material(mat)
    unreal.EditorAssetLibrary.save_loaded_asset(mat)
    return mat


def srgb_to_linear(c):
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def occlusion_base():
    """M_MultiUAV_Occlusion: постобработка после тонмаппинга, весь экран — цвет Color
    или картинка Image (растянута на экран), выбор — UseImage 0/1."""
    path = f"{MAT_DIR}/M_MultiUAV_Occlusion"
    if unreal.EditorAssetLibrary.does_asset_exist(path):
        unreal.EditorAssetLibrary.delete_asset(path)
    mat = tools.create_asset("M_MultiUAV_Occlusion", MAT_DIR, unreal.Material, unreal.MaterialFactoryNew())
    mat.set_editor_property("material_domain", unreal.MaterialDomain.MD_POST_PROCESS)
    mat.set_editor_property("blendable_location",
                            unreal.BlendableLocation.BL_SCENE_COLOR_AFTER_TONEMAPPING)
    color = mel.create_material_expression(mat, unreal.MaterialExpressionVectorParameter, -700, -200)
    color.set_editor_property("parameter_name", "Color")
    color.set_editor_property("default_value", unreal.LinearColor(0.3, 0.3, 0.3, 1.0))
    uv = mel.create_material_expression(mat, unreal.MaterialExpressionScreenPosition, -900, 100)
    image = mel.create_material_expression(mat, unreal.MaterialExpressionTextureSampleParameter2D,
                                           -700, 100)
    image.set_editor_property("parameter_name", "Image")
    image.set_editor_property("texture", unreal.load_asset("/Engine/EngineResources/WhiteSquareTexture"))
    mel.connect_material_expressions(uv, "ViewportUV", image, "UVs")
    use = mel.create_material_expression(mat, unreal.MaterialExpressionScalarParameter, -700, 400)
    use.set_editor_property("parameter_name", "UseImage")
    lerp = mel.create_material_expression(mat, unreal.MaterialExpressionLinearInterpolate, -300, 0)
    mel.connect_material_expressions(color, "", lerp, "A")
    mel.connect_material_expressions(image, "RGB", lerp, "B")
    mel.connect_material_expressions(use, "", lerp, "Alpha")
    mel.connect_material_property(lerp, "", unreal.MaterialProperty.MP_EMISSIVE_COLOR)
    mel.recompile_material(mat)
    unreal.EditorAssetLibrary.save_loaded_asset(mat)
    return mat


def occlusion_material(color_srgb, image_file, rebuild):
    """MI_MultiUAV_Occlusion: цвет (sRGB 0..1, как на экране) или картинка из файла."""
    base = unreal.load_asset(f"{MAT_DIR}/M_MultiUAV_Occlusion")
    if rebuild or base is None:
        base = occlusion_base()
    path = f"{MAT_DIR}/MI_MultiUAV_Occlusion"
    mi = unreal.load_asset(path)
    if mi is None:
        mi = tools.create_asset("MI_MultiUAV_Occlusion", MAT_DIR, unreal.MaterialInstanceConstant,
                                unreal.MaterialInstanceConstantFactoryNew())
    mi.set_editor_property("parent", base)
    lin = [srgb_to_linear(c) for c in color_srgb]
    mel.set_material_instance_vector_parameter_value(mi, "Color", unreal.LinearColor(*lin, 1.0))
    if image_file:
        task = unreal.AssetImportTask()
        task.set_editor_property("filename", image_file)
        task.set_editor_property("destination_path", MAT_DIR)
        task.set_editor_property("destination_name", "T_MultiUAV_OcclusionImage")
        task.set_editor_property("replace_existing", True)
        task.set_editor_property("automated", True)
        task.set_editor_property("save", True)
        tools.import_asset_tasks([task])
        tex = unreal.load_asset(f"{MAT_DIR}/T_MultiUAV_OcclusionImage")
        if tex is None:
            raise RuntimeError(f"Unreal не импортировал картинку {image_file}")
        mel.set_material_instance_texture_parameter_value(mi, "Image", tex)
        mel.set_material_instance_scalar_parameter_value(mi, "UseImage", 1.0)
    else:
        mel.set_material_instance_scalar_parameter_value(mi, "UseImage", 0.0)
    unreal.EditorAssetLibrary.save_loaded_asset(mi)
    return mi


def occlusion_volume(mi):
    """Объём затемнения: PostProcessVolume2 Виталия, переделанный в куб 400 м, включённый,
    но «запаркованный» глубоко под картой. MultiUAV на время затемнения переносит его к
    дрону (simSetObjectPose). Новый объём из Python без окна не создать: у него нет кисти,
    а копирование объекта в этом режиме роняет Unreal."""
    vol = next(a for a in actors() if isinstance(a, unreal.PostProcessVolume)
               and a.get_actor_label() in ("PostProcessVolume2", OCCLUSION_LABEL))
    vol.set_actor_label(OCCLUSION_LABEL)
    vol.set_folder_path(FOLDER)
    vol.root_component.set_mobility(unreal.ComponentMobility.MOVABLE)
    vol.set_actor_scale3d(unreal.Vector(OCCLUSION_SIZE_CM / 200, OCCLUSION_SIZE_CM / 200,
                                        OCCLUSION_SIZE_CM / 200))
    vol.set_actor_location(unreal.Vector(*OCCLUSION_PARK_CM), False, True)
    vol.set_editor_property("unbound", False)
    vol.set_editor_property("enabled", True)
    vol.set_editor_property("priority", 100.0)
    vol.set_editor_property("blend_radius", 0.0)
    vol.set_editor_property("blend_weight", 1.0)
    settings = vol.get_editor_property("settings")
    blendables = settings.weighted_blendables
    item = unreal.WeightedBlendable()
    item.weight = 1.0
    item.object = mi
    blendables.array = [item]
    settings.weighted_blendables = blendables
    vol.set_editor_property("settings", settings)
    # Затемнение Виталия выключено (его Blueprint берёт секунды вместо мс, путает порядок зон,
    # а первая зона не гаснет обратно): триггеры больше не ловят пересечения.
    for a in actors():
        if isinstance(a, unreal.TriggerBox):
            comp = a.get_component_by_class(unreal.ShapeComponent)
            comp.set_editor_property("generate_overlap_events", False)
            comp.set_collision_enabled(unreal.CollisionEnabled.NO_COLLISION)
    log(f"объём затемнения {vol.get_name()} ({OCCLUSION_LABEL}), триггеры Виталия выключены")
    return vol


def fit_zone(zone, axis, walls):
    """Растянуть коробку зоны поперёк коридора от стены до стены (и по центру коридора);
    длину вдоль хода и высоту не трогать."""
    o, e = zone.get_actor_bounds(False)
    center, width = corridor_center((o.x, o.y), axis, walls)
    if width is None:
        log(f"ВНИМАНИЕ: у {zone.get_actor_label()} не нашёл стен по бокам — размер не менял")
        return
    box = zone.get_component_by_class(unreal.BoxComponent)
    extent = box.get_editor_property("box_extent")
    scale = zone.get_actor_scale3d()
    half = width / 2 + ZONE_WALL_OVERLAP_CM
    loc = zone.get_actor_location()
    if axis[0] == 0:  # коридор вдоль Y (восток–запад): растягиваем по X
        scale.x = half / extent.x
        loc.x = center[0]
    else:
        scale.y = half / extent.y
        loc.y = center[1]
    zone.set_actor_scale3d(scale)
    zone.set_actor_location(loc, False, True)
    log(f"зона {zone.get_actor_label()}: поперёк {2 * half / 100:.1f} м")


def spawn_box(mesh, label, loc, rot, size_cm, mat, collide=True):
    a = eas.spawn_actor_from_class(unreal.StaticMeshActor, loc, rot)
    if a is None:
        raise RuntimeError(f"Unreal не создал {label}")
    a.root_component.set_mobility(unreal.ComponentMobility.MOVABLE)
    a.static_mesh_component.set_static_mesh(mesh)
    a.set_actor_label(label)
    a.set_actor_scale3d(unreal.Vector(*(s / 100.0 for s in size_cm)))
    a.tags = [unreal.Name(TAG)]
    a.set_folder_path(FOLDER)
    comp = a.static_mesh_component
    comp.set_material(0, mat)
    if not collide:
        comp.set_collision_enabled(unreal.CollisionEnabled.NO_COLLISION)
        comp.set_editor_property("cast_shadow", False)
    return a


def place_gate(label, center, yaw_deg, frame_mat, dot_mat):
    """Ворота — одна группа: точка label (MU_GateCW01; её и двигать/поворачивать в
    редакторе), к ней привязаны 4 бруса рамки label_L/R/T/B. Всё подвижное (Movable):
    ворота другого направления MultiUAV перед полётом уносит под карту."""
    cube = unreal.load_asset("/Engine/BasicShapes/Cube.Cube")
    sphere = unreal.load_asset("/Engine/BasicShapes/Sphere.Sphere")
    rot = unreal.Rotator(roll=0.0, pitch=0.0, yaw=yaw_deg)
    fwd = (math.cos(math.radians(yaw_deg)), math.sin(math.radians(yaw_deg)))
    right = (-fwd[1], fwd[0])
    half = OPENING_CM / 2 + BAR_CM / 2
    outer = OPENING_CM + 2 * BAR_CM
    dot = spawn_box(sphere, label, unreal.Vector(*center), rot, (DOT_CM,) * 3, dot_mat,
                    collide=False)
    parts = [  # (смещение вправо, вверх), размер (вдоль, поперёк, вверх)
        ("L", (-half, 0), (BAR_CM, BAR_CM, outer)),
        ("R", (half, 0), (BAR_CM, BAR_CM, outer)),
        ("T", (0, half), (BAR_CM, outer, BAR_CM)),
        ("B", (0, -half), (BAR_CM, outer, BAR_CM)),
    ]
    keep = unreal.AttachmentRule.KEEP_WORLD
    for name, (r, u), size in parts:
        loc = unreal.Vector(center[0] + right[0] * r, center[1] + right[1] * r, center[2] + u)
        bar = spawn_box(cube, f"{label}_{name}", loc, rot, size, frame_mat)
        bar.attach_to_actor(dot, "", keep, keep, keep, False)
    return dot


def main(argv):
    p = argparse.ArgumentParser()
    p.add_argument("--color", default="#969696",
                   help="цвет затемнения, как на экране: #RRGGBB (по умолчанию серый 150 — "
                        "средняя яркость сцены)")
    p.add_argument("--image", default="", help="картинка вместо цвета (png/jpg), растянута на экран")
    p.add_argument("--occlusion-only", action="store_true",
                   help="только цвет/картинку затемнения (для меню): ворота и объём не трогать")
    p.add_argument("--export-only", action="store_true",
                   help="ничего не создавать: выгрузить ворота MU_GateNN и зоны там, где они "
                        "сейчас стоят (после того как их передвинули в редакторе)")
    p.add_argument("--out", default=os.path.join(REPO, "maps", "FlyingExampleMap.json"))
    args = p.parse_args(argv)

    world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
    if not world or world.get_path_name().split(".")[0] != MAP:
        les.load_level(MAP)  # в редакторе с уже открытой картой — берём её как есть
    start = next(a for a in actors() if isinstance(a, unreal.PlayerStart))
    s = start.get_actor_location()
    capsule = start.get_component_by_class(unreal.CapsuleComponent)
    floor = s.z - capsule.get_scaled_capsule_half_height()
    zones = {a.get_name(): a for a in actors() if isinstance(a, unreal.TriggerBox)}
    walls = []
    for a in actors():
        if isinstance(a, unreal.StaticMeshActor) and "snake" in str(a.get_folder_path()):
            o, e = a.get_actor_bounds(False)
            walls.append((o, e))

    color = args.color.lstrip("#")
    color_srgb = [int(color[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    image = os.path.abspath(os.path.expanduser(args.image)) if args.image else ""
    old = {}
    if os.path.exists(args.out):
        old = json.loads(open(args.out, encoding="utf-8").read())
    if args.export_only:
        occlusion = {k: v for k, v in old.get("occlusion", {}).items() if k in ("color", "image")}
        mi = None
    else:
        mi = occlusion_material(color_srgb, image, rebuild=not args.occlusion_only)
        occlusion = {"color": "#" + color.lower(), "image": image or None}
    if args.occlusion_only:
        # только поправить цвет в уже выгруженном файле
        data = json.loads(open(args.out, encoding="utf-8").read())
        data["occlusion"].update(occlusion)
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        log(f"затемнение: {occlusion}")
        return
    if args.export_only:
        vol = next(a for a in actors() if a.get_actor_label() == OCCLUSION_LABEL)
    else:
        for a in actors():
            if unreal.Name(TAG) in a.tags:
                a.destroy_actor()
        frame_mat = material("M_MultiUAV_GateFrame", (1.0, 0.35, 0.0), 0.4)
        dot_mat = material("M_MultiUAV_GateDot", (1.0, 0.0, 0.0), 8.0)
        vol = occlusion_volume(mi)

    def ned(x, y, z):
        return {"north": round((x - s.x) / 100, 3), "east": round((y - s.y) / 100, 3),
                "alt": round((z - s.z) / 100, 3)}

    def progress(x, y):
        """Где точка (см Unreal) на кольце по часовой: (м вдоль осевой, (dX, dY) хода там)."""
        n, e = (x - s.x) / 100, (y - s.y) / 100
        best, walked = None, 0.0
        for (n0, e0), (n1, e1) in zip(CENTERLINE, CENTERLINE[1:]):
            dn, de = n1 - n0, e1 - e0
            length = math.hypot(dn, de)
            a = max(0.0, min(1.0, ((n - n0) * dn + (e - e0) * de) / length ** 2))
            dist = math.hypot(n - n0 - a * dn, e - e0 - a * de)
            if best is None or dist < best[0]:
                best = (dist, walked + a * length, (round(dn / length), round(de / length)))
            walked += length
        return best[1], best[2]

    # Зоны — все TriggerBox на карте (и скопированные вручную), по ходу трассы по часовой.
    boxes = []
    for z in zones.values():
        o, e = z.get_actor_bounds(False)
        along, axis = progress(o.x, o.y)
        if not args.export_only:
            fit_zone(z, axis, walls)
            o, e = z.get_actor_bounds(False)
        boxes.append((along, z, o, e, axis))
    boxes.sort(key=lambda b: b[0])
    out_zones = []
    for i, (along, z, o, e, axis) in enumerate(boxes):
        out_zones.append({"index": i, "name": z.get_name(), "label": z.get_actor_label(),
                          "center": ned(o.x, o.y, o.z), "along_m": round(along, 1),
                          "half_size": {"north": round(e.x / 100, 3), "east": round(e.y / 100, 3),
                                        "up": round(e.z / 100, 3)}})

    directions = {}
    for key, info in DIRECTIONS.items():
        sign = 1 if key == "cw" else -1
        order = list(range(len(boxes))) if key == "cw" else list(reversed(range(len(boxes))))
        prefix = f"MU_Gate{key.upper()}"
        gates = []
        for k, zi in enumerate(order):
            _, z, o, e, axis = boxes[zi]
            dx, dy = axis[0] * sign, axis[1] * sign
            label = f"{prefix}{k + 1:02d}"
            if args.export_only:
                dot = next((a for a in actors() if a.get_actor_label() == label), None)
                if dot is None:
                    raise RuntimeError(f"нет ворот {label} — запустите python -m multiuav track reset")
                loc = dot.get_actor_location()
                center, yaw = (loc.x, loc.y, loc.z), dot.get_actor_rotation().yaw
            else:
                exit_point = (o.x + dx * (e.x + GATE_AFTER_CM), o.y + dy * (e.y + GATE_AFTER_CM))
                (gx, gy), _ = corridor_center(exit_point, (dx, dy), walls)
                center = (gx, gy, floor + GATE_HEIGHT_CM)
                yaw = math.degrees(math.atan2(dy, dx))
                dot = place_gate(label, center, yaw, frame_mat, dot_mat)
            gates.append({"index": k, "name": label, "actor": dot.get_name(), "after_zone": k,
                          "center": ned(*center), "heading_deg": round(yaw % 360, 1),
                          "width_m": OPENING_CM / 100, "height_m": OPENING_CM / 100})
            log(f"{key} ворота {k + 1}: за {z.get_actor_label()}, {gates[-1]['center']}, курс {yaw:.0f}")
        directions[key] = {**info, "zones": order, "gates": gates}

    if not args.export_only:
        les.save_current_level()
    walls_ne = []
    for o, e in walls:
        n0, e0 = (o.x - e.x - s.x) / 100, (o.y - e.y - s.y) / 100
        walls_ne.append([round(n0, 2), round(e0, 2), round(n0 + 2 * e.x / 100, 2),
                         round(e0 + 2 * e.y / 100, 2)])
    data = {
        "map": MAP,
        "frame": "NED: north, east — м от PlayerStart; alt — м вверх от PlayerStart; "
                 "heading_deg — курс полёта через ворота, 0 — север, по часовой",
        "player_start_ue_cm": [round(s.x, 1), round(s.y, 1), round(s.z, 1)],
        "player_start_yaw_deg": round(start.get_actor_rotation().yaw, 1),
        "floor_alt": round((floor - s.z) / 100, 3),
        "occlusion": {**occlusion, "volume": vol.get_name(), "label": OCCLUSION_LABEL,
                      "size_m": OCCLUSION_SIZE_CM / 100,
                      "park_ned": [OCCLUSION_PARK_CM[0] / 100, OCCLUSION_PARK_CM[1] / 100,
                                   -OCCLUSION_PARK_CM[2] / 100]},
        "centerline": CENTERLINE,
        "walls": walls_ne,
        "zones": out_zones,  # по ходу по часовой; для направления — directions[…]["zones"]
        "directions": directions,
    }
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    log(f"записано {args.out}")
    print(f"MULTIUAV: записано {args.out}: зон {len(out_zones)}")


argv = sys.argv[1:]
if len(argv) == 1 and " " in argv[0]:
    argv = shlex.split(argv[0])
main(argv)
