#!/usr/bin/env bash
# Запуск всего сразу: settings.json -> Unreal (карта в режиме игры) -> экраны камер -> полёт.
#
#   scripts/demo.sh                      пульт в AltHold, 4 ведомых, карта Blocks
#   scripts/demo.sh -m acro              пульт в Acro
#   scripts/demo.sh -f 0                 только ведущий, без группы
#   scripts/demo.sh -a flock             ведомые — стая Рейнольдса
#   scripts/demo.sh -M cesium            карта CesiumCity (реальный мир)
#   scripts/demo.sh -e                   Unreal уже открыт и Play нажат — не запускать его
#   scripts/demo.sh -k                   без 3D: упрощённый симулятор kinematic
#   scripts/demo.sh -c bottom-left       экраны камер в другом углу окна игры
#   scripts/demo.sh -w -n                камеры 3 ведомых внутри окна Unreal (подокна AirSim, внизу)
#   scripts/demo.sh -d                   отладочные строки AirSim видны сразу (иначе — по F2 в окне игры)
#   scripts/demo.sh -q 3                 графика Epic (0 Low … 4 Cinematic; по умолчанию 1 — Medium:
#                                        игре нужно больше кадров в секунду, иначе камеры тормозят)
#
# Остановить: Ctrl+C. Журнал и картинка — в runs/demo_<время>/.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
UE="$HOME/Downloads/UnrealEngine5/Engine/Binaries/Linux/UnrealEditor"
PROJECT="$HOME/Downloads/Blocks 5.8/Blocks.uproject"
MAP="/Game/FlyingCPP/Maps/FlyingExampleMap"

scenario="scenarios/rc_group.yaml"
followers=4
mode=althold
algorithm=""
launch_ue=1
backend=cosys
cameras=1
corner=top-right
quality=1
subwindows=()
messages=()

usage() { sed -n '2,17p' "$0" | sed 's/^# \{0,1\}//'; echo
  echo "Опции: -s сценарий  -f ведомых  -m althold|acro  -a алгоритм  -M blocks|cesium|/Game/...";
  echo "       -e (Unreal уже открыт)  -k (kinematic, без 3D)  -n (без экранов камер)"
  echo "       -c top-right|top-left|bottom-right|bottom-left  -w (подокна AirSim внутри Unreal)  -d (отладочные строки сразу)"
  echo "       -q 0..4 (качество графики; по умолчанию 1 — Medium)"; }

while getopts "s:f:m:a:M:c:q:ekwndh" opt; do
  case $opt in
    s) scenario=$OPTARG ;;
    f) followers=$OPTARG ;;
    m) mode=$OPTARG ;;
    a) algorithm=$OPTARG ;;
    M) case $OPTARG in
         blocks) MAP="/Game/FlyingCPP/Maps/FlyingExampleMap" ;;
         cesium) MAP="/Game/CesiumCity" ;;
         *) MAP=$OPTARG ;;
       esac ;;
    e) launch_ue=0 ;;
    k) backend=kinematic; launch_ue=0; cameras=0 ;;
    n) cameras=0 ;;
    c) corner=$OPTARG ;;
    q) quality=$OPTARG ;;
    w) subwindows=(--subwindows) ;;
    d) messages=(--messages) ;;
    h|*) usage; exit 0 ;;
  esac
done

cd "$REPO"
source .venv/bin/activate
out="runs/demo_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$out"
run_args=(run "$scenario" --backend "$backend" --followers "$followers" --mode "$mode"
          --out "$out" --plot "$out.png")
[[ -n $algorithm ]] && run_args+=(--algorithm "$algorithm")

pids=()
cleanup() {
  for pid in "${pids[@]}"; do kill "$pid" 2>/dev/null || true; done
}
trap cleanup EXIT

if [[ $backend == cosys ]]; then
  echo ">> settings.json для $((followers + 1)) дронов"
  python -m multiuav settings "$scenario" --followers "$followers" "${subwindows[@]}" "${messages[@]}" --write

  if [[ $launch_ue == 1 ]]; then
    echo ">> Unreal: $MAP (лог — $out/unreal.log)"
    # Те же переменные, что в alias ue5: рисует дискретная NVIDIA.
    __NV_PRIME_RENDER_OFFLOAD=1 __GLX_VENDOR_LIBRARY_NAME=nvidia \
      "$UE" "$PROJECT" "$MAP" -game -windowed -ResX=1600 -ResY=900 -WinX=0 -WinY=0 -nosplash \
      "-ExecCmds=scalability $quality" \
      > "$out/unreal.log" 2>&1 &
    pids+=($!)
  fi

  echo ">> жду симулятор (порт 41451)..."
  for _ in $(seq 1 180); do
    (exec 3<>/dev/tcp/127.0.0.1/41451) 2>/dev/null && break
    sleep 1
  done
  (exec 3<>/dev/tcp/127.0.0.1/41451) 2>/dev/null \
    || { echo "Симулятор не ответил за 3 минуты. Лог: $out/unreal.log"; exit 1; }

  if [[ $cameras == 1 && $followers -gt 0 ]]; then
    echo ">> экраны камер ведомых"
    python -m multiuav cameras --followers "$followers" --corner "$corner" --game 1600x900+0+0 \
      > "$out/cameras.log" 2>&1 &
    pids+=($!)
  fi
fi

echo ">> полёт: ${run_args[*]}"
python -m multiuav "${run_args[@]}"
