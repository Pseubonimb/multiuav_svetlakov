# Правки плагина Cosys-AirSim 3.5.0 для MultiUAV

Плагин лежит в проекте Unreal, а не в этом репозитории
(`~/Downloads/Blocks 5.8/Plugins/AirSim`), поэтому правки хранятся здесь патчами.
Пути в патчах — от корня плагина (`Plugins/AirSim`).

| Патч | Что меняет | Зачем | Нужен? |
|---|---|---|---|
| `01-rpc-server-threads.patch` | `Source/SimMode/SimModeBase.cpp`: потоков RPC-сервера `число дронов × 2 + 16` вместо `+ 4` | Каждая команда `moveBy…Async` держит поток сервера до следующей. С окном камер (`multiuav cameras`) свободных потоков не оставалось, и шаг управления растягивался с 54 до 277 мс — пульт запаздывал. С патчем — 53 мс | **Да**, если используется окно камер |
| `02-fast-model.patch` | `AirSimSettings.hpp` (ключ `"Model"` у дрона), `SimpleFlightQuadXParams.hpp` (тяга ×k), `SimpleFlightApi.hpp` (наклон до 45°, вращение до 5 рад/с; коэффициенты, выдающие газ, делятся на k) | Быстрая модель: `"Model": "Fast2.5"` в settings.json (`model: fast2.5` в сценарии) — тяга ×2,5; `Fast` без числа — ×1,3. Регуляторы масштабируются под тягу, поэтому висение и AltHold работают | Для Acro — **да** (`rc_single.yaml`). Без `"Model": "Fast…"` ничего не меняет |

| `03-propeller-drag-area.patch` | `Source/AirLib/include/vehicles/multirotor/MultiRotorPhysicsBody.hpp`: площадь винта в сопротивлении воздуха `π·D²/4` вместо `π·D²` | Площадь 4 винтов была завышена в 4 раза — сверху дрон был как под зонтиком 0,68 м²: вязкий разгон и торможение, чем сильнее наклон, тем ниже предельная скорость. На висении (скорость 0) ничего не меняет, регуляторы не затрагивает | **Да** |
| `04-rotor-drag.patch` | `AirSimSettings.hpp` (ключ `"RotorDrag"` у дрона), `MultiRotorParams.hpp`, `PhysicsBody.hpp`, `MultiRotorPhysicsBody.hpp`, `FastPhysicsEngine.hpp`: сила сопротивления винтов `−k · тяга · скорость поперёк оси винтов` | В исходном AirSim её нет, только сопротивление корпуса ~v² — дрон скользит, как по льду (с 15 м/с катится 145 м). `"RotorDrag": 0.035` (`rotor_drag: 0.035` в сценарии) — 31 м | **Да**. Без ключа (`0`) ничего не меняет. Накладывать после 02 |
| `05-toggle-log-messages.patch` | `Source/SimHUD/SimHUD.h`, `SimHUD.cpp`: клавиша **F2** показывает и прячет отладочные строки в левом верхнем углу | `multiuav settings` пишет `"LogMessagesVisible": false` — строки скрыты с самого старта, F2 их возвращает. Без патча вернуть их можно только `settings --messages` и перезапуском Play | Да, если строки скрыты |

Подробности и замеры — `docs/MultiUAV-Cosys-UE5_Ubuntu22_experience.md`, разделы 18, 20–24.

## Наложить

```bash
cd "$HOME/Downloads/Blocks 5.8/Plugins/AirSim"
patch -p1 < ~/Documents/multiuav_svetlakov/airsim_patches/01-rpc-server-threads.patch
patch -p1 < ~/Documents/multiuav_svetlakov/airsim_patches/02-fast-model.patch   # нужен для 04
patch -p1 < ~/Documents/multiuav_svetlakov/airsim_patches/03-propeller-drag-area.patch
patch -p1 < ~/Documents/multiuav_svetlakov/airsim_patches/04-rotor-drag.patch
patch -p1 < ~/Documents/multiuav_svetlakov/airsim_patches/05-toggle-log-messages.patch
```

Затем закрыть Unreal и пересобрать (UBA должен быть отключён, см. раздел 7 файла опыта):

```bash
~/Downloads/UnrealEngine5/Engine/Build/BatchFiles/Linux/Build.sh BlocksEditor Linux Development -Project="$HOME/Downloads/Blocks 5.8/Blocks.uproject"
```

## Снять

```bash
cd "$HOME/Downloads/Blocks 5.8/Plugins/AirSim"
patch -p1 -R < ~/Documents/multiuav_svetlakov/airsim_patches/05-toggle-log-messages.patch
patch -p1 -R < ~/Documents/multiuav_svetlakov/airsim_patches/04-rotor-drag.patch
patch -p1 -R < ~/Documents/multiuav_svetlakov/airsim_patches/03-propeller-drag-area.patch
patch -p1 -R < ~/Documents/multiuav_svetlakov/airsim_patches/02-fast-model.patch
patch -p1 -R < ~/Documents/multiuav_svetlakov/airsim_patches/01-rpc-server-threads.patch
```

и пересобрать так же. На этой машине рядом с изменёнными файлами лежат и исходные копии:
`*.bak-before-rpc-threads`, `*.bak-before-fast-model`, `*.bak-before-prop-area`, `*.bak-before-fast-scale`, `*.bak-before-rotor-drag`, `*.bak-before-msg-toggle`.

Патчи сняты с Cosys-AirSim 3.5.0 (релиз `5.8-v3.5.0`); на другой версии плагина могут не наложиться.
