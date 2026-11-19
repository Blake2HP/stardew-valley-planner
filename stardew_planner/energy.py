"""前期体力：按种植清单算每天手浇的格数和体力，压力大的日子要安排补体力（数据见 data/energy.json）。

- 每天要浇的格数：单次收获的作物从种下当天浇到收获前一天；可再生作物整季都要浇；上季带来的作物同理；
  来不及成熟的作物一直浇到季末。不算洒水器和下雨天（下雨不用浇水）。
- 浇水体力 = 格数 × (2 − 0.1 × 耕种等级)，开局最大体力 270（wiki Energy / Skills）。
- 超过 heavy_threshold（默认 100 点）的日子算“浇水压力大”。连续压力大的日子里，每 replenish_every_days
  （默认 3）天内至少要有一天的目标写了补体力（采集大葱 / 鲑莓、去沙龙买沙拉……），否则报“⚠️ 体力预警”，
  交给模型修正。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .bonuses import NO_BONUSES, Bonuses
from .data_loader import DEFAULT_DATA_DIR

SEASON_DAYS = 28


@dataclass(frozen=True)
class EnergyRules:
    max_energy: int = 270
    watering_cost: float = 2.0
    level_reduction: float = 0.1
    heavy_threshold: float = 100
    replenish_every_days: int = 3
    replenish_words: tuple[str, ...] = ("大葱", "鲑莓", "沙拉", "补体力", "补充体力", "吃")

    def cost_per_tile(self, farming_level: int = 0) -> float:
        return max(0.0, self.watering_cost - self.level_reduction * farming_level)


def load_energy_rules(data_dir: str | Path = DEFAULT_DATA_DIR) -> EnergyRules | None:
    """文件不存在时为 None（不做体力校验）。"""
    path = Path(data_dir) / "energy.json"
    if not path.exists():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    return EnergyRules(int(raw["max_energy"]), float(raw["watering_cost"]), float(raw["level_reduction"]),
                       float(raw["heavy_threshold"]), int(raw["replenish_every_days"]),
                       tuple(raw["replenish_words"]))


def watered_tiles(plantings, carry_in=()) -> dict[int, int]:
    """第 1-28 天每天要浇的格数。plantings：farm_state.Planting；carry_in：farm_state.CarryCrop。"""
    tiles = {d: 0 for d in range(1, SEASON_DAYS + 1)}

    def add(first: int, last: int, n: int):
        for d in range(max(1, first), min(SEASON_DAYS, last) + 1):
            tiles[d] += n

    for p in plantings:
        if p.crop.regrow_days or not p.harvests:
            add(p.day, SEASON_DAYS, p.tiles)          # 可再生作物整季要浇；来不及成熟的也一直浇
        else:
            add(p.day, p.harvests[0] - 1, p.tiles)    # 收获当天收走，不用再浇
    for c in carry_in:
        if c.crop.regrow_days or not c.harvest_days:
            add(1, SEASON_DAYS, c.tiles)
        else:
            add(1, c.harvest_days[0] - 1, c.tiles)
    return tiles


def heavy_days(tiles: dict[int, int], rules: EnergyRules, bonuses: Bonuses = NO_BONUSES,
               players: int = 1) -> dict[int, float]:
    """浇水体力超过阈值的日子 → 当天浇水体力。联机时每人一条体力，阈值按人数放大。"""
    cost = rules.cost_per_tile(bonuses.effective_farming_level)
    limit = rules.heavy_threshold * players
    return {d: n * cost for d, n in tiles.items() if n * cost > limit}


def _runs(days) -> list[list[int]]:
    runs: list[list[int]] = []
    for d in sorted(days):
        if runs and runs[-1][-1] == d - 1:
            runs[-1].append(d)
        else:
            runs.append([d])
    return runs


def check_energy(goals: dict[int, str], plantings, carry_in, rules: EnergyRules,
                 bonuses: Bonuses = NO_BONUSES, players: int = 1) -> list[str]:
    """浇水压力大的日子里，每 replenish_every_days 天内至少一天要安排补体力。

    联机时 goals 是所有玩家合成的目标，任何一个玩家安排了补体力都算；阈值 = 单人阈值 × 人数。
    """
    tiles = watered_tiles(plantings, carry_in)
    heavy = heavy_days(tiles, rules, bonuses, players)
    if not heavy:
        return []
    cost = rules.cost_per_tile(bonuses.effective_farming_level)
    step = rules.replenish_every_days
    problems = []
    for run in _runs(heavy):
        missing = [run[i:i + step] for i in range(0, len(run), step)
                   if not any(any(w in goals.get(d, "") for w in rules.replenish_words) for d in run[i:i + step])]
        if not missing:
            continue
        peak = max(tiles[d] for d in run)
        span = "、".join(f"第 {c[0]}-{c[-1]} 天" if len(c) > 1 else f"第 {c[0]} 天" for c in missing)
        if players > 1:
            problems.append(
                f"⚠️ 联机校验：体力预警：第 {run[0]}-{run[-1]} 天 {players} 人合计每天要手浇最多 {peak} 格，"
                f"约 {peak * cost:.0f} 点体力（每格 {cost:g} 点，每人上限 {rules.max_energy}，"
                f"{players} 人的安全线约 {rules.heavy_threshold * players:g} 点），{span}没有安排补充体力；"
                f"请至少每 {step} 天让大家一起补体力（采集大葱/鲑莓、去沙龙买沙拉、泡温泉），或者减少种植格数")
            continue
        problems.append(
            f"⚠️ 体力预警：第 {run[0]}-{run[-1]} 天每天要手浇最多 {peak} 格，约 {peak * cost:.0f} 点体力"
            f"（每格 {cost:g} 点，开局上限 {rules.max_energy}），{span}没有安排补充体力；"
            f"请至少每 {step} 天安排一次“采集大葱/鲑莓”或“去沙龙买沙拉”等补体力的事，或者减少种植格数")
    return problems


def describe_energy(rules: EnergyRules | None, bonuses: Bonuses = NO_BONUSES, players: int = 1) -> str:
    """Prompt【数据参考】里的体力一节。联机时每人一条体力，安全线按人数放大。"""
    if rules is None:
        return "（没有体力数据）"
    level = bonuses.effective_farming_level
    cost = rules.cost_per_tile(level)
    limit = rules.heavy_threshold * players
    tiles = int(limit // cost) if cost else 0
    who = f"每人开局最大体力 {rules.max_energy} 点（{players} 人联机，各自一条）" if players > 1         else f"开局最大体力 {rules.max_energy} 点"
    return (f"{who}；喷壶每浇一格消耗 2 − 0.1 × 耕种等级 = {cost:g} 点（耕种等级 {level}）。"
            f"程序按种植清单算每天要手浇的格数（不算洒水器和下雨天）：浇水超过 {limit:g} 点"
            f"（约 {tiles} 格）的日子，每 {rules.replenish_every_days} 天内至少要安排一次补体力，"
            "例如春季采集大葱、鲑莓采集季摘鲑莓、秋季黑莓采集季摘黑莓、随时可以去沙龙买沙拉或泡温泉。")
