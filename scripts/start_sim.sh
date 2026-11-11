#!/usr/bin/env bash
# Запуск симулятора для исследований: то же, что python -m multiuav sim (его же зовёт меню).
#
#   scripts/start_sim.sh scenarios/rc_group.yaml --world city --followers 4 --out runs/city1
#   scripts/start_sim.sh scenarios/corner.yaml --backend kinematic --leader waypoints
#   scripts/start_sim.sh scenarios/x.yaml --project ~/UE/MyWorld/MyWorld.uproject --map /Game/Maps/Field
#   scripts/start_sim.sh scenarios/rc_group.yaml --world city --editor     в редакторе, Play — сами
#   scripts/start_sim.sh --list-worlds                                   известные миры
#   scripts/start_sim.sh --list-maps ~/UE/MyWorld/MyWorld.uproject       карты своего проекта
#   scripts/start_sim.sh --save-world field --project … --map /Game/…    запомнить свой мир
#
# Все опции: scripts/start_sim.sh --help. scripts/demo.sh — короткая демонстрация, меню его не зовёт.
set -euo pipefail
cd "$(dirname "$0")/.."
source .venv/bin/activate
exec python -m multiuav sim "$@"
