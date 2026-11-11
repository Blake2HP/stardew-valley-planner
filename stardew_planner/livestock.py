"""畜牧：按动物数量和天数算本季的产出收入、买动物和干草的支出（数值与假设见 data/animals.json）。

- 新买的动物在本季第 1 天买入，扣购买价；过完成熟所需的夜数后成年，之后每隔产出周期产一次。
  第一次产出日 = 1 + 成熟夜数 + 产出周期（偏保守）；
- 上季带来的动物已经成年，从第 1 天起按产出周期产出；
- 只算基础产品（蛋、牛奶、动物毛……），大号产品和品质要靠好感与心情，不计入；
- 春夏秋放出去吃草不花钱；冬季每只每天 1 份干草（玛妮牧场 50g）；
- 猪：冬季不出门、不产松露；程序不知道哪天下雨，非冬季按每天 1 个计（偏乐观）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

SEASON_DAYS = 28


@dataclass(frozen=True)
class Animal:
    name: str
    name_en: str
    building: str
    building_en: str
    buy_price: int
    mature_nights: int
    produce_every_days: int
    product: str
    product_en: str
    product_price: int
    tool: str = ""
    outdoor_only: bool = False  # 只在出门时产出（猪）


@dataclass(frozen=True)
class Herd:
    """一群同种动物：new=True 表示本季第 1 天新买的，False 表示上季带来的成年动物。"""
    animal: Animal
    count: int
    new: bool = True


@dataclass
class LivestockResult:
    cash_by_day: dict[int, float] = field(default_factory=dict)
    income: int = 0
    purchase_cost: int = 0
    hay_cost: int = 0
    products: dict[str, int] = field(default_factory=dict)  # 产品名 → 件数
    lines: list[str] = field(default_factory=list)

    @property
    def net(self) -> int:
        return self.income - self.purchase_cost - self.hay_cost


def parse_animals(spec, animals: list[Animal]) -> dict[Animal, int]:
    """“鸡:2,牛:1”或 {"鸡": 2} → {Animal: 数量}。名字可以是中文或英文；写错、数量不是正整数时报 ValueError。"""
    if not spec:
        return {}
    if isinstance(spec, str):
        pairs = []
        for part in re.split(r"[,，;；]", spec):
            part = part.strip()
            if not part:
                continue
            m = re.fullmatch(r"(.+?)\s*[:：×x*]\s*(\d+)", part)
            if not m:
                raise ValueError(f"动物写法应为“名字:数量”，例如“鸡:2,牛:1”，收到：{part}")
            pairs.append((m.group(1).strip(), int(m.group(2))))
    else:
        pairs = [(str(k).strip(), v) for k, v in dict(spec).items()]
    by_name = {a.name: a for a in animals} | {a.name_en.lower(): a for a in animals}
    out: dict[Animal, int] = {}
    for name, count in pairs:
        animal = by_name.get(name) or by_name.get(name.lower())
        if animal is None:
            known = "、".join(a.name for a in animals)
            raise ValueError(f"数据库中没有动物“{name}”（目前收录：{known}）")
        if not isinstance(count, int) or isinstance(count, bool) or count < 1:
            raise ValueError(f"{animal.name}的数量必须是正整数，收到 {count!r}")
        out[animal] = out.get(animal, 0) + count
    return out


def _every(a: Animal) -> str:
    return "每天" if a.produce_every_days == 1 else f"每 {a.produce_every_days} 天"


def produce_days(herd: Herd, season: str) -> list[int]:
    """这群动物本季产出的日子。"""
    if herd.animal.outdoor_only and season == "winter":
        return []
    every = herd.animal.produce_every_days
    first = 1 + herd.animal.mature_nights + every if herd.new else every
    return list(range(first, SEASON_DAYS + 1, every))


def simulate_livestock(herds: list[Herd], season: str, hay_price: int) -> LivestockResult:
    result = LivestockResult()
    if not herds:
        return result

    def add(day: int, amount: float):
        result.cash_by_day[day] = result.cash_by_day.get(day, 0) + amount

    for h in herds:
        a = h.animal
        label = f"{a.name} ×{h.count}" + ("（本季第 1 天买入）" if h.new else "（上季带来）")
        if h.new:
            cost = a.buy_price * h.count
            result.purchase_cost += cost
            add(1, -cost)
        days = produce_days(h, season)
        for d in days:
            add(d, a.product_price * h.count)
        n = len(days) * h.count
        result.products[a.product] = result.products.get(a.product, 0) + n
        result.income += n * a.product_price
        if days:
            grow = f"，第 {1 + a.mature_nights} 天成年" if h.new else ""
            result.lines.append(f"{label}{grow}：{a.product} {len(days)} 次 × {h.count} 只 × {a.product_price}g"
                                f" = {n * a.product_price}g（第 {days[0]} 天起{_every(a)}一次）")
        else:
            why = "冬季不出门，不产松露" if a.outdoor_only and season == "winter" else "本季还来不及产出"
            result.lines.append(f"{label}：{why}")
        if h.new:
            result.lines.append(f"买{a.name} = {h.count} × {a.buy_price} = {a.buy_price * h.count}g（玛妮牧场）")

    if season == "winter":
        total = sum(h.count for h in herds)
        result.hay_cost = total * SEASON_DAYS * hay_price
        for day in range(1, SEASON_DAYS + 1):
            add(day, -total * hay_price)
        result.lines.append(f"冬季干草 = {total} 只 × 28 天 × {hay_price}g = {result.hay_cost}g"
                            "（动物冬季不出门；如果秋天存够了干草，实际支出会更少）")
    return result


def describe_herds(herds: list[Herd], season: str, hay_price: int) -> str:
    """Prompt【数据参考】里的畜牧一节。"""
    if not herds:
        return "（玩家没有养动物）"
    lines = []
    for h in herds:
        a = h.animal
        days = produce_days(h, season)
        src = "本季第 1 天从玛妮牧场买入" if h.new else "上季带来，已成年"
        need = f"，需要{a.building}" + (f"和{a.tool}" if a.tool else "")
        when = f"第 {days[0]} 天起{_every(a)}产 1 个{a.product}（{a.product_price}g），本季共 {len(days)} 次" \
            if days else ("冬季不出门，不产松露" if a.outdoor_only and season == "winter" else "本季来不及产出")
        lines.append(f"- {a.name} ×{h.count}（{src}{need}）：{when}")
    result = simulate_livestock(herds, season, hay_price)
    lines.append(f"程序计算：产出收入 {result.income}g，买动物 {result.purchase_cost}g，干草 {result.hay_cost}g，"
                 f"畜牧净收支 {result.net:+d}g（只算基础产品，已计入资金）。")
    lines.append("每天的目标都要包含“照料动物”（喂食、抚摸、收产品），用完全相同的措辞写在当天主要任务后面。")
    return "\n".join(lines)
