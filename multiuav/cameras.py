"""Маленькие экраны с камер ведомых поверх окна Unreal.

AirSim умеет показывать только 3 своих подокна, и внизу экрана (их можно включить
`multiuav settings ... --subwindows`), поэтому здесь картинки берутся по API
(simGetImages, по потоку на дрон, без PNG) и рисуются в отдельном окне без рамки,
которое всегда поверх остальных. Частоту кадров ограничивает сама игра: камера
не может дать кадров больше, чем игра рисует (см. `scripts/demo.sh -q`). По умолчанию — в правом верхнем углу окна игры, чтобы не закрывать
сообщения AirSim слева.

Столкновение дрона подсвечивает его экран красной рамкой и надписью на HIT_SECONDS
секунд; счётчик столкновений остаётся в подписи.

Запуск (симулятор уже открыт, Play нажат):
    python -m multiuav cameras --drones 5
"""

from __future__ import annotations

import threading
import time

#: Сколько секунд держать красную подсветку после столкновения.
HIT_SECONDS = 3.0
#: Касания ниже этой высоты над точкой старта — посадка или взлёт, а не авария.
GROUND_ALTITUDE = 0.5
RED = (220, 30, 30)


def window_position(corner: str, size: tuple[int, int], game: tuple[int, int, int, int],
                    margin: int = 6, title_bar: int = 38) -> tuple[int, int]:
    """Где поставить окно камер: угол окна игры (x, y, ширина, высота)."""
    gx, gy, gw, gh = game
    top = gy + title_bar + margin
    bottom = gy + gh - size[1] - margin
    left = gx + margin
    right = gx + gw - size[0] - margin
    return {"top-left": (left, top), "top-right": (right, top),
            "bottom-left": (left, bottom), "bottom-right": (right, bottom)}[corner]


class CollisionWatch:
    """Следит за столкновениями одного дрона по simGetCollisionInfo."""

    def __init__(self, client, vehicle: str) -> None:
        self.client = client
        self.vehicle = vehicle
        self.count = 0
        self.until = 0.0
        self.what = ""
        self.last_stamp = self._info().time_stamp  # что было до нас — не считаем

    def _info(self):
        return self.client.simGetCollisionInfo(vehicle_name=self.vehicle)

    def update(self, now: float) -> bool:
        """True, пока экран надо подсвечивать красным."""
        info = self._info()
        if info.has_collided and info.time_stamp != self.last_stamp:
            self.last_stamp = info.time_stamp
            z = self.client.simGetGroundTruthKinematics(vehicle_name=self.vehicle).position.z_val
            if -z > GROUND_ALTITUDE:  # на земле при взлёте и посадке — не авария
                if now > self.until:
                    self.count += 1  # одно касание стены даёт много событий подряд
                self.until = now + HIT_SECONDS
                self.what = info.object_name
        return now < self.until


class DroneFeed(threading.Thread):
    """Свой поток и своё подключение на каждый дрон.

    Запрос кадра ждёт, пока игра его отрисует (около одного кадра игры), поэтому
    дроны опрашиваются параллельно: по очереди 4 камеры дают в 4 раза меньше кадров.
    Запускать только после warm_up(): иначе первые запросы включают камеры из
    разных потоков одновременно и роняют Unreal.
    Кадры берутся без PNG — сырые RGB, их не надо ни сжимать, ни распаковывать.
    """

    def __init__(self, host: str, drone: int, camera: str) -> None:
        super().__init__(daemon=True)
        self.host, self.drone, self.camera = host, drone, camera
        self.frame: tuple[int, int, bytes] | None = None  # ширина, высота, RGB
        self.frames = 0  # для подсчёта FPS
        self.hit = False
        self.count = 0
        self.what = ""
        self.stop = threading.Event()

    def run(self) -> None:
        import asyncio

        import cosysairsim as airsim

        asyncio.set_event_loop(asyncio.new_event_loop())  # клиенту AirSim нужен свой цикл
        client = airsim.MultirotorClient(ip=self.host)
        watch = CollisionWatch(client, f"Drone{self.drone}")
        request = [airsim.ImageRequest(self.camera, airsim.ImageType.Scene, False, False)]
        while not self.stop.is_set():
            try:
                response = client.simGetImages(request, vehicle_name=f"Drone{self.drone}")
                if response and response[0].width > 0:
                    r = response[0]
                    self.frame = (r.width, r.height, bytes(r.image_data_uint8))
                    self.frames += 1
                self.hit = watch.update(time.monotonic())
                self.count, self.what = watch.count, watch.what
            except Exception:
                time.sleep(0.5)  # симулятор занят или перезапускается — попробуем снова


def warm_up(client, ids: list[int], camera: str) -> None:
    """Первый кадр с каждой камеры — строго по очереди.

    При первом запросе AirSim включает камеру (UActorComponent::Activate), а это
    можно делать только из одного потока: одновременное включение из нескольких
    потоков RPC роняет Unreal (Assertion failed ... SparseArray.h). После включения
    камера остаётся включённой, и параллельные запросы уже безопасны.
    """
    import cosysairsim as airsim

    request = [airsim.ImageRequest(camera, airsim.ImageType.Scene, False, False)]
    for drone in ids:
        client.simGetImages(request, vehicle_name=f"Drone{drone}")


def run_cameras(drones: int, host: str = "127.0.0.1", first: int = 1, camera: str = "front_center",
                tile: tuple[int, int] = (256, 144), columns: int = 2, fps: float = 20.0,
                corner: str = "top-right", game: tuple[int, int, int, int] = (0, 0, 1600, 900),
                position: tuple[int, int] | None = None, show_fps: bool = True) -> None:
    """Показывать камеры дронов first..drones-1 сеткой columns в столбец."""
    import cosysairsim as airsim
    import pygame
    from pygame._sdl2.video import Renderer, Texture, Window

    ids = list(range(first, drones))
    if not ids:
        raise SystemExit("Ведомых нет: нечего показывать (--drones должно быть больше 1)")
    client = airsim.MultirotorClient(ip=host)
    client.confirmConnection()  # симулятор должен быть запущен
    warm_up(client, ids, camera)
    pygame.init()
    font = pygame.font.SysFont("DejaVu Sans", 16, bold=True)
    small = pygame.font.SysFont("DejaVu Sans", 13, bold=True)
    cols = min(columns, len(ids))
    rows = (len(ids) + cols - 1) // cols
    pad = 3
    size = (cols * (tile[0] + pad) + pad, rows * (tile[1] + pad) + pad)
    window = Window("MultiUAV cameras", size=size,
                    position=position or window_position(corner, size, game),
                    borderless=True, always_on_top=True, skip_taskbar=True, utility=True)
    renderer = Renderer(window)
    canvas = pygame.Surface(size)

    feeds = {d: DroneFeed(host, d, camera) for d in ids}
    for feed in feeds.values():
        feed.start()
    period = 1.0 / fps
    rate = {d: 0.0 for d in ids}
    last_count = {d: 0 for d in ids}
    last_rate_time = time.monotonic()
    print(f"Камеры дронов {ids[0]}..{ids[-1]}, угол {corner}. Остановить — Ctrl+C.")
    try:
        while True:
            started = time.monotonic()
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    return
            if started - last_rate_time >= 1.0:  # сколько новых кадров пришло за секунду
                for d, feed in feeds.items():
                    rate[d] = (feed.frames - last_count[d]) / (started - last_rate_time)
                    last_count[d] = feed.frames
                last_rate_time = started
                print("FPS камер: " + ", ".join(f"#{d} {rate[d]:.0f}" for d in ids), flush=True)
            canvas.fill((20, 20, 20))
            for k, drone in enumerate(ids):
                feed = feeds[drone]
                x = pad + (k % cols) * (tile[0] + pad)
                y = pad + (k // cols) * (tile[1] + pad)
                if feed.frame is not None:
                    w, h, data = feed.frame
                    image = pygame.image.frombuffer(data, (w, h), "RGB")
                    if (w, h) != tile:
                        image = pygame.transform.scale(image, tile)
                    canvas.blit(image, (x, y))
                if feed.hit:
                    tint = pygame.Surface(tile, pygame.SRCALPHA)
                    tint.fill((*RED, 70))
                    canvas.blit(tint, (x, y))
                    pygame.draw.rect(canvas, RED, (x - 2, y - 2, tile[0] + 4, tile[1] + 4), 4)
                    alarm = font.render(" СТОЛКНОВЕНИЕ ", True, (255, 255, 255), RED)
                    canvas.blit(alarm, (x + (tile[0] - alarm.get_width()) // 2,
                                        y + tile[1] // 2 - alarm.get_height()))
                    what = small.render(f" {feed.what[:28]} ", True, (255, 255, 255), (60, 0, 0))
                    canvas.blit(what, (x + (tile[0] - what.get_width()) // 2, y + tile[1] // 2 + 2))
                text = f" #{drone} " + (f"✕{feed.count} " if feed.count else "")
                canvas.blit(font.render(text, True, (255, 255, 255), RED), (x + 4, y + 4))
                if show_fps:
                    label = small.render(f" {rate[drone]:.0f} fps ", True, (255, 255, 255), (0, 0, 0))
                    canvas.blit(label, (x + tile[0] - label.get_width() - 4, y + 4))
            renderer.clear()
            Texture.from_surface(renderer, canvas).draw()
            renderer.present()
            time.sleep(max(0.0, period - (time.monotonic() - started)))
    except KeyboardInterrupt:
        pass
    finally:
        for feed in feeds.values():
            feed.stop.set()
        window.destroy()
        pygame.quit()
