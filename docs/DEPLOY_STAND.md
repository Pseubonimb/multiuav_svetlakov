# Как развернуть экспериментальный стенд на другом компьютере

Пошагово: от чистой Ubuntu до значка «Эксперимент БПЛА», который запускает всю попытку
(меню → игра → запись пульта → полёт → обработка). Проверено 05.11.2026 при переносе
на компьютер центра когнитивных исследований. Подробности о каждой проблеме — в
[файле опыта](MultiUAV-Cosys-UE5_Ubuntu22_experience.md), раздел 27; о командах —
в [путеводителе](PROGRAM_GUIDE.md).

## 0. Что нужно

| | |
|---|---|
| ОС | Ubuntu 22.04, сеанс **X11** (на экране входа — шестерёнка → «Ubuntu on Xorg»). Под Wayland не работает клавиша R |
| Видеокарта | NVIDIA с драйвером: команда `nvidia-smi` показывает карту |
| Диск | ~40 ГБ: движок ~25 ГБ, проект ~4 ГБ, кэш шейдеров |
| Пульт | RadioMaster (EdgeTX) по USB в режиме **USB Joystick (HID)** |
| С ноутбука-источника | папка проекта Unreal `~/Desktop/New_Karta_Vitali` (с картой, воротами и патчами плагина) |

Пути ниже — как на ноутбуке. Если положили иначе — поправьте `engine` и `project` в
`experiments/friday.json` (шаг 7).

## 1. Unreal Engine 5.8 для Linux

1. Скачать архив Linux-версии с unrealengine.com/linux (нужен аккаунт Epic) и распаковать в
   `~/Downloads/UnrealEngine5`. Проверка: есть файл
   `~/Downloads/UnrealEngine5/Engine/Binaries/Linux/UnrealEditor`.
2. Отключить UBA — иначе сборка плагина зависает на 1 %. Создать файл
   `~/.config/Unreal Engine/UnrealBuildTool/BuildConfiguration.xml`:
   ```xml
   <?xml version="1.0" encoding="utf-8" ?>
   <Configuration xmlns="https://www.unrealengine.com/BuildConfiguration">
     <BuildConfiguration>
       <bAllowUBAExecutor>false</bAllowUBAExecutor>
     </BuildConfiguration>
   </Configuration>
   ```
3. Чтобы Ubuntu не спрашивала «программа не отвечает», пока Unreal компилирует шейдеры:
   ```bash
   gsettings set org.gnome.mutter check-alive-timeout 60000
   ```

## 2. Проект Unreal с картой

Скопировать с ноутбука папку `~/Desktop/New_Karta_Vitali` **без** `Saved`, `Intermediate` и
`DerivedDataCache` (это кэш и логи, они пересоздаются), например на флешку:

```bash
rsync -a --exclude Saved --exclude Intermediate --exclude DerivedDataCache ~/Desktop/New_Karta_Vitali/ /media/$USER/ФЛЕШКА/New_Karta_Vitali/
```

и на новом компьютере — обратно в `~/Desktop/New_Karta_Vitali`. В этой папке уже есть всё:
три карты уровней сложности (`FlyingExampleMap` — сложный, `Track_Medium`, `Track_Easy` в
`Content/FlyingCPP/Maps/`) с воротами и зонами затемнения и плагин AirSim с наложенными
патчами `airsim_patches/01–06` (`-a` сохраняет права и даты). Если копировали проект раньше,
чем появились уровни, — либо докопировать два файла `Track_*.umap`, либо после шага 3
выполнить `python -m multiuav track variants`.

Собрать C++-часть проекта и плагина под свой движок (3–5 минут, в конце `Result: Succeeded`):

```bash
~/Downloads/UnrealEngine5/Engine/Build/BatchFiles/Linux/Build.sh BlocksEditor Linux Development -Project="$HOME/Desktop/New_Karta_Vitali/Blocks.uproject"
```

> Начинать с чистого проекта Виталия (без наших правок): наложить патчи на `Plugins/AirSim`
> (`airsim_patches/README.md`), собрать, затем поставить ворота и затемнение командой
> `python -m multiuav track reset` (после шага 3). Файлы плагина с окончаниями строк CRLF —
> не переписывать Python-скриптом.

## 3. Программа MultiUAV

1. Системные пакеты: `git`, окружение Python, окно меню (`tk`) и инструменты сборки модуля
   `evdev`:
   ```bash
   sudo apt install git python3-venv python3-tk python3-dev gcc
   ```
2. Код — ветка эксперимента:
   ```bash
   git clone -b friday-trial https://github.com/Pseubonimb/multiuav_svetlakov.git ~/Documents/multiuav_svetlakov
   ```
3. Окружение и **все** модули Python. Ставятся через `pip` внутрь `.venv`, а не через `apt`:
   `evdev` — чтение пульта, `pygame` — пульт в полёте, `cosysairsim` — связь с игрой.
   ```bash
   cd ~/Documents/multiuav_svetlakov && python3 -m venv .venv && .venv/bin/pip install -e ".[cosys,gamepad,sticks,plot,dev]"
   ```
4. Проверка без Unreal — все тесты должны пройти:
   ```bash
   .venv/bin/python -m pytest -q
   ```

## 4. Пульт

1. Подключить пульт по USB и выбрать на нём **USB Joystick (HID)**.
2. Проверить, что программа видит его **без sudo** — строка `EdgeTX Radiomaster Pocket Joystick`
   и 8 осей:
   ```bash
   .venv/bin/python -m multiuav sticks list
   ```
   Пульта нет, хотя `sudo evtest` его показывает → нет прав на `/dev/input/eventN`. Добавить себя
   в группу `input`, затем выйти из сеанса и войти снова:
   ```bash
   sudo usermod -aG input $USER
   ```
3. Разметка осей и кнопки SE лежит в `radio/sticks_map.json` (газ — `ABS_Z`, рыскание — `ABS_RX`,
   тангаж — `ABS_Y`, крен — `ABS_X`, все «прямые»; SE — `BTN_A`, код 304). Другой пульт или
   другая модель в EdgeTX — разметить заново (команды спрашивают, что двигать):
   ```bash
   .venv/bin/python -m multiuav sticks map
   ```
   ```bash
   .venv/bin/python -m multiuav sticks button SE
   ```

## 5. Первый запуск игры — компиляция шейдеров

Один раз открыть редактор на карте и дождаться, пока он докомпилирует шейдеры (от 10 минут до
часа, дальше — из кэша). Затем закрыть.

```bash
__NV_PRIME_RENDER_OFFLOAD=1 __GLX_VENDOR_LIBRARY_NAME=nvidia ~/Downloads/UnrealEngine5/Engine/Binaries/Linux/UnrealEditor "$HOME/Desktop/New_Karta_Vitali/Blocks.uproject" /Game/FlyingCPP/Maps/FlyingExampleMap
```

(Переменные `__NV_…` — рисовать на дискретной NVIDIA; на компьютере без гибридной графики
они не мешают.)

## 6. Эталон расстановки ворот и зон

Карта с воротами пришла с проектом, их координаты — в git (`maps/FlyingExampleMap.json`).
Запомнить их как эталон, чтобы при случайной правке вернуть командой `track restore`:

```bash
.venv/bin/python -m multiuav track save
```

## 7. Значок «Эксперимент БПЛА»

```bash
~/Documents/multiuav_svetlakov/scripts/install_launcher.sh
```

Скрипт проверит модули Python в `.venv` (если чего-то нет — напишет команду) и положит значок
на рабочий стол и в меню приложений. Если значок с крестиком — правой кнопкой →
**«Разрешить запуск»**.

Если движок или проект лежат не там, где на ноутбуке, — записать свои пути в
`experiments/local.json` (настройки этого компьютера: git его не видит, `git pull` ему не
мешает; значения перекрывают `experiments/friday.json`). Образец — `experiments/local.example.json`:
```bash
cp experiments/local.example.json experiments/local.json && nano experiments/local.json
```
```json
{
  "engine": "~/Documents/UnrealEngine5/Engine/Binaries/Linux/UnrealEditor",
  "project": "~/Desktop/New_Karta_Vitali/Blocks.uproject",
  "time_server": "192.168.10.1"
}
```

## 8. Проверка всего цикла

1. Значок «Эксперимент БПЛА» → в меню горят зелёным: пульт, «Ворота и зоны», «Игра найдена».
   Подвигать стики — полоски идут вправо при «вверх/вправо», газ внизу = 1000; нажать SE —
   «Кнопка SE работает».
2. Код участника `test`, длительность 2 мин → **Старт**. Игра откроется на весь экран (первый
   раз дольше). Опустить газ в ноль (взвод моторов), взлететь, пролететь ворота, нажать R —
   дрон в центре последних ворот.
3. **Стоп** (Alt+Tab в меню) → **Обработать попытку** → **Открыть папку**. В
   `runs/test/<дата_время>/` должны быть: `experiment.json`, `drone0.csv`, `sticks.csv`,
   `buttons.csv`, `passport.json`, `resets.csv`, `collisions.csv`, `occlusion_events.csv`,
   `unreal.log`, `summary.json`, а после обработки — `gates.csv`, `presses.csv`,
   `occlusions.csv`, `contacts.csv`, `track.png`.

Без пульта и без Unreal можно проверить цепочку так:
```bash
.venv/bin/python -m multiuav launch experiments/friday.json --participant test --backend kinematic --no-sticks
```

## 9. Если что-то не так

| Симптом | Причина | Что делать |
|---|---|---|
| «нет Python-модуля evdev», «пульт не найден» | модули не поставлены в `.venv` (`sudo apt install evtest` — это другая программа) | шаг 3.3 |
| `sticks list` пульта не видит, `sudo evtest` видит | нет прав на `/dev/input/eventN` | шаг 4.2, группа `input`, перезайти |
| `pip` падает на `evdev`: `Python.h`, `gcc` | нет инструментов сборки | `sudo apt install python3-dev gcc`, повторить шаг 3.3 |
| значок не запускается | не разрешён запуск | правой кнопкой → «Разрешить запуск»; вывод меню — `runs/menu.log` |
| «Нет движка или проекта» в меню | другие пути | `experiments/friday.json`, шаг 7 |
| «порт 41451 уже занят» | открыт редактор или старая игра | закрыть их (по PID: `pgrep -x UnrealEditor`, `kill`) |
| «игра упала», `VK_ERROR_DEVICE_LOST` в `unreal.log` | сбой видеокарты при смене окна | не менять размер окна; `"fullscreen": false` в `experiments/friday.json` |
| R не срабатывает | сеанс Wayland или R занята | войти в «Ubuntu on Xorg»; сброс — кнопкой «К воротам» в меню |
| сборка висит на 1 % | UBA | шаг 1.2 |
| дрон на старте носом не туда / ворота «не те» | выбрано не то направление | поле «Направление по трассе» в меню |
| ворота сбились после правки карты | — | `.venv/bin/python -m multiuav track restore` |

## 10. Общее время с компьютером датчиков (Windows: айтрекер, пульсометр)

Записи симулятора (`pc_time_ms`, `t_ms` в `sticks.csv` и `buttons.csv`) и записи датчиков
склеиваются по часам компьютеров, поэтому часы Ubuntu должны идти по часам Windows.
Схема: Windows — сервер времени (NTP), Ubuntu постоянно сверяется с ней по кабелю службой
`chrony`. Проверено 05.11.2026: Ubuntu расходится с Windows на доли микросекунды по оценке
`chrony`; на деле точность ограничивает служба времени Windows — обычно меньше 1 мс.

### Windows (всё — от администратора)

1. Включить сервер времени — скрипт научрука `time.bat` (правой кнопкой → «Запуск от имени
   администратора»). Он включает в Windows NTP-сервер и сверяет саму Windows с `pool.ntp.org`.
2. Пустить запросы времени через брандмауэр (UDP, порт 123) — в `time.bat` этого нет:
   ```
   netsh advfirewall firewall add rule name="NTP Server" dir=in action=allow protocol=UDP localport=123
   ```
   Проверить: `netsh advfirewall firewall show rule name="NTP Server"`.
3. Кабель напрямую, без роутера: в свойствах адаптера Ethernet → IPv4 вручную:
   адрес `192.168.10.1`, маска `255.255.255.0`, шлюз пустой.
4. Если у Windows нет интернета, её служба времени может считать себя «ненадёжной»
   (`w32tm /query /status` → «не синхронизировано»), и Ubuntu её отвергнет. Тогда:
   ```
   w32tm /config /manualpeerlist:"" /syncfromflags:NO /reliable:YES /update
   ```
   ```
   net stop w32time && net start w32time
   ```

### Ubuntu

1. Имя порта Ethernet (у компьютера центра — `enp4s0`, маленькими буквами):
   ```bash
   nmcli device status
   ```
2. Ручной адрес на этом порту; интернет остаётся через Wi-Fi (`ipv4.never-default yes`).
   Свойство — `ifname`, не «iframe»:
   ```bash
   sudo nmcli con add type ethernet ifname enp4s0 con-name timelink ipv4.method manual ipv4.addresses 192.168.10.2/24 ipv4.never-default yes
   ```
   `/24` — маска `255.255.255.0`: оба компьютера в сети `192.168.10.…`. Проверка: `ping -c 3 192.168.10.1`.
3. Выключить автосверку времени самой Ubuntu (иначе она сверяется с `ntp.ubuntu.com` и
   спорит с Windows) и поставить `chrony`:
   ```bash
   sudo timedatectl set-ntp false
   ```
   ```bash
   sudo apt install chrony
   ```
4. Конфиг — правка только через `sudo` (файлы в `/etc` принадлежат root):
   ```bash
   sudo cp /etc/chrony/chrony.conf /etc/chrony/chrony.conf.bak
   ```
   ```bash
   sudo nano /etc/chrony/chrony.conf
   ```
   - закомментировать (`#`) строки `pool …`;
   - добавить в конец:
     ```
     server 192.168.10.1 iburst minpoll 4 maxpoll 6
     maxdistance 16
     ```
     `iburst` — при старте 4 быстрых запроса; `minpoll 4`/`maxpoll 6` — спрашивать раз в
     2⁴ = 16 … 2⁶ = 64 с; `maxdistance 16` — **обязательно для Windows**: её служба сообщает
     о себе огромную погрешность (`Root dispersion` ≈ 7,8 с), а chrony по умолчанию не
     принимает источники хуже 3 с и ставит им `^?`.
   - сохранить (Ctrl+O, Enter), выйти (Ctrl+X), применить:
     ```bash
     sudo systemctl restart chrony
     ```
5. Проверить через полминуты:
   ```bash
   chronyc sources
   ```
   у `192.168.10.1` должно стоять **`^*`** (`^?` — источник не принят, см. ниже);
   ```bash
   chronyc tracking
   ```
   `Reference ID` — `192.168.10.1`, `System time` — расхождение (доли миллисекунды),
   `Leap status: Normal`.
6. Адрес сервера времени — в `experiments/local.json`: `"time_server": "192.168.10.1"`.
   Тогда каждая попытка записывает в `experiment.json` → `clock_sync` снимок `chronyc
   tracking` в начале и в конце (расхождение, источник, состояние), а меню предупреждает,
   если часы не сверены или расходятся больше чем на 2 мс.

### Перед каждой попыткой и во время неё

- `chrony` включается сам при загрузке; Windows и кабель должны быть включены, иначе
  часы Ubuntu постепенно уйдут;
- во время попытки не запускать `ntpdate` и не менять настройки: часы могут прыгнуть, а
  `pc_time_ms` в журналах — это часы компьютера.

### Если `^?`

| Проверка | Команда | Что видно |
|---|---|---|
| ответы от Windows | `sudo chronyc ntpdata 192.168.10.1` | `Total RX 0` — брандмауэр (Windows, шаг 2) или служба времени не включена (шаг 1) |
| часы Windows надёжны | там же, `Leap status`, `Stratum` | `Not synchronised` или `Stratum 16` — Windows, шаг 4 |
| погрешность | там же, `Root dispersion` | больше 3 с — нет `maxdistance 16` (Ubuntu, шаг 4) |
| `501 Not authorised` | — | `chronyc ntpdata` только с `sudo` |

