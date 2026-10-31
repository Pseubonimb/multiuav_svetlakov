# Установка на Debian

Cosys-AirSim официально проверяют на Ubuntu 24.04, но ничего специфичного для
Ubuntu ему не нужно: Debian 12 или 13 подходит. Ниже два пути: быстрая проверка
на готовой сборке (без установки Unreal) и полная установка для своих сцен.

## 1. Драйвер NVIDIA и Vulkan

Unreal Engine под Linux рисует через Vulkan, поэтому нужен проприетарный драйвер.

1. Включи разделы `contrib non-free non-free-firmware` в источниках apt
   (`/etc/apt/sources.list` или `/etc/apt/sources.list.d/debian.sources`).
2. Установи драйвер и Vulkan:
   ```bash
   sudo apt update
   sudo apt install linux-headers-amd64 nvidia-driver firmware-misc-nonfree \
       nvidia-vulkan-icd vulkan-tools
   sudo reboot
   ```
3. Проверь:
   ```bash
   nvidia-smi                 # видна карта и версия драйвера
   vulkaninfo --summary       # в списке устройств есть NVIDIA
   ```

Если карта новее, чем драйвер в репозитории Debian (например, серия RTX 50),
бери драйвер из `bookworm-backports` / `trixie-backports`.

## 2. Python-часть проекта

```bash
sudo apt install git python3-venv
git clone https://github.com/annihilismus/multiuav.git
cd multiuav
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[cosys,gamepad,plot,dev]"
pytest -q                                      # все тесты зелёные
python -m multiuav compare scenarios/corner.yaml --plot corner.png
```

## 3. Быстрая проверка на готовой сборке Blocks

В релизе Cosys-AirSim есть собранная под Linux тестовая сцена Blocks, Unreal
для неё ставить не нужно.

1. Скачай «Packaged Demo Build» для Linux со страницы релиза
   [5.8-v3.5.0](https://github.com/Cosys-Lab/Cosys-AirSim/releases/tag/5.8-v3.5.0)
   и распакуй.
2. Создай настройки дронов из сценария:
   ```bash
   python -m multiuav settings scenarios/blocks_demo.yaml --write
   ```
   Файл ляжет в `~/Documents/AirSim/settings.json`. Его нужно пересоздавать
   и перезапускать симулятор каждый раз, когда меняется число дронов.
3. Запусти сцену: файл `.sh` в распакованной папке, например
   `./Blocks.sh -windowed -ResX=1280 -ResY=720`.
4. Во втором терминале:
   ```bash
   . .venv/bin/activate
   python -m multiuav run scenarios/blocks_demo.yaml --backend cosys
   ```
   Пять дронов взлетят, наберут 30 м и облетят квадрат за ведущим.

Ручное управление ведущим:

```bash
python -m multiuav gamepad                     # посмотреть номера осей своего геймпада
python -m multiuav run scenarios/blocks_demo.yaml --backend cosys --leader gamepad
```

Раскладка по умолчанию как на пульте в Mode 2: левый стик — высота и поворот,
правый — вперёд-назад и вбок. Если оси у геймпада другие, их номера задаются
в сценарии: `leader: {algorithm: gamepad, params: {axes: {roll: 3, pitch: 4, throttle: 1, yaw: 0}}}`.

## 4. Полная установка: Unreal Engine 5.8 и плагин

Нужна для своих сцен, например города с плотной застройкой.

1. Скачай Unreal Engine 5.8 для Linux со страницы
   [unrealengine.com/linux](https://www.unrealengine.com/en-US/linux) (нужен
   аккаунт Epic) и распакуй, например, в `/opt/UnrealEngine`:
   ```bash
   sudo mkdir -p /opt/UnrealEngine && sudo chown $USER /opt/UnrealEngine
   unzip -o Linux_Unreal_Engine_5.8.*.zip -d /opt/UnrealEngine
   ```
2. Плагин Cosys-AirSim. Проще всего взять готовый «Precompiled plugin» для Linux
   из того же релиза и положить в папку `Plugins` своего Unreal-проекта.
   Собрать из исходников тоже можно:
   ```bash
   sudo apt install build-essential cmake rsync unzip wget lsb-release
   git clone https://github.com/Cosys-Lab/Cosys-AirSim.git
   cd Cosys-AirSim
   ./setup.sh
   ./build.sh --ue-root /opt/UnrealEngine
   ```
   С `--ue-root` сборка идёт компилятором, который поставляется с Unreal, поэтому
   версия clang в Debian не важна.
3. Открой проект сцены (для начала `Cosys-AirSim/Unreal/Environments/Blocks`):
   ```bash
   /opt/UnrealEngine/Engine/Binaries/Linux/UnrealEditor \
       ~/Cosys-AirSim/Unreal/Environments/Blocks/Blocks.uproject
   ```
   На вопрос о сборке ответь «Да», плагин не отключай. Нажми Play и запускай
   сценарий, как в пункте 3.
4. В настройках редактора (Edit → Editor Preferences) сними галочку
   «Use Less CPU when in Background», иначе симуляция замедляется, когда окно
   не в фокусе.

## Если что-то не так

- `В симуляторе нет дронов [...]`: settings.json не совпадает со сценарием.
  Пересоздай его командой из пункта 3.2 и перезапусти симулятор.
- Дроны дёргаются или отстают: цикл не успевает за 20 Гц. Уменьши `rate_hz`
  в сценарии до 10.
- Симулятор на другой машине: `--host <её адрес>`.
