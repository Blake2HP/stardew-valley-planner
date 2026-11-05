"""基于结构化数值的确定性计算：在调用大模型之前先算好，作为 Prompt 的“事实依据”，
避免模型凭记忆编造数字。"""

from __future__ import annotations

from dataclasses import dataclass

from .data_loader import Crop, GameData

SEASON_DAYS = 28


@dataclass(frozen=True)
class CropProfit:
    crop: Crop
    harvests: int
    profit_per_tile: int        # 整季单格净利润（直接出售）
    best_artisan: str | None    # 最佳加工方式
    artisan_unit_price: int | None
    plant_day: int = 1          # 实际种植日（不早于种子开售日）

    @property
    def available_days(self) -> int:
        return available_growing_days(self.plant_day)

    @property
    def profit_per_day(self) -> float:
        return self.profit_per_tile / self.available_days


def effective_plant_day(crop: Crop, plant_day: int = 1) -> int:
    """实际种植日 = max(计划种植日, 种子最早开售日)：种子还买不到时无法种植。"""
    return max(plant_day, crop.earliest_available_day)


def available_growing_days(plant_day: int) -> int:
    """实际可用生长天数 = 28 − 种植日 + 1（种植当天算作第一天）。"""
    return SEASON_DAYS - plant_day + 1


def season_profit(crop: Crop, plant_day: int = 1) -> tuple[int, int]:
    """返回 (收获次数, 单格整季净利润)。

    种植日会被推迟到种子最早开售日（earliest_available_day），所以草莓按第 13 天起算：
    实际可用生长天数 = 28 − 13 + 1 = 16。非重复收获作物假设收获后立即补种。
    """
    days_left = available_growing_days(effective_plant_day(crop, plant_day))
    if crop.regrow_days:
        harvests = 0 if crop.grow_days >= days_left else 1 + (days_left - 1 - crop.grow_days) // crop.regrow_days
        seeds = 1
    else:
        harvests = (days_left - 1) // crop.grow_days
        seeds = harvests
    revenue = harvests * crop.sell_price * crop.harvest_yield
    return harvests, int(revenue - seeds * crop.seed_price)


def rank_crops(data: GameData, season: str, top_n: int = 6) -> list[CropProfit]:
    results = []
    for crop in data.crops_in_season(season):
        harvests, profit = season_profit(crop)
        rules = data.artisan_rules_for(crop)
        best = max(rules, key=lambda r: r.price_for(crop), default=None)
        results.append(CropProfit(
            crop=crop,
            harvests=harvests,
            profit_per_tile=profit,
            best_artisan=f"{best.machine}→{best.product}" if best else None,
            artisan_unit_price=best.price_for(crop) if best else None,
            plant_day=effective_plant_day(crop),
        ))
    results.sort(key=lambda r: r.profit_per_tile, reverse=True)
    return results[:top_n]


def build_data_context(data: GameData, season: str, query: str) -> dict[str, str]:
    """把结构化 + 非结构化数据整理成可直接塞进 Prompt 的文本片段。"""
    crop_lines = ["| 作物 | 种子价 | 售价 | 生长/再生(天) | 最早种植日 | 整季收获次数 | 单格整季净利 | 最佳加工(单价) | 种子来源 |",
                  "|---|---|---|---|---|---|---|---|---|"]
    for r in rank_crops(data, season):
        c = r.crop
        regrow = f"{c.grow_days}/{c.regrow_days}" if c.regrow_days else f"{c.grow_days}/-"
        artisan = f"{r.best_artisan}({r.artisan_unit_price}g)" if r.best_artisan else "-"
        crop_lines.append(
            f"| {c.name} | {c.seed_price}g | {c.sell_price}g | {regrow} | 第 {r.plant_day} 天 | {r.harvests} | "
            f"{r.profit_per_tile}g | {artisan} | {c.seed_source} |"
        )

    artisan_lines = [
        f"- {r.machine}：{r.input_category} → {r.product}，"
        + (f"固定 {r.fixed_price}g" if r.fixed_price is not None else f"售价 ×{r.multiplier} + {r.bonus}")
        + f"，耗时约 {r.processing_days} 天"
        for r in data.artisan_rules
    ]

    event_lines = [f"- 第 {e.day} 天【{e.name}】：{e.tip}" for e in data.events_in_season(season)]

    guide_lines = [f"### {g.title}（来源：{g.source}）\n{g.text}"
                   for g in data.retrieve_guides(query, season=season)]

    return {
        "crop_table": "\n".join(crop_lines),
        "artisan_rules": "\n".join(artisan_lines),
        "calendar_events": "\n".join(event_lines) or "（无）",
        "guide_excerpts": "\n\n".join(guide_lines) or "（无相关攻略片段）",
    }
