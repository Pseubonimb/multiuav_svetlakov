# MultiUAV: путеводитель по программе

Шпаргалка, чтобы ориентироваться в проекте, править его и запускать на другом компьютере
без посторонней помощи. Подробная история проблем и замеров —
[MultiUAV-Cosys-UE5_Ubuntu22_experience.md](MultiUAV-Cosys-UE5_Ubuntu22_experience.md)
(дальше — «файл опыта»), физика моделей дрона — [drone_models.md](drone_models.md),
схема блоков — [architecture.html](architecture.html).

---

## 1. Из чего состоит система

Три программы работают одновременно и общаются только по сети:

```
 Пульт RadioMaster (USB Joystick)
        │  оси джойстика (pygame)                      ┌───────────────────────────────────┐
        ▼                                              │ Unreal Engine 5.8 (проект Blocks) │
 ┌────────────────────────────┐   RPC, TCP 41451       │  ┌─────────────────────────────┐  │
 │ python -m multiuav run     │ ─────────────────────► │  │ плагин Cosys-AirSim         │  │
 │  SwarmManager, 20 раз/с:   │  команды moveBy…       │  │  RPC-сервер :41451          │  │
 │  состояния → восприятие →  │ ◄───────────────────── │  │  SimpleFlight (автопилот)   │  │
 │  алгоритмы → команды → лог │  положения, лидар,     │  │  физика дрона, лидар, камеры│  │
 └────────────────────────────┘  столкновения          │  └─────────────────────────────┘  │
                                                       │  мир: Blocks / город / Cesium     │
 ┌────────────────────────────┐   RPC, TCP 41451       │  рендер, коллизии                 │
 │ python -m multiuav cameras │ ◄───────────────────── │                                   │
 │  окно с камерами ведомых   │  кадры, столкновения   └───────────────▲───────────────────┘
 └────────────────────────────┘                                        │ читает при старте игры
                                                    ~/Documents/AirSim/settings.json
 ┌────────────────────────────┐                     (пишет python -m multiuav settings)
 │ python -m multiuav sticks  │ ◄── /dev/input/eventN (evdev): стики ~500 раз/с в свой CSV
 └────────────────────────────┘
```

- **Unreal** — только мир, картинка и столкновения с геометрией. Полётом не управляет.
- **Cosys-AirSim** (плагин внутри Unreal) — физика дрона, автопилот SimpleFlight, датчики,
  RPC-сервер. Наши правки плагина — патчи в [airsim_patches/](../airsim_patches/README.md).
- **MultiUAV** — программа на Python, не плагин. Решает, что делать каждому дрону, и пишет лог.
- **settings.json** — единственная связь «через файл»: сколько дронов, где стоят, модель,
  лидар. Unreal читает его **только в момент старта игры** (Play или `-game`).
- Есть второй симулятор — **kinematic**: простая физика на Python, без Unreal и видеокарты.
  Для отладки алгоритмов и автотестов.

---

## 2. Где что лежит

### В репозитории (`~/Documents/multiuav_svetlakov`)

| Путь | Что это |
|---|---|
| `multiuav/` | программа (Python-пакет) |
| `scenarios/*.yaml` | сценарии полётов |
| `scripts/demo.sh` | запуск всего одной командой |
| `scripts/physics_tests/` | замеры физики дрона в Unreal (`run_model.sh`, `acrotest.py`, `althold.py`) |
| `airsim_patches/` | правки плагина Cosys-AirSim (патчи 01–05) и инструкция |
| `radio/sticks_map.json` | какой стик пульта какой оси соответствует (для `sticks`) |
| `tests/` | автотесты (`pytest`) |
| `examples/my_algorithm.py` | пример своего алгоритма |
| `docs/` | этот файл, файл опыта, модели дрона, схема |
| `runs/` | результаты полётов (в git не попадают) |
| `.venv/` | окружение Python (в git не попадает) |

### Вне репозитория — переносить отдельно

| Путь | Что это |
|---|---|
| `~/Downloads/UnrealEngine5/` | движок Unreal Engine 5.8.3 для Linux |
| `~/Downloads/UnrealEngine5/Engine/Plugins/Marketplace/CesiumForUnreal/` | плагин Cesium (только для карты реального мира) |
| `~/Downloads/Blocks 5.8/` | проект Unreal, ~2,6 ГБ: все карты и плагин AirSim |
| `~/Downloads/Blocks 5.8/Plugins/AirSim/` | плагин Cosys-AirSim 3.5.0 с наложенными патчами |
| `~/Downloads/Blocks 5.8/Content/` | карты: `FlyingCPP/Maps/FlyingExampleMap` (Blocks), `RealCityPolygonMap` (город), `CesiumCity`, `CesiumCity_Ekb`; ассеты города — `UnityCity/` |
| `~/Downloads/Blocks 5.8/Config/DefaultEngine.ini` | настройки проекта (аппаратная трассировка лучей выключена) |
| `~/Documents/AirSim/settings.json` | настройки AirSim, пишутся командой `settings` |
| `~/.config/Unreal Engine/UnrealBuildTool/BuildConfiguration.xml` | отключение UBA (без него сборка зависает) |
| `~/.bashrc`, строка с `alias ue5=…` | запуск редактора на видеокарте NVIDIA |
| `~/Documents/Polygon_Real_City/` | экспорт города из Unity и скрипты сборки города в UE5 |
| `~/Desktop/Karta_Vitali/Blocks.sh` | готовая сборка карты Виталия (наши патчи плагина в ней не действуют) |

---

## 3. Блоки программы `multiuav/`

| Модуль | Отвечает за | Править, если нужно… |
|---|---|---|
| `__main__.py` | командная строка: все команды и флаги | добавить команду или флаг |
| `scenario.py` | чтение YAML-сценария в `Scenario` (все ключи — раздел 6) | новый параметр сценария |
| `manager.py` | `SwarmManager`: главный цикл 20 Гц, клавиша L, запись `track.csv` и `summary.json` | изменить формат лога, условия остановки |
| `types.py` | `DroneState` (положение, скорость, курс, столкновение, дальномер), `RcOverride` (4 канала + режим) | новые данные о дроне |
| `control.py` | перевод стиков в физику: AltHold (углы, скорость подъёма), Acro (скорости вращения, тяга); помощник «скорость → стики» | чувствительность стиков |
| `perception.py` | кого из соседей ведомый видит сам: дальность, поле зрения, заслонение, шум | правила «зрения» |
| `algorithms/gamepad.py` | ведущий с пульта: оси, инверсия, мёртвая зона, тумблер режима | раскладка пульта |
| `algorithms/waypoints.py` | ведущий-автопилот по точкам | автоматический маршрут |
| `algorithms/vision.py` | ведомые без связи: `visual_follow`, `flock`; обход по лидару, мин. высота, посадка | поведение ведомых |
| `algorithms/single_file.py`, `leader_path.py` | старые ведомые: «гуськом» и «по следу» (нереалистичен) | — |
| `algorithms/__init__.py` | список встроенных алгоритмов и загрузка своих из файла | зарегистрировать новый алгоритм |
| `backends/base.py` | интерфейс симулятора: `start`, `states`, `send`, `advance`, `message`, `close` | новый симулятор |
| `backends/cosys.py` | Unreal + Cosys-AirSim: взлёт, команды, лидар → дальность по секторам, столкновения на экране | связь с AirSim |
| `backends/kinematic.py` | упрощённый симулятор: здания-коробки, AltHold/Acro, лучевой дальномер, мягкая посадка | отладка без Unreal |
| `cosys_settings.py` | сборка `settings.json` из сценария: дроны, лидар, модель, сопротивление винтов, подокна, строки | что получает Unreal |
| `cameras.py` | окно с камерами ведомых: поток на дрон, подсветка столкновений | экраны оператора |
| `menu.py` | меню эксперимента (tkinter), значки пульта/SE, полоски стиков | вид меню |
| `launch.py` | попытка целиком: процессы игры и записи пульта, закрытие по PID | порядок запуска |
| `experiment.py` | папка попытки, `experiment.json`, `drone0.csv`, `resets.csv`, сброс к воротам | столбцы журнала эксперимента |
| `occlusion.py` | затемнение в зонах: переносит объём `MU_Occlusion` к дрону на N мс | правила затемнения |
| `track.py` | ворота и зоны из `maps/<карта>.json`, пересечение плоскости ворот | геометрия трассы |
| `analyze.py` | постобработка: `gates.csv`, `presses.csv`, `occlusions.csv`, `track.png` | что считается |
| `xkeys.py` | клавиша R при любом окне в фокусе (X11) | клавиши экспериментатора |
| `keyboard.py` | клавиши в терминале без Enter (для L) | новые клавиши |
| `sticklog.py` | журнал стиков пульта через evdev, паспорт попытки | запись тремора |
| `plot.py` | картинка траекторий сверху | вид картинки |

### Один шаг главного цикла (`manager.py`, 20 раз в секунду)

1. `backend.states()` — положения, скорости, курсы, столкновения и дальномеры всех дронов.
2. Для ведомых «по зрению» `Observer` оставляет только видимых соседей.
3. Каждый алгоритм `step(me, others, leader, t)` возвращает `RcOverride`.
4. `backend.send(id, rc)` — в Cosys это `moveByRollPitchYawrateZAsync` (AltHold)
   или `moveByAngleRatesThrottleAsync` (Acro).
5. Строка в лог на каждого дрона.

---

## 4. Точки входа — команды

Перед любой командой:

```bash
cd ~/Documents/multiuav_svetlakov && source .venv/bin/activate
```

Помощь по любой команде: `python -m multiuav <команда> --help`, `scripts/demo.sh -h`.

| Команда | Что делает | Главные флаги |
|---|---|---|
| `python -m multiuav run СЦЕНАРИЙ` | один полёт | `--backend kinematic\|cosys` (по умолчанию kinematic), `--followers N`, `--mode althold\|acro`, `--algorithm`, `--leader`, `--out ПАПКА`, `--plot ФАЙЛ.png` |
| `python -m multiuav settings СЦЕНАРИЙ --write` | записать `~/Documents/AirSim/settings.json` | `--followers N`, `--model generic\|fast\|fast2.5…`, `--rotor-drag 0.035`, `--subwindows`, `--messages` |
| `python -m multiuav compare СЦЕНАРИЙ` | серия прогонов в kinematic | `--algorithms a,b`, `--drones 3,5,7,10`, `--plot` |
| `python -m multiuav cameras` | экраны камер ведомых поверх игры | `--followers N`, `--corner`, `--tile 256x144`, `--fps` |
| `python -m multiuav gamepad` | показать оси пульта/геймпада | `--index` |
| `python -m multiuav sticks list\|map\|button\|log` | журнал стиков через evdev; `button` — разметить кнопку (`--name SE`) | `--profile "…"`, `--adc-filter off`, `--out ПАПКА` |
| `python -m multiuav experiment` | **меню эксперимента** (окно): участник, затемнения, цвет/картинка затемнения, проверка пульта, Старт/Стоп/К воротам/Обработать | `--config experiments/friday.json` |
| `python -m multiuav launch ФАЙЛ.json` | одна попытка целиком (то, что делает «Старт»): settings.json → игра → запись пульта → полёт → закрыть всё | `--participant P07`, `--backend kinematic` (без Unreal), `--no-game`, `--no-sticks` |
| `python -m multiuav analyze ПАПКА` | обработка попытки: ворота, затемнения, нажатия SE | `--window 2` (с), `--track` |
| `python -m multiuav track edit\|export\|reset` | ворота и зоны карты: открыть в редакторе / выгрузить после правки / поставить заново | `--config` |
| `scripts/demo.sh` | всё сразу: settings → Unreal → камеры → run | см. ниже |
| `scripts/physics_tests/run_model.sh МОДЕЛЬ` | замер физики модели дрона | `RD=0.035`, `TESTS=…`, `ALT=1` |
| `python -m pytest -q` | автотесты | — |

**Правило числа дронов:** `--followers N` в `settings`, `run` и `cameras` должно быть
одинаковым. Иначе `run` напишет «В симуляторе нет дронов …».

### `scripts/demo.sh` — опции

| Опция | Значение |
|---|---|
| `-s сценарий` | по умолчанию `scenarios/rc_group.yaml` |
| `-f N` | ведомых (по умолчанию 4); `-f 0` — один ведущий |
| `-m althold\|acro` | режим пульта (не действует, если в сценарии включён тумблер `mode_switch`) |
| `-a алгоритм` | алгоритм ведомых (`visual_follow`, `flock`, `путь/файл.py:Класс`) |
| `-M blocks\|city\|cesium\|/Game/…` | карта (по умолчанию Blocks) |
| `-e` | Unreal уже открыт в редакторе — не запускать, ждать Play |
| `-k` | без 3D: kinematic |
| `-n` | без окна камер |
| `-c угол` | угол окна камер: `top-right` (по умолчанию), `top-left`, `bottom-right`, `bottom-left` |
| `-w` | камеры 3 ведомых во встроенных подокнах AirSim (обычно вместе с `-n`) |
| `-d` | отладочные строки AirSim видны сразу (иначе — по F2) |
| `-o модель`, `-r k` | модель дрона и сопротивление винтов |
| `-q 0..4` | качество графики (по умолчанию 1 — Medium) |

Результаты `demo.sh` — `runs/demo_<время>/`: `track.csv`, `summary.json`, `unreal.log`,
`cameras.log` и картинка `runs/demo_<время>.png`.

---

## 5. Варианты запуска (рецепты)

### 5.1. Без Unreal — проверить, что всё работает

```bash
python -m pytest -q                                        # все тесты должны пройти
python -m multiuav compare scenarios/corner.yaml --algorithms visual_follow,flock --plot runs/corner.png
scripts/demo.sh -k -s scenarios/corner.yaml -a visual_follow   # один полёт по коридору
```

### 5.2. Группа с пультом, без редактора (режим игры)

Unreal должен быть закрыт.

```bash
scripts/demo.sh                 # Blocks, пульт, 4 ведомых
scripts/demo.sh -M city         # город
scripts/demo.sh -M city -f 2 -a flock
```

### 5.3. Группа с пультом в редакторе (Play вручную)

```bash
ue5 "$HOME/Downloads/Blocks 5.8/Blocks.uproject" /Game/RealCityPolygonMap   # открыть карту
```

Дальше либо `scripts/demo.sh -e` и нажать **Play**, когда появится «жду симулятор», либо по шагам:

```bash
python -m multiuav settings scenarios/rc_group.yaml --followers 4 --write
# в редакторе: Play (Selected Viewport)
python -m multiuav run scenarios/rc_group.yaml --backend cosys --followers 4 --out runs/city1 --plot runs/city1.png
# во втором терминале, по желанию:
python -m multiuav cameras --followers 4
```

### 5.4. Автопилот — лидер летит сам по точкам

```bash
scripts/demo.sh -s scenarios/blocks_demo.yaml -a visual_follow          # квадрат над Blocks
scripts/demo.sh -M city -s scenarios/unity_city.yaml -a visual_follow  # кольцо над городом, 50 м
```

### 5.5. Один дрон с пультом (быстрая модель `fast2.5`, режим — тумблером на пульте)

```bash
scripts/demo.sh -s scenarios/rc_single.yaml -f 0
```

### 5.6. Карта реального мира (Cesium)

```bash
scripts/demo.sh -M cesium -s scenarios/cesium_demo.yaml -a visual_follow
```

Нужен токен Cesium ion в карте (файл опыта, разделы 10–13).

### 5.7. Журнал стиков для когнитивистов — параллельно с полётом

Во втором терминале, до начала попытки:

```bash
python -m multiuav sticks log --profile "Модель X: расходы 100%, expo 0" --adc-filter off --out runs/sticks_ИМЯ
```

Остановить — Ctrl+C. Склейка с журналом полёта — по столбцу `t_ms` (общие часы компьютера).

### 5.8. Эксперимент когнитивистов (один дрон, Acro, карта Виталия)

Значок **«Эксперимент БПЛА»** на рабочем столе и в меню приложений (поставить один раз:
`scripts/install_launcher.sh`; если на рабочем столе он с крестиком — правой кнопкой →
«Разрешить запуск»). То же из терминала:

```bash
python -m multiuav experiment
```

Всё остальное — кнопками меню. Попытка ложится в `runs/<код>/<ГГГГ-ММ-ДД_ЧЧ-ММ-СС>/`:
`experiment.json`, `drone0.csv` (20 строк/с), `sticks.csv`, `buttons.csv` (кнопка SE),
`passport.json`, `resets.csv`, `collisions.csv`, `occlusion_events.csv`, `unreal.log`, `summary.json`;
после «Обработать» — `gates.csv`, `presses.csv`, `occlusions.csv`, `contacts.csv`, `track.png`.

Без меню (то же самое): `python -m multiuav launch experiments/friday.json --participant P07`,
затем `python -m multiuav analyze runs/P07/<папка>`. Проверить без Unreal:
`python -m multiuav launch experiments/friday.json --participant test --backend kinematic --no-sticks`.

Карта: `~/Desktop/New_Karta_Vitali`, `/Game/FlyingCPP/Maps/FlyingExampleMap` (в `BigTrackMap`
трассы нет — это пустой шаблон Open World). Ворота и затемнение в карту ставит
`tools/unreal/friday_track.py` (файл опыта, раздел 27); он же пишет `maps/FlyingExampleMap.json`.

### 5.9. Клавиши во время полёта

| Где | Клавиша | Действие |
|---|---|---|
| терминал с `run` | **L** (в русской раскладке «Д») | посадить ведомых / поднять обратно |
| терминал с `run` | **Ctrl+C** | закончить полёт, сохранить лог |
| любое окно, во время `launch`/меню | **R** (русская «К») | дрон — в центр последних пройденных ворот, скорость 0 |
| окно игры | **F2** | показать/спрятать отладочные строки AirSim |
| окно игры | **F**, **B**, **M**, **F1** | вид из дрона, камера сзади, свободная камера, справка AirSim |
| редактор | **Esc** | остановить Play |

---

## 6. Сценарий YAML — все ключи

Ключи `model`, `rotor_drag`, `drones` и прочие верхнего уровня пишутся **без отступа**,
не внутри `leader:`/`params:` — иначе их молча пропустят.

| Ключ | Пример | Смысл |
|---|---|---|
| `name` | `rc_group` | имя для сводки |
| `drones` | `5` | всего дронов; флаг `--followers N` на один запуск заменяет его на N + 1 |
| `rate_hz` | `20` | частота цикла управления |
| `duration_s` | `120` или `null` | длительность; `null` — до Ctrl+C |
| `start` | `{north, east, altitude, heading_deg, spacing}` | точка старта ведущего, высота, курс, шаг колонны |
| `leader` | `{algorithm: gamepad, params: {...}}` | кто ведёт: `gamepad` или `waypoints` |
| `leader.params` для `gamepad` | `axes`, `invert`, `deadzone`, `mode`, `mode_switch` | раскладка пульта и режим |
| `leader.params` для `waypoints` | `speed`, `waypoints: [[north, east, alt], …]` | маршрут автопилота |
| `followers` | `{algorithm: visual_follow, params: {...}}` | алгоритм ведомых |
| `followers.params` | `distance`, `max_speed`, `min_altitude`, `safe`, `width`, `lookahead`, `separation`, `max_predict` | дистанция, скорость, обход |
| `perception` | `lidar_range`, `sectors`, `vision_range`, `fov_deg`, `noise` | лидар и «зрение» ведомых |
| `limits` | `max_tilt_deg`, `max_yaw_rate_deg`, `max_climb`, `max_rate_deg`, `hover_throttle` | что значит полный стик |
| `model` | `generic`, `fast`, `fast2.5` | модель дрона в Cosys (патч 02) |
| `rotor_drag` | `0.035` | сопротивление винтов, с/м (патч 04); 0 — «лёд» |
| `rate_gains` | `{p, i, d}` | свои коэффициенты регулятора Acro (обычно не задаются) |
| `world.buildings` | `[{north: [a,b], east: [c,d], height}]` | здания — **только для kinematic** |
| `finish` | `{north: [a,b], east: [c,d]}` | финишная зона |
| `stop_on_collision` | `false` | для ручного полёта: столкновение не останавливает |
| `show_rc` | `true` | каналы пульта поверх окна Unreal |

Сценарии в проекте:

| Файл | Для чего |
|---|---|
| `rc_group.yaml` | пульт + 4 ведомых `visual_follow` — главный для экспериментов |
| `rc_single.yaml` | один дрон с пультом, Acro, быстрая модель |
| `blocks_demo.yaml` | автопилот, квадрат над Blocks |
| `unity_city.yaml` | автопилот, кольцо над городом на 50 м |
| `cesium_demo.yaml` | автопилот над реальным миром |
| `corner.yaml` | коридор с поворотом — только kinematic, для отладки алгоритмов |

Оси: NED — север, восток, высота вверх в метрах от точки старта ведущего. Курс 0 — север,
по часовой — плюс.

---

## 7. Что программа пишет

| Файл | Содержимое |
|---|---|
| `ПАПКА/track.csv` | строка на дрона 20 раз/с: `t, drone, north, east, altitude, yaw, collided, rc_roll, rc_pitch, rc_throttle, rc_yaw, rc_mode` |
| `ПАПКА/summary.json` | сколько дошло, сколько столкнулось, время первого столкновения каждого |
| `ФАЙЛ.png` | траектории сверху |
| `runs/demo_…/unreal.log` | вывод Unreal при запуске через `demo.sh` |
| `runs/demo_…/cameras.log` | FPS камер, ошибки окна камер |
| `runs/sticks_…/sticks.csv`, `passport.json` | журнал стиков и паспорт попытки |
| `Blocks 5.8/Saved/Logs/Blocks.log` | полный лог Unreal (падения, ошибки AirSim) |
| `~/.config/Epic/UnrealBuildTool/Log.txt` | лог сборки плагина |

Касания земли тоже считаются столкновениями; с чем столкнулся дрон, в лог не пишется
(видно на экране: `COLLISION DroneN: объект` по F2).

---

## 8. Перенос на другой компьютер

Проверено на Ubuntu 22.04 с NVIDIA RTX 4050 Laptop. Шаги по порядку:

1. **Драйвер NVIDIA** — проверка `nvidia-smi`.
2. **Unreal Engine 5.8 для Linux** — скачать с unrealengine.com/linux (нужен аккаунт Epic),
   распаковать в `~/Downloads/UnrealEngine5`.
3. **Отключить UBA** — иначе сборка плагина зависает на 1 % (файл опыта, раздел 7):
   `~/.config/Unreal Engine/UnrealBuildTool/BuildConfiguration.xml` с
   `<bAllowUBAExecutor>false</bAllowUBAExecutor>`.
4. **Проект Unreal.** Проще всего скопировать всю папку `~/Downloads/Blocks 5.8` с этого
   компьютера (в ней все карты, город и плагин с патчами). Папки `Saved/` и `Intermediate/`
   можно не копировать. Если начинать с нуля: `Blocks_editor_project_58_350.zip` и
   `AirSim_plugin_Linux_58_350.zip` с github.com/Cosys-Lab/Cosys-AirSim/releases
   (тег `5.8-v3.5.0`), плагин — в `Blocks 5.8/Plugins/AirSim`, затем патчи:
   ```bash
   cd "$HOME/Downloads/Blocks 5.8/Plugins/AirSim"
   for p in ~/Documents/multiuav_svetlakov/airsim_patches/0*.patch; do patch -p1 < "$p"; done
   ```
5. **Выключить аппаратную трассировку лучей** в `Blocks 5.8/Config/DefaultEngine.ini`
   (`r.RayTracing`, `r.RayTracing.Skylight`, `r.RayTracing.Shadows`, `r.Lumen.HardwareRayTracing`,
   `r.Vulkan.RayTracing` = `False`) — иначе Unreal падает (раздел 11). В скопированной папке уже сделано.
6. **Собрать проект:**
   ```bash
   ~/Downloads/UnrealEngine5/Engine/Build/BatchFiles/Linux/Build.sh BlocksEditor Linux Development -Project="$HOME/Downloads/Blocks 5.8/Blocks.uproject"
   ```
   В конце должно быть `Result: Succeeded`.
7. **Cesium** (только для `-M cesium`) — `CesiumForUnreal-58-v2.30.0.zip` в
   `~/Downloads/UnrealEngine5/Engine/Plugins/Marketplace/` (раздел 10).
8. **Alias и окна** — в `~/.bashrc`:
   ```bash
   alias ue5='__NV_PRIME_RENDER_OFFLOAD=1 __GLX_VENDOR_LIBRARY_NAME=nvidia ~/Downloads/UnrealEngine5/Engine/Binaries/Linux/UnrealEditor'
   ```
   и `gsettings set org.gnome.mutter check-alive-timeout 60000` (чтобы Ubuntu не спрашивала
   «не отвечает» во время сборки шейдеров).
9. **MultiUAV:**
   ```bash
   git clone https://github.com/Pseubonimb/multiuav_svetlakov.git ~/Documents/multiuav_svetlakov
   cd ~/Documents/multiuav_svetlakov && python3 -m venv .venv && source .venv/bin/activate
   pip install -e ".[cosys,gamepad,sticks,plot,dev]"
   python -m pytest -q
   ```
   Путь к проекту и движку `scripts/demo.sh` берёт из `~/Downloads/...` — если положили
   в другое место, поправьте `UE=` и `PROJECT=` в начале скрипта.
10. **Первый запуск:** `ue5 "$HOME/Downloads/Blocks 5.8/Blocks.uproject"` — компиляция шейдеров
    долгая, только в первый раз. Потом `scripts/demo.sh`.
11. **Пульт:** RadioMaster по USB в режиме USB Joystick, `python -m multiuav gamepad` —
    сверить оси с `leader.params.axes` в `rc_group.yaml`. Для `sticks` —
    `python -m multiuav sticks map`, если пульт или модель другие.

---

## 9. Частые проблемы

| Симптом | Причина | Что делать |
|---|---|---|
| `Connection refused`, run ждёт | игра не запущена (нет Play) | нажать Play / запустить `demo.sh` без `-e` |
| «В симуляторе нет дронов …» | `settings.json` на другое число дронов или записан после Play | `settings --followers N --write`, затем Play заново |
| Сборка висит на 1 % десятки минут | UBA | раздел 8, шаг 3 |
| `VK_ERROR_DEVICE_LOST`, Unreal падает | смена окна на гибридной графике / трассировка лучей | не менять размер окна; трассировка выключена (шаг 5) |
| Unreal падает при старте окна камер (`SparseArray.h`) | гонка при включении камер | исправлено в `cameras.py` (`warm_up`); не откатывать |
| Пульт запаздывает при открытых камерах | мало потоков RPC-сервера | патч `01-rpc-server-threads` |
| Ведомые висят и никого не видят | лидар видит свой корпус или землю | патч уже в коде (`LIDAR_SELF`), ведомые не ниже 3 м |
| После посадки не взлетают | см. раздел 19 файла опыта | клавиша L |
| Дрон «на льду» | нет сопротивления винтов | `rotor_drag: 0.035` и патч 04 |
| `--mode acro` не действует | в сценарии включён тумблер `mode_switch` | переключать тумблером или закомментировать `mode_switch` |
| `pkill -f …` убил сам терминал | шаблон совпал с командой терминала | закрывать по PID: `pgrep -x UnrealEditor`, затем `kill` |
| На карте Виталия патчи не работают | плагин вшит в его сборку | ему собрать игру с патчами 01–05 |

---

## 10. Как править проект — куда смотреть

| Хочу | Где |
|---|---|
| Новый алгоритм ведомых | свой файл с классом от `FollowerAlgorithm` (пример `examples/my_algorithm.py`), запуск `--algorithm путь.py:Класс`; встроить — `algorithms/__init__.py` |
| Чтобы ведомый видел только «глазами» | `vision_only = True` в классе, или наследовать `VisionAlgorithm` |
| Новый параметр сценария | `scenario.py` (поле + чтение в `from_dict`) |
| Новый столбец в логе | `manager.py`, `_record` и заголовок в `RunResult.save` |
| Что уходит в Unreal | `cosys_settings.py` |
| Как команда превращается в движение | `control.py` + `backends/cosys.py` (`send`) |
| Поменять физику или автопилот дрона | исходники плагина в `Blocks 5.8/Plugins/AirSim/Source`, потом новый патч в `airsim_patches/` и пересборка |
| Новая опция запуска | `scripts/demo.sh` |
| Проверить, что ничего не сломал | `python -m pytest -q` и полёт `scripts/demo.sh -k -s scenarios/corner.yaml -a visual_follow` |

Новый патч плагина снимать с резервной копии: `diff -u --label a/ПУТЬ --label b/ПУТЬ ПУТЬ.bak ПУТЬ`.
Файлы плагина с окончаниями строк CRLF: не переписывать их Python-скриптом (раздел 23).

---

## 11. Вопросы для самопроверки

Ответ — под каждым вопросом, нажмите, чтобы раскрыть.

<details><summary>1. Кто считает физику полёта дрона: Unreal или Cosys-AirSim?</summary>

Cosys-AirSim (плагин внутри Unreal) и его автопилот SimpleFlight. Unreal даёт мир, картинку
и столкновения с геометрией.
</details>

<details><summary>2. Как MultiUAV передаёт команды в симулятор и через какой порт?</summary>

По RPC (msgpack-rpc поверх TCP), порт 41451. Это вызовы функций AirSim, а не MAVLink.
</details>

<details><summary>3. Вы поменяли число ведомых. Что нужно сделать перед полётом?</summary>

Заново записать `settings.json` (`settings … --followers N --write`) и перезапустить игру:
Unreal читает файл только при Play / старте `-game`. И передать тот же `--followers N` в `run`
и `cameras`. `demo.sh` делает всё сам.
</details>

<details><summary>4. Чем отличаются `scripts/demo.sh` и `scripts/demo.sh -e`?</summary>

Без `-e` скрипт сам запускает Unreal в режиме игры и закрывает его после Ctrl+C. С `-e` Unreal
уже открыт в редакторе: скрипт пишет `settings.json` и ждёт, пока вы нажмёте Play.
</details>

<details><summary>5. Где проверить алгоритм ведомых, не запуская Unreal?</summary>

В kinematic: `scripts/demo.sh -k -s scenarios/corner.yaml -a visual_follow` или
`python -m multiuav compare scenarios/corner.yaml --algorithms visual_follow,flock`.
</details>

<details><summary>6. Почему ведомый в Unreal может вести себя иначе, чем в kinematic?</summary>

В Unreal лидар видит соседние дроны как препятствия, физика AirSim с инерцией и автопилотом;
в kinematic дальномер видит только здания, физика упрощённая.
</details>

<details><summary>7. Что делает клавиша L и в каком окне её нажимать?</summary>

Сажает ведомых (мягко, вертикально), повторное нажатие — поднимает обратно. Нажимать в
терминале, где идёт `run` (или `demo.sh`), а не в окне игры.
</details>

<details><summary>8. Почему ведомые не опускаются ниже 3 м?</summary>

Ниже ~2,5 м их лидар задевает землю и принимает её за стены — ведомый «слепнет». Поэтому
`min_altitude: 3`, а садиться — только по L.
</details>

<details><summary>9. В каком файле лежит лог полёта и какие в нём столбцы?</summary>

`ПАПКА/track.csv` (папка из `--out` или `runs/demo_<время>/`): время, номер дрона, север,
восток, высота, курс, столкновение, 4 канала пульта и режим.
</details>

<details><summary>10. Вы запустили `--mode acro`, а дрон летит в AltHold. Почему?</summary>

В сценарии включён `mode_switch` — режим задаёт тумблер на пульте, и он главнее флага.
</details>

<details><summary>11. Какой патч плагина нужен, чтобы пульт не тормозил при открытом окне камер?</summary>

`01-rpc-server-threads.patch`: больше потоков RPC-сервера. Без него камеры занимают все потоки,
и шаг управления растягивается с ~50 до ~280 мс.
</details>

<details><summary>12. Что скопировать, чтобы перенести карты и плагин на новый компьютер?</summary>

Папку проекта `~/Downloads/Blocks 5.8` (кроме `Saved/` и `Intermediate/`), затем собрать
`Build.sh`. Отдельно: движок UE 5.8, отключение UBA, `alias ue5`, репозиторий с `pip install`.
</details>

<details><summary>13. Где искать причину падения Unreal?</summary>

`Blocks 5.8/Saved/Logs/Blocks.log` (при `demo.sh` — ещё `runs/demo_…/unreal.log`). Ошибки сборки
плагина — `~/.config/Epic/UnrealBuildTool/Log.txt`.
</details>

<details><summary>14. Куда добавить новый параметр, чтобы его можно было писать в сценарии?</summary>

В `multiuav/scenario.py`: поле в `Scenario` и чтение в `from_dict`. Если он нужен Unreal —
ещё в `cosys_settings.py`.
</details>

<details><summary>15. Почему `model: fast2.5`, записанный внутри `leader: params:`, не работает?</summary>

`model` — ключ верхнего уровня сценария. Внутри `params` его читает только алгоритм ведущего
и молча игнорирует.
</details>

<details><summary>16. Как записать стики пульта с высокой частотой для когнитивистов?</summary>

Во втором терминале `python -m multiuav sticks log --profile "…" --adc-filter off --out runs/ИМЯ`.
Пишет строку при каждом движении стика (пульт шлёт до ~500 пачек в секунду) в `sticks.csv` и паспорт в `passport.json`; склеивать с полётом — по `t_ms`.
</details>
