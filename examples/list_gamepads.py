"""Какие джойстики видит система и какой из них возьмёт ведущий gamepad_auto.

    python examples/list_gamepads.py              — список и выбранный геймпад
    python examples/list_gamepads.py --live       — потом показывать его оси (Ctrl+C — выход)
    python examples/list_gamepads.py --name msi   — искать по части имени
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from multiuav.algorithms.gamepad_auto import axes_for, list_joysticks, pick_joystick  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", help="часть имени геймпада")
    ap.add_argument("--live", action="store_true", help="показывать оси выбранного геймпада")
    args = ap.parse_args()

    os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    import pygame

    pygame.init()
    pygame.joystick.init()
    found = list_joysticks(pygame)
    if not found:
        raise SystemExit("Система не видит ни одного джойстика. Включите геймпад (кнопка Home).")
    index = pick_joystick(found, args.name)
    for i, name, n_axes in found:
        mark = "  <- геймпад для ведущего" if i == index else ""
        print(f"{i}: {name} ({n_axes} осей){mark}")
    if index is None:
        raise SystemExit("Подходящего геймпада нет (нужно не меньше 4 осей).")
    print(f"Раскладка: {axes_for(found[index][2])}")
    if not args.live:
        return

    js = pygame.joystick.Joystick(index)
    print("Подвигайте стики, Ctrl+C — выход.")
    try:
        while True:
            pygame.event.pump()
            axes = " ".join(f"{i}:{js.get_axis(i):+.2f}" for i in range(js.get_numaxes()))
            print("\r" + axes, end="", flush=True)
            time.sleep(0.05)
    except KeyboardInterrupt:
        print()


if __name__ == "__main__":
    main()
