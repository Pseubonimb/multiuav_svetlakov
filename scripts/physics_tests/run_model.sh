#!/bin/bash
# Замер физики дрона в Unreal: пишет settings.json, запускает Blocks, гоняет тесты, закрывает Unreal.
#   scripts/physics_tests/run_model.sh МОДЕЛЬ ["P I D" ...]
#   RD=0.035 scripts/physics_tests/run_model.sh fast2.5           — с сопротивлением винтов
#   TESTS=glide,top,turn,brake scripts/physics_tests/run_model.sh fast2.5
#   ALT=1 scripts/physics_tests/run_model.sh fast2.5               — ещё и AltHold (althold.py)
# Без "P I D" — коэффициенты регулятора плагина; с ними — свои (setAngleRateControllerGains).
# Unreal не должен быть открыт: скрипт займёт порт 41451 и в конце закроет все UnrealEditor.
D="$(cd "$(dirname "$0")" && pwd)"
model=$1; shift
k=${model#fast}; case $model in generic) k=1;; fast) k=1.3;; esac; export FAST_K=$k
cd "$D/../.." && source .venv/bin/activate
tmp=$(mktemp -d)
sed "s/^model: .*/model: $model/" "$D/acro_test.yaml" > "$tmp/run.yaml"; echo "rotor_drag: ${RD:-0}" >> "$tmp/run.yaml"
python -m multiuav settings "$tmp/run.yaml" --write > /dev/null
__NV_PRIME_RENDER_OFFLOAD=1 __GLX_VENDOR_LIBRARY_NAME=nvidia ~/Downloads/UnrealEngine5/Engine/Binaries/Linux/UnrealEditor \
  "$HOME/Downloads/Blocks 5.8/Blocks.uproject" -game -windowed -ResX=1280 -ResY=720 -WinX=0 -WinY=0 -nosplash \
  "-ExecCmds=scalability 1" > "$tmp/unreal.log" 2>&1 &
UE=$!
for i in $(seq 1 120); do (echo > /dev/tcp/127.0.0.1/41451) 2>/dev/null && break; sleep 2; done; sleep 5
echo "===== модель $model, rotor_drag ${RD:-0} ====="
if [ $# -eq 0 ]; then set -- ""; fi
for g in "$@"; do
  timeout 300 python -u "$D/acrotest.py" $g 2>&1 | grep -v "^$\|Ver:\|Connected"
done
if [ -n "${ALT:-}" ]; then echo "  --- AltHold ---"; timeout 200 python -u "$D/althold.py" 2>&1 | grep -v "^$\|Ver:\|Connected"; fi
# Unreal не всегда закрывается по kill — тогда новый не займёт порт; поэтому затем kill -9
kill $UE; sleep 8; pgrep UnrealEditor | xargs -r kill -9; sleep 3
rm -rf "$tmp"
