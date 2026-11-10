"""蜂房模拟：按蜂房数量算出本季的蜂蜜收入（规则见 data/artisan.json 的 honey，wiki Honey / Bee House）。

- 每个蜂房每 4 天产一罐（第 4、8、…、28 天取蜜），冬季不产；温室里的蜂房不产蜜，所以这里只算室外蜂房；
- 取蜜当天，室外农田里有开着的花时产花蜜：售价 = 100 + 花的基础售价 × 2，取最值钱的那种花；
  没有花时是野蜂蜜 100g；
- 花从第一次可收获那天起算“开着”，到季末为止：假设每种花在蜂房旁边留 1 株不收（一株花可以供很多个蜂房，
  对作物收入的影响可以忽略）；先收花再取蜜会变成野蜂蜜，所以那一株要等取完蜜再收；
- 蜂蜜不分品质；工匠职业 +40%（和其他工匠物品一样向下取整）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .bonuses import Bonuses, floor_mul
from .data_loader import Crop, HoneyRule

SEASON_DAYS = 28


@dataclass
class HoneyResult:
    cash_by_day: dict[int, float] = field(default_factory=dict)
    jars: dict[str, float] = field(default_factory=dict)  # 蜂蜜名 → 罐数
    income: int = 0
    lines: list[str] = field(default_factory=list)


def honey_price(honey: HoneyRule, flower: Crop | None, bonuses: Bonuses) -> int:
    price = honey.base_price if flower is None else honey.price_for(flower)
    for p in bonuses.professions:
        if p.artisan_price_multiplier != 1.0:
            price = floor_mul(price, p.artisan_price_multiplier)
    return price


def simulate_honey(
    blooms: list[tuple[Crop, int]],
    season: str,
    bee_houses: int,
    honey: HoneyRule,
    bonuses: Bonuses,
) -> HoneyResult:
    """blooms：室外的花和它开花（第一次可收获）的日子；bee_houses：室外蜂房数量。"""
    result = HoneyResult()
    if bee_houses <= 0:
        return result
    if season == "winter":
        result.lines.append(f"蜂房 {bee_houses} 个：冬季不产蜂蜜")
        return result
    step = honey.production_days
    for day in range(step, SEASON_DAYS + 1, step):
        open_now = [c for c, start in blooms if start <= day]
        best = max(open_now, key=lambda c: honey.price_for(c), default=None)
        price = honey_price(honey, best, bonuses)
        name = f"{best.name}蜂蜜" if best else "野蜂蜜"
        result.jars[name] = result.jars.get(name, 0) + bee_houses
        result.cash_by_day[day] = result.cash_by_day.get(day, 0) + bee_houses * price
        result.income += bee_houses * price
    detail = "、".join(f"{name} {n:g} 罐" for name, n in result.jars.items())
    result.lines.append(f"蜂蜜（蜂房 {bee_houses} 个，每 {step} 天一罐，取最值钱的开着的花）：{detail}，"
                        f"收入 {result.income}g")
    return result
