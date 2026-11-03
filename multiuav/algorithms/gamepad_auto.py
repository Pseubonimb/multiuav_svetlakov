"""Ведущий с геймпада, который сам находит геймпад среди «джойстиков» системы.

Обычный `gamepad` берёт джойстик по номеру (joystick: 0). Но номера меняются:
Linux иногда считает джойстиком и клавиатуру или мышь, и тогда под номером 0
оказывается она, а геймпад — под номером 1, или наоборот, смотря кто подключился
раньше. Этот вариант выбирает геймпад по имени и по числу осей, номер не нужен.

В сценарии:
    leader:
      algorithm: multiuav.algorithms.gamepad_auto:AutoGamepadPilot
      params:
        name: 8bitdo      # необязательно: часть имени геймпада (регистр не важен)

Без name берётся первый джойстик, у которого не меньше 4 осей и в имени нет
Keyboard / Mouse. Если задан joystick: N — работает как обычный gamepad (по номеру).

Геймпад уснул или ещё не подключён — ведущий ждёт его до wait_s секунд (60),
печатая подсказку «нажмите Home». Отключился посреди полёта — ведущий получает
стики в центре (держит высоту и плавно тормозит), а когда геймпад снова появится,
управление возвращается само.

Оси: если в params нет axes, раскладка выбирается по геймпаду —
  6 и больше осей (геймпад как Xbox):          yaw 0, throttle 1, roll 3, pitch 4;
  4–5 осей (8BitDo в режиме Switch Pro и т. п.): yaw 0, throttle 1, roll 2, pitch 3.
Остальные параметры (invert, deadzone, mode, mode_switch) — как у gamepad.

Посмотреть, что видит система и какой джойстик будет выбран:
    python examples/list_gamepads.py
"""

from __future__ import annotations

import time
from typing import Any

from ..types import RcOverride
from .gamepad import DEFAULT_AXES, GamepadPilot

NOT_GAMEPAD = ("keyboard", "mouse")
SWITCH_AXES = {"yaw": 0, "throttle": 1, "roll": 2, "pitch": 3}


def list_joysticks(pygame) -> list[tuple[int, str, int]]:
    """(номер, имя, число осей) для всех джойстиков, которые видит SDL."""
    out = []
    for i in range(pygame.joystick.get_count()):
        js = pygame.joystick.Joystick(i)
        out.append((i, js.get_name(), js.get_numaxes()))
    return out


def pick_joystick(joysticks: list[tuple[int, str, int]], name: str | None) -> int | None:
    """Номер подходящего геймпада или None."""
    for i, js_name, n_axes in joysticks:
        low = js_name.lower()
        if name:
            if name.lower() in low:
                return i
        elif n_axes >= 4 and not any(word in low for word in NOT_GAMEPAD):
            return i
    return None


def axes_for(n_axes: int) -> dict[str, int]:
    return dict(DEFAULT_AXES) if n_axes >= 6 else dict(SWITCH_AXES)


class AutoGamepadPilot(GamepadPilot):
    """GamepadPilot, но геймпад ищется по имени, а раскладка осей — по геймпаду."""

    def param(self, name: str, default: Any) -> Any:
        if name == "joystick":
            return self._find_joystick()
        if name == "axes" and "axes" not in self.params:
            return axes_for(self._n_axes)
        return super().param(name, default)

    def _scan(self) -> tuple[list[tuple[int, str, int]], int | None]:
        pygame = self._pygame
        pygame.event.pump()  # SDL узнаёт о подключённых и отключённых устройствах
        found = list_joysticks(pygame)
        if "joystick" in self.params:
            index = int(self.params["joystick"])
            return found, index if index < len(found) else None
        return found, pick_joystick(found, self.params.get("name"))

    def _find_joystick(self) -> int:
        wait_s = float(self.params.get("wait_s", 60.0))
        deadline = time.monotonic() + wait_s
        found, index = self._scan()
        if index is None:
            print("[gamepad] геймпад не виден — нажмите на нём Home "
                  f"(жду до {wait_s:.0f} с)...", flush=True)
        while index is None and time.monotonic() < deadline:
            time.sleep(0.5)
            found, index = self._scan()
        if index is None:
            seen = "; ".join(f"{i}: {n} ({a} осей)" for i, n, a in found) or "ничего"
            want = f"с именем «{self.params['name']}»" if self.params.get("name") else ""
            raise RuntimeError(
                f"Геймпад {want} не найден. Система видит: {seen}. "
                "Включите геймпад (кнопка Home) и проверьте: python examples/list_gamepads.py")
        self._n_axes = found[index][2]
        self._name = found[index][1]
        self._count = len(found)
        self._lost = False
        self._next_check = 0.0
        axes = self.params.get("axes") or axes_for(self._n_axes)
        print(f"[gamepad] геймпад: {self._name} (номер {index}, осей {self._n_axes}); "
              f"оси: {axes}", flush=True)
        return index

    def _recheck(self) -> None:
        """Раз в секунду: не отключился ли геймпад, не появился ли снова."""
        now = time.monotonic()
        if now < self._next_check:
            return
        self._next_check = now + 1.0
        self._pygame.event.pump()
        if self._pygame.joystick.get_count() == self._count and not self._lost:
            return  # ничего не подключали и не отключали
        found, index = self._scan()
        self._count = len(found)
        alive = index is not None and found[index][1] == self._name
        if not alive:
            if not self._lost:
                print("[gamepad] геймпад отключился — ведущий держит высоту и тормозит; "
                      "нажмите на геймпаде Home", flush=True)
            self._lost = True
            return
        self.joystick = self._pygame.joystick.Joystick(index)
        self.joystick.init()
        if self._lost:
            print(f"[gamepad] геймпад снова на связи: {self._name}", flush=True)
        self._lost = False

    def step(self, me, others, leader, t) -> RcOverride:
        self._recheck()
        if self._lost:
            return RcOverride.from_normalized(mode=self.mode)
        try:
            return super().step(me, others, leader, t)
        except Exception as err:  # устройство пропало между проверками
            print(f"[gamepad] не удалось прочитать геймпад ({err})", flush=True)
            self._lost = True
            return RcOverride.from_normalized(mode=self.mode)
