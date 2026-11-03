#!/usr/bin/env bash
# Значок «Эксперимент БПЛА» на рабочем столе и в меню приложений: открывает меню эксперимента
# (python -m multiuav experiment) без терминала. Вывод меню — в runs/menu.log.
#   scripts/install_launcher.sh
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
DESKTOP_DIR="$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop")"
APPS="$HOME/.local/share/applications"
mkdir -p "$APPS" "$REPO/runs"
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
