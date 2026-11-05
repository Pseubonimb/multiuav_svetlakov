#!/usr/bin/env bash
# Значок «Эксперимент БПЛА» на рабочем столе и в меню приложений: открывает меню эксперимента
# (python -m multiuav experiment) без терминала. Вывод меню — в runs/menu.log.
#   scripts/install_launcher.sh
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
DESKTOP_DIR="$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop")"
APPS="$HOME/.local/share/applications"
mkdir -p "$APPS" "$REPO/runs"
# Модули Python, без которых меню не работает (ставятся в .venv через pip, не через apt).
if [[ ! -x "$REPO/.venv/bin/python" ]]; then
  echo "Нет окружения $REPO/.venv. Создайте его:"
  echo "  cd $REPO && python3 -m venv .venv && .venv/bin/pip install -e \".[cosys,gamepad,sticks,plot,dev]\""
  exit 1
fi
missing=$("$REPO/.venv/bin/python" - <<'PY'
import importlib.util
names = {"evdev": "sticks", "pygame": "gamepad", "cosysairsim": "cosys", "matplotlib": "plot",
         "tkinter": None, "yaml": None, "numpy": None}
print(" ".join(n for n in names if importlib.util.find_spec(n) is None))
PY
)
if [[ -n $missing ]]; then
  echo "В .venv не хватает модулей Python: $missing"
  echo "Поставить (из папки $REPO):"
  echo "  .venv/bin/pip install -e \".[cosys,gamepad,sticks,plot,dev]\""
  [[ $missing == *tkinter* ]] && echo "  sudo apt install python3-tk      # tkinter — для окна меню"
  exit 1
fi
FILE="$APPS/multiuav-experiment.desktop"
cat > "$FILE" <<DESK
[Desktop Entry]
Type=Application
Name=Эксперимент БПЛА
Comment=Меню эксперимента: участник, Старт/Стоп, обработка попытки
Exec="$REPO/scripts/experiment.sh"
Icon=$REPO/assets/experiment_icon.svg
Terminal=false
Categories=Science;
StartupNotify=true
DESK
chmod +x "$FILE"
cp "$FILE" "$DESKTOP_DIR/"
chmod +x "$DESKTOP_DIR/multiuav-experiment.desktop"
# GNOME запускает значок с рабочего стола, только если он помечен как доверенный.
gio set "$DESKTOP_DIR/multiuav-experiment.desktop" metadata::trusted true 2>/dev/null || true
echo "Готово: значок «Эксперимент БПЛА» на рабочем столе ($DESKTOP_DIR) и в меню приложений."
echo "Если на рабочем столе он с крестиком — правой кнопкой → «Разрешить запуск»."
