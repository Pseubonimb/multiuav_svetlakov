# multiuav_svetlakov

Форк MultiUAV (https://github.com/annihilismus/multiuav): Python-среда, где оператор ведёт дрон-лидер, а ведомыми управляют подключаемые алгоритмы. Работает в упрощённом симуляторе `kinematic` или в Cosys-AirSim на Unreal Engine 5.8.

Опыт установки и первого запуска на этой машине (Ubuntu 22.04, UE 5.8.3, Cosys-AirSim 3.5.0): архитектура, точки входа, все найденные проблемы и их решения, шпаргалка команд. Прочитай перед помощью с настройкой или запуском:

@docs/MultiUAV-Cosys-UE5_Ubuntu22_experience.md

## Коротко

- Окружение: `source .venv/bin/activate` в корне форка.
- Запуск в Unreal: `python -m multiuav settings <сценарий> --write` → `ue5 "$HOME/Downloads/Blocks 5.8/Blocks.uproject"` → Play → `python -m multiuav run <сценарий> --backend cosys --out runs/NAME --plot runs/NAME.png`.
- Тесты: `python -m pytest -q`.
- Cesium for Unreal 2.30.0 установлен в движок (`Engine/Plugins/Marketplace`), карта `/Game/CesiumCity` и сценарий `scenarios/cesium_demo.yaml` — см. раздел 10 файла опыта.
- Демонстрация всего сразу: `scripts/demo.sh` (пульт AltHold/Acro, `--followers`, экраны камер, алгоритмы без связи `visual_follow`/`flock`) — см. раздел 14 файла опыта.
- Журнал стиков пульта RadioMaster (evdev, отдельный процесс): `python -m multiuav sticks list|map|log` — см. раздел 25 файла опыта.
- UBA в Unreal отключён (`~/.config/Unreal Engine/UnrealBuildTool/BuildConfiguration.xml`): с ним сборка AirSim зависала.
- Пользователь пишет по-русски и учится: объясняй, что делает каждая команда.
