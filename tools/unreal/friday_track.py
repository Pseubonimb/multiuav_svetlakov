"""Карта Виталия для пробного эксперимента: ворота, серое затемнение, выгрузка геометрии.

Запускается внутри Unreal (Python редактора), без окна:

  UnrealEditor-Cmd ~/Desktop/New_Karta_Vitali/Blocks.uproject -run=pythonscript \
      -script="tools/unreal/friday_track.py --color '#969696'" -nullrhi -unattended

Ворота и зоны можно двигать в редакторе: точка MU_GateNN (папка MultiUAV/Gates) —
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

# Зоны по ходу трассы и направление полёта через зону (dX, dY) в осях Unreal. От старта
# летят на север по левому коридору (PlayerStart Виталия смотрит на восток — неверно;
# проверено полётом 04.11: зоны сработали в порядке 5, 3, 4, 2).
ZONES = [
    ("TriggerBox_4", (0, 1)),    # TriggerBox5, верхний коридор, на восток
    ("TriggerBox_2", (0, 1)),    # TriggerBox3
    ("TriggerBox_3", (0, 1)),    # TriggerBox4
    ("TriggerBox_0", (-1, 0)),   # TriggerBox2, правый коридор, на юг
    ("TriggerBox_1", (0, -1)),   # TriggerBox, нижний коридор, на запад
]
START_HEADING_DEG = 0.0  # нос на старте — на север
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


def spawn_box(mesh, label, loc, rot, size_cm, mat, collide=True):
    a = eas.spawn_actor_from_class(unreal.StaticMeshActor, loc, rot)
    if a is None:
        raise RuntimeError(f"Unreal не создал {label}")
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


def place_gate(index, center, yaw_deg, frame_mat, dot_mat):
    """Ворота — одна группа: точка MU_GateNN (её и двигать/поворачивать в редакторе),
    к ней привязаны 4 бруса рамки MU_GateNN_L/R/T/B."""
    cube = unreal.load_asset("/Engine/BasicShapes/Cube.Cube")
    sphere = unreal.load_asset("/Engine/BasicShapes/Sphere.Sphere")
    rot = unreal.Rotator(roll=0.0, pitch=0.0, yaw=yaw_deg)
    fwd = (math.cos(math.radians(yaw_deg)), math.sin(math.radians(yaw_deg)))
    right = (-fwd[1], fwd[0])
    half = OPENING_CM / 2 + BAR_CM / 2
    outer = OPENING_CM + 2 * BAR_CM
    dot = spawn_box(sphere, f"MU_Gate{index:02d}", unreal.Vector(*center), rot, (DOT_CM,) * 3,
                    dot_mat, collide=False)
    dot.root_component.set_mobility(unreal.ComponentMobility.STATIC)
    parts = [  # (смещение вправо, вверх), размер (вдоль, поперёк, вверх)
        ("L", (-half, 0), (BAR_CM, BAR_CM, outer)),
        ("R", (half, 0), (BAR_CM, BAR_CM, outer)),
        ("T", (0, half), (BAR_CM, outer, BAR_CM)),
        ("B", (0, -half), (BAR_CM, outer, BAR_CM)),
    ]
    keep = unreal.AttachmentRule.KEEP_WORLD
    for name, (r, u), size in parts:
        loc = unreal.Vector(center[0] + right[0] * r, center[1] + right[1] * r, center[2] + u)
        bar = spawn_box(cube, f"MU_Gate{index:02d}_{name}", loc, rot, size, frame_mat)
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
        dots = {a.get_actor_label(): a for a in actors() if unreal.Name(TAG) in a.tags}
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

    out_zones, out_gates = [], []
    for i, (name, (dx, dy)) in enumerate(ZONES):
        z = zones[name]
        o, e = z.get_actor_bounds(False)
        out_zones.append({"index": i, "name": name, "label": z.get_actor_label(),
                          "center": ned(o.x, o.y, o.z),
                          "half_size": {"north": round(e.x / 100, 3), "east": round(e.y / 100, 3),
                                        "up": round(e.z / 100, 3)},
                          "heading_deg": round(math.degrees(math.atan2(dy, dx)) % 360, 1)})
        if args.export_only:
            dot = dots.get(f"MU_Gate{i + 1:02d}")
            if dot is None:
                raise RuntimeError(f"нет ворот MU_Gate{i + 1:02d} — запустите скрипт без --export-only")
            loc = dot.get_actor_location()
            center = (loc.x, loc.y, loc.z)
            yaw = dot.get_actor_rotation().yaw
            width = None
        else:
            exit_point = (o.x + dx * (e.x + GATE_AFTER_CM), o.y + dy * (e.y + GATE_AFTER_CM))
            (gx, gy), width = corridor_center(exit_point, (dx, dy), walls)
            center = (gx, gy, floor + GATE_HEIGHT_CM)
            yaw = math.degrees(math.atan2(dy, dx))
            place_gate(i + 1, center, yaw, frame_mat, dot_mat)
        out_gates.append({"index": i, "name": f"MU_Gate{i + 1:02d}", "after_zone": i,
                          "center": ned(*center), "heading_deg": round(yaw % 360, 1),
                          "width_m": OPENING_CM / 100, "height_m": OPENING_CM / 100,
                          "corridor_m": None if width is None else round(width / 100, 2)})
        log(f"ворота {i + 1}: за {z.get_actor_label()}, {out_gates[-1]['center']}, курс {yaw:.0f}")

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
                 "heading_deg — курс полёта через ворота/зону, 0 — север, по часовой",
        "player_start_ue_cm": [round(s.x, 1), round(s.y, 1), round(s.z, 1)],
        "player_start_yaw_deg": round(start.get_actor_rotation().yaw, 1),
        "start_heading_deg": START_HEADING_DEG,
        "floor_alt": round((floor - s.z) / 100, 3),
        "occlusion": {**occlusion, "volume": vol.get_name(), "label": OCCLUSION_LABEL,
                      "size_m": OCCLUSION_SIZE_CM / 100,
                      "park_ned": [OCCLUSION_PARK_CM[0] / 100, OCCLUSION_PARK_CM[1] / 100,
                                   -OCCLUSION_PARK_CM[2] / 100]},
        "walls": walls_ne,
        "gates": out_gates,
        "zones": out_zones,
    }
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    log(f"записано {args.out}")
    print(f"MULTIUAV: записано {args.out}: ворот {len(out_gates)}, зон {len(out_zones)}")


argv = sys.argv[1:]
if len(argv) == 1 and " " in argv[0]:
    argv = shlex.split(argv[0])
main(argv)
