"""Поставить в Cosys-AirSim стены из сценария (раздел world: buildings).

Сцена (Blocks.sh) должна быть уже запущена. Стены ставятся как растянутые кубы
через API симулятора (simSpawnObject). Координаты — те же, что в сценарии:
север, восток в метрах от точки старта (PlayerStart).

Порядок работы:
    # 1. какие «кубы» есть в сцене
    python examples/spawn_walls.py --list
    # 2. проверка масштаба: куб 10 x 10 x 10 м в 20 м к северу от старта
    python examples/spawn_walls.py --test
    #    Посмотрите в окне сцены: куб должен стоять на земле и быть заметно
    #    больше дрона (примерно в 20 раз). Если он висит в воздухе наполовину
    #    под землёй — добавьте --pivot bottom; если он в 100 раз меньше или
    #    больше — подберите --unit (размер исходного ассета в метрах).
    python examples/spawn_walls.py --remove
    # 3. стены по сценарию
    python examples/spawn_walls.py scenarios/line_blocks_city.yaml
    # 4. убрать стены
    python examples/spawn_walls.py --remove

Параметры: --asset ИМЯ (по умолчанию — первый подходящий куб из --list),
--unit (1.0 — размер ассета в метрах), --pivot center|bottom (center),
--host (127.0.0.1).
"""
import argparse
import sys

import yaml

PREFIX = "mu_wall_"
PREFERRED = ("1M_Cube_Chamfer", "1M_Cube", "Cube", "TemplateCube_Rounded", "Shape_Cube")


def connect(host):
    import cosysairsim as airsim
    client = airsim.MultirotorClient(ip=host)
    client.confirmConnection()
    return airsim, client


def cube_assets(client):
    names = client.simListAssets()
    return [n for n in names if "cube" in n.lower()]


def pick_asset(client, asked):
    if asked:
        return asked
    found = cube_assets(client)
    for pref in PREFERRED:
        for n in found:
            if n == pref:
                return n
    for pref in PREFERRED:
        for n in found:
            if pref.lower() in n.lower():
                return n
    if found:
        return found[0]
    sys.exit("В сцене не нашлось ассетов с «cube» в названии. Посмотрите полный список: "
             "python -c \"import cosysairsim as a; c=a.MultirotorClient(); print(c.simListAssets())\"")


def spawn_box(airsim, client, name, asset, north, east, height, unit, pivot):
    """Параллелепипед: north=(n0, n1), east=(e0, e1), от земли до height метров."""
    n0, n1 = north
    e0, e1 = east
    cn, ce = (n0 + n1) / 2, (e0 + e1) / 2
    z = -height / 2 if pivot == "center" else 0.0   # NED: вниз положительно
    pose = airsim.Pose(airsim.Vector3r(cn, ce, z), airsim.Quaternionr())
    scale = airsim.Vector3r((n1 - n0) / unit, (e1 - e0) / unit, height / unit)
    return client.simSpawnObject(name, asset, pose, scale, False)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("scenario", nargs="?")
    p.add_argument("--list", action="store_true")
    p.add_argument("--test", action="store_true")
    p.add_argument("--remove", action="store_true")
    p.add_argument("--asset")
    p.add_argument("--unit", type=float, default=1.0)
    p.add_argument("--pivot", choices=["center", "bottom"], default="center")
    p.add_argument("--host", default="127.0.0.1")
    a = p.parse_args()
    airsim, client = connect(a.host)

    if a.list:
        print("Ассеты с «cube» в названии:")
        for n in cube_assets(client):
            print("  ", n)
        return
    if a.remove:
        names = client.simListSceneObjects(PREFIX + ".*")
        for n in names:
            client.simDestroyObject(n)
        print(f"Удалено объектов: {len(names)}")
        return
    asset = pick_asset(client, a.asset)
    if a.test:
        got = spawn_box(airsim, client, PREFIX + "test", asset, (15, 25), (-5, 5), 10, a.unit, a.pivot)
        print(f"Поставлен тестовый куб 10 x 10 x 10 м ({asset}) как «{got}» в 15–25 м к северу от старта.")
        return
    if not a.scenario:
        p.error("укажите сценарий или --list / --test / --remove")
    data = yaml.safe_load(open(a.scenario, encoding="utf-8"))
    blds = (data.get("world") or {}).get("buildings") or []
    if not blds:
        sys.exit("В сценарии нет раздела world: buildings.")
    for i, b in enumerate(blds):
        got = spawn_box(airsim, client, f"{PREFIX}{i}", asset, b["north"], b["east"],
                        float(b.get("height", 50.0)), a.unit, a.pivot)
        print(f"  стена {i}: север {b['north']}, восток {b['east']}, высота {b.get('height')} м -> {got}")
    print(f"Готово: {len(blds)} стен из ассета {asset}.")


if __name__ == "__main__":
    main()
