"""Картинка прогона сверху: здания, траектории, столкновения, финиш."""

from __future__ import annotations

from pathlib import Path

from .manager import RunResult
from .scenario import Scenario


def draw_run(ax, scenario: Scenario, result: RunResult) -> None:
    """Нарисовать прогон на осях matplotlib: восток вправо, север вверх."""
    from matplotlib.patches import Rectangle

    for b in scenario.buildings:
        ax.add_patch(Rectangle((b.east[0], b.north[0]), b.east[1] - b.east[0],
                               b.north[1] - b.north[0], color="0.8", zorder=0))
    if scenario.finish:
        f = scenario.finish
        ax.add_patch(Rectangle((f.east[0], f.north[0]), f.east[1] - f.east[0],
                               f.north[1] - f.north[0], fill=False, ls="--", color="green"))

    import matplotlib.pyplot as plt

    cmap = plt.get_cmap("viridis")
    for i in range(result.drones):
        rows = [r for r in result.track if r[1] == i]
        east = [r[3] for r in rows]
        north = [r[2] for r in rows]
        color = "black" if i == 0 else cmap(i / max(1, result.drones - 1))
        ax.plot(east, north, color=color, lw=2 if i == 0 else 1,
                label="ведущий" if i == 0 else None)
        if i in result.collided:
            ax.plot(east[-1], north[-1], "x", color="red", ms=10, mew=2)

    s = result.summary()
    ax.set_title(f"{result.followers}, {result.drones} дронов: "
                 f"дошли {s['finished']}, столкнулись {s['collided']}")
    ax.set_xlabel("восток, м")
    ax.set_ylabel("север, м")
    ax.set_aspect("equal")
    north_all = [r[2] for r in result.track]
    east_all = [r[3] for r in result.track]
    pad = 15
    ax.set_xlim(min(east_all) - pad, max(east_all) + pad)
    ax.set_ylim(min(north_all) - pad, max(north_all) + pad)
    ax.legend(loc="lower right")


def plot_runs(runs: list[tuple[Scenario, RunResult]], path: str | Path) -> Path:
    """Сохранить один или несколько прогонов рядом в PNG."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(1, len(runs), figsize=(6.5 * len(runs), 6.5), squeeze=False)
    for ax, (scenario, result) in zip(axes[0], runs):
        draw_run(ax, scenario, result)
    fig.tight_layout()
    path = Path(path)
    fig.savefig(path, dpi=110)
    plt.close(fig)
    return path


def plot_run(scenario: Scenario, result: RunResult, path: str | Path) -> Path:
    return plot_runs([(scenario, result)], path)
