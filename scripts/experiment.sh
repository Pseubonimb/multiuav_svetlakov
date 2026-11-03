#!/usr/bin/env bash
# Меню эксперимента (то же, что python -m multiuav experiment); так его запускает значок
# «Эксперимент БПЛА». Вывод — в runs/menu.log.
REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO" && mkdir -p runs
exec "$REPO/.venv/bin/python" -m multiuav experiment >> "$REPO/runs/menu.log" 2>&1
