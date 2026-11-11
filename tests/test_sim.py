"""multiuav sim: миры Unreal (любой .uproject на диске) и прогон целиком в kinematic."""

import json
import sys

import pytest

from multiuav import sim


def _project(tmp_path):
    project = tmp_path / "MyWorld" / "MyWorld.uproject"
    (project.parent / "Content" / "Maps").mkdir(parents=True)
    project.write_text("{}")
    (project.parent / "Content" / "Field.umap").write_text("")
    (project.parent / "Content" / "Maps" / "City.umap").write_text("")
    # служебная папка World Partition — не карта
    (project.parent / "Content" / "__ExternalActors__" / "X").mkdir(parents=True)
    (project.parent / "Content" / "__ExternalActors__" / "X" / "A.umap").write_text("")
    return project


def test_project_maps_lists_game_paths(tmp_path):
    assert sim.project_maps(_project(tmp_path)) == ["/Game/Field", "/Game/Maps/City"]


def test_own_world_is_saved_locally_and_resolved(tmp_path):
    project = _project(tmp_path)
    local = tmp_path / "local.json"
    local.write_text(json.dumps({"time_server": "192.168.10.1"}))
    sim.save_world("field", str(project), "/Game/Field", "Поле", local=local)
    data = json.loads(local.read_text())
    assert data["time_server"] == "192.168.10.1"  # остальные настройки компьютера не трогаем
    worlds = sim.load_worlds(tmp_path / "нет.json", local)
    w = sim.resolve_world("field", None, None, worlds)
    assert (w["project"], w["map"], w["title"]) == (str(project), "/Game/Field", "Поле")
    # --map поверх мира — другая карта того же проекта
    assert sim.resolve_world("field", None, "/Game/Maps/City", worlds)["map"] == "/Game/Maps/City"


def test_wrong_world_or_map_is_explained(tmp_path):
    project = _project(tmp_path)
    worlds = {"worlds": {}, "engine": ""}
    with pytest.raises(SystemExit, match="нет мира"):
        sim.resolve_world("nope", None, None, worlds)
    with pytest.raises(SystemExit, match="нет карты /Game/Foo"):
        sim.resolve_world(None, str(project), "/Game/Foo", worlds)
    with pytest.raises(SystemExit, match="нет файла проекта"):
        sim.resolve_world(None, str(tmp_path / "x.uproject"), "/Game/Field", worlds)


def test_kinematic_run_puts_everything_in_one_folder(tmp_path, monkeypatch):
    out = tmp_path / "run"
    monkeypatch.setattr(sys, "argv", ["multiuav", "sim"])
    parser = __import__("argparse").ArgumentParser()
    sim.add_parser(parser.add_subparsers(), ("althold", "acro"))
    args = parser.parse_args(["sim", "scenarios/corner.yaml", "--backend", "kinematic",
                              "--followers", "2", "--rotor-drag", "0.02", "--out", str(out),
                              "--no-plot"])
    result = sim.run_sim(args)
    assert result["ok"] and result["drones"] == 3
    assert {p.name for p in out.iterdir()} >= {"sim.json", "scenario.yaml", "track.csv", "summary.json"}
    info = json.loads((out / "sim.json").read_text())
    assert info["rotor_drag"] == 0.02 and info["stopped_by"] == "полёт закончен"
