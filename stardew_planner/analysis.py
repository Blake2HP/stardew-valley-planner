"""基于结构化数值的确定性计算：在调用大模型之前先算好，作为 Prompt 的“事实依据”，
避免模型凭记忆编造数字。职业与肥料加成见 bonuses.py。"""

from __future__ import annotations

from dataclasses import dataclass

from .bonuses import NO_BONUSES, Bonuses
from .data_loader import NO_GUIDES_TEXT, Crop, GameData

SEASON_DAYS = 28


@dataclass(frozen=True)
class CropProfit:
    crop: Crop
    harvests: int
    profit_per_tile: int        # 整季单格净利润（直接出售，已计入职业、肥料）
    best_artisan: str | None    # 最佳加工方式
    artisan_unit_price: int | None
    plant_day: int = 1          # 实际种植日（不早于种子开售日）
    grow_days: int = 0          # 实际生长天数（已计入加速）
    sell_price: int = 0         # 实际售价（已计入农耕人）

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


def harvest_plan(grow_days: int, regrow_days: int | None, days_left: int) -> tuple[int, int]:
    """返回 (收获次数, 需要的种子数)。非重复收获作物假设收获后立即补种。"""
    if regrow_days:
        harvests = 0 if grow_days >= days_left else 1 + (days_left - 1 - grow_days) // regrow_days
        return harvests, 1
    harvests = (days_left - 1) // grow_days
    return harvests, max(harvests, 1)


def season_profit(crop: Crop, plant_day: int = 1, bonuses: Bonuses = NO_BONUSES) -> tuple[int, int]:
    """返回 (收获次数, 单格整季净利润)。

    - 种植日会被推迟到种子最早开售日（草莓按第 13 天起算：可用 28 − 13 + 1 = 16 天）；
    - 生长天数按加速肥料 / 农业学家查表，再生天数不变；
    - 售价计入农耕人加成；肥料每格每季施一次，成本只扣一次。
    """
    days_left = available_growing_days(effective_plant_day(crop, plant_day))
    harvests, seeds = harvest_plan(bonuses.grow_days(crop), crop.regrow_days, days_left)
    revenue = harvests * bonuses.crop_price(crop) * crop.harvest_yield
    return harvests, int(revenue - seeds * crop.seed_price - bonuses.fertilizer_cost)


def rank_crops(
    data: GameData, season: str, top_n: int = 6, bonuses: Bonuses = NO_BONUSES, year: int | None = None
) -> list[CropProfit]:
    """本季作物按单格整季净利排序；传入 year 时过滤掉当年买不到种子的作物（如第 1 年的大蒜）。"""
    results = []
    for crop in data.crops_in_season(season, year):
        harvests, profit = season_profit(crop, bonuses=bonuses)
        rules = data.artisan_rules_for(crop)
        best = max(rules, key=lambda r: bonuses.artisan_price(r, crop), default=None)
        results.append(CropProfit(
            crop=crop,
            harvests=harvests,
            profit_per_tile=profit,
            best_artisan=f"{best.machine}→{best.product}" if best else None,
            artisan_unit_price=bonuses.artisan_price(best, crop) if best else None,
            plant_day=effective_plant_day(crop),
            grow_days=bonuses.grow_days(crop),
            sell_price=bonuses.crop_price(crop),
        ))
    results.sort(key=lambda r: r.profit_per_tile, reverse=True)
    return results[:top_n]


def build_data_context(
    data: GameData,
    season: str,
    guide_excerpts: str = NO_GUIDES_TEXT,
    bonuses: Bonuses = NO_BONUSES,
    year: int | None = None,
) -> dict[str, str]:
    """把结构化数值整理成可直接塞进 Prompt 的文本片段；攻略片段由 search_guides() 检索后传入。"""
    crop_lines = ["| 作物 | 种子价 | 售价 | 生长/再生(天) | 最早种植日 | 整季收获次数 | 单格整季净利 | 最佳加工(单价) | 种子来源 |",
                  "|---|---|---|---|---|---|---|---|---|"]
    for r in rank_crops(data, season, bonuses=bonuses, year=year):
        c = r.crop
        regrow = f"{r.grow_days}/{c.regrow_days}" if c.regrow_days else f"{r.grow_days}/-"
        artisan = f"{r.best_artisan}({r.artisan_unit_price}g)" if r.best_artisan else "-"
        crop_lines.append(
            f"| {c.name} | {c.seed_price}g | {r.sell_price}g | {regrow} | 第 {r.plant_day} 天 | {r.harvests} | "
            f"{r.profit_per_tile}g | {artisan} | {c.seed_source} |"
        )

    artisan_lines = [
        f"- {r.machine}：{r.input_category} → {r.product}，"
        + (f"固定 {r.fixed_price}g" if r.fixed_price is not None else f"售价 ×{r.multiplier} + {r.bonus}")
        + f"，耗时约 {r.processing_days} 天"
        for r in data.artisan_rules
    ]

    event_lines = [f"- 第 {e.day} 天【{e.name}】：{e.tip}" for e in data.events_in_season(season)]

    return {
        "crop_table": "\n".join(crop_lines),
        "artisan_rules": "\n".join(artisan_lines),
        "calendar_events": "\n".join(event_lines) or "（无）",
        "guide_excerpts": guide_excerpts or NO_GUIDES_TEXT,
        "bonuses": describe_bonuses(bonuses),
    }


def describe_bonuses(bonuses: Bonuses) -> str:
    """Prompt 中的加成说明：表格里的数字已经计入了哪些加成。"""
    if bonuses.is_empty:
        return "无（表中为基础数值）"
    lines = [bonuses.describe() + "（表中售价、生长天数、净利已计入）"]
    for p in bonuses.professions:
        lines.append(f"- {p.name_zh}：{p.note}")
    if bonuses.fertilizer:
        f = bonuses.fertilizer
        cost = f"每格每季 {f.price}g" if f.price else "无法直接购买，成本按 0 计"
        lines.append(f"- {f.name_zh}：生长速度 +{f.growth_speed_percent}%，不缩短再生时间；{cost}；来源：{f.source}")
    return "\n".join(lines)
