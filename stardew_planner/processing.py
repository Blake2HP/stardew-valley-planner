"""加工模拟：按机器数量和加工时间安排小桶、罐头瓶，算出加工带来的收入变化。

- 每次收获按品质拆成若干份（第一个作物按品质分布，额外产出为普通品质），数量是期望值；
- 每份作物交给“每台机器每天多赚最多”的机器类型：多赚 = 加工品单价 − 原料个数 × 直接卖单价
  （加工品不继承品质；咖啡要 5 颗豆子），除以加工天数（向上取整，所以每台机器每天最多做 1 批）。所以低星作物优先加工，和 analysis.sale_plan 的结论一致；
- 机器数量有限：只有在第 28 天前还来得及开工的部分才留着等加工，其余收获当天直接卖；
- 留着加工的作物当天不卖（现金减少它的直接卖价），加工完成当天按加工品价格入账；
- 第 28 天还在机器里的货品带到下一季，在完成那天入账（跨季的加工品）。

数量按期望值计算（可能有小数），机器按“流体”近似：同一类机器的空闲容量可以分给多份作物。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from math import ceil

from .bonuses import QUALITIES, Bonuses
from .data_loader import ArtisanRule, Crop

SEASON_DAYS = 28


@dataclass(frozen=True)
class GoodsInProcess:
    """季末还在机器里的加工品，带到下一季。"""
    product: str
    machine: str
    units: float
    value: float      # 完成后的总价值
    finish_day: int   # 下一季的第几天完成


@dataclass
class ProcessingResult:
    cash_by_day: dict[int, float] = field(default_factory=dict)  # 现金变化：留作加工 −直接卖价，完成 +加工品价
    completed: dict[str, float] = field(default_factory=dict)    # 本季完成的加工品（品名 → 件数）
    completed_value: float = 0.0       # 本季完成的加工品价值（含上季带来的）
    held_value: float = 0.0            # 本季留作加工的作物原本直接卖的价值
    carried_value_in: float = 0.0      # 其中来自上季、本季完成的部分
    goods_out: list[GoodsInProcess] = field(default_factory=list)
    lines: list[str] = field(default_factory=list)

    @property
    def net(self) -> float:
        """对本季种植收支的影响：完成的加工品 − 留作加工的作物原本的直接卖价。"""
        return self.completed_value - self.held_value


@dataclass
class _Batch:
    crop: str
    product: str
    machine: str
    units: float
    direct: float     # 每件直接卖的价格
    artisan: float    # 每件加工品的价格
    days: int         # 加工天数（向上取整）
    per: int = 1      # 每件加工品要几个原料（咖啡 = 5）

    @property
    def rate(self) -> float:
        """每台机器每天多赚多少：(加工品价 − 原料个数 × 直接卖价) / 加工天数。"""
        return (self.artisan - self.per * self.direct) / self.days


def _units_by_quality(crop: Crop, bonuses: Bonuses) -> dict[str, float]:
    """每格每次收获各品质的期望件数（额外产出一律普通品质）。"""
    odds = bonuses.quality_odds()
    units = {q: odds[q] for q in QUALITIES}
    units["normal"] += crop.harvest_yield - 1
    return {q: u for q, u in units.items() if u > 1e-12}


def _candidates(crop: Crop, quality: str, rules: list[ArtisanRule], machines: dict[str, int],
                bonuses: Bonuses) -> list[_Batch]:
    """这一份作物可以交给哪些机器：比直接卖赚钱的，按“每台机器每天多赚多少”从高到低排。
    每种机器只取最赚钱的那条规则；前面的机器排满了，再用后面的。"""
    direct = bonuses.crop_price(crop, quality)
    best: dict[str, _Batch] = {}
    for rule in rules:
        if machines.get(rule.machine, 0) <= 0:
            continue
        batch = _Batch(crop.name, rule.product, rule.machine, 0.0, direct,
                       bonuses.artisan_price(rule, crop), max(1, ceil(rule.processing_days)), max(1, rule.input_count))
        if batch.artisan > batch.per * direct and (rule.machine not in best or batch.rate > best[rule.machine].rate):
            best[rule.machine] = batch
    return sorted(best.values(), key=lambda b: b.rate, reverse=True)


def simulate_processing(
    harvests: list[tuple[int, Crop, float]],
    rules_for,
    machines: dict[str, int],
    bonuses: Bonuses,
    goods_in: tuple[GoodsInProcess, ...] = (),
) -> ProcessingResult:
    """harvests：本季每次收获 (日, 作物, 格数)；rules_for(crop) 返回可用的加工规则；machines：机器名 → 台数。"""
    result = ProcessingResult()
    # 机器里的货品：(件数, 完成日, 品名, 单价, 是否上季带来)
    running: dict[str, list[tuple[float, int, str, float, bool]]] = {m: [] for m in machines}
    queues: dict[str, list[_Batch]] = {m: [] for m in machines}

    for g in goods_in:  # 上季带来的货品先占着机器
        running.setdefault(g.machine, []).append(
            (g.units, g.finish_day, g.product, g.value / g.units if g.units else 0.0, True))

    arrivals: dict[int, list[tuple[float, list[_Batch]]]] = {}  # 日 → [(件数, 候选机器)]
    for day, crop, tiles in harvests:
        rules = rules_for(crop)
        for quality, per_tile in _units_by_quality(crop, bonuses).items():
            options = _candidates(crop, quality, rules, machines, bonuses)
            if options:
                arrivals.setdefault(day, []).append((per_tile * tiles, options))

    def add_cash(day: int, amount: float):
        result.cash_by_day[day] = result.cash_by_day.get(day, 0.0) + amount

    for day in range(1, SEASON_DAYS + 1):
        # 1) 完成
        for machine, jobs in running.items():
            keep = []
            for job in jobs:
                units, finish, product, price, carried = job
                if finish <= day:
                    add_cash(day, units * price)
                    result.completed_value += units * price
                    result.completed[product] = result.completed.get(product, 0.0) + units
                    if carried:
                        result.carried_value_in += units * price
                else:
                    keep.append(job)
            running[machine] = keep

        # 2) 收获：在第 28 天前还来得及开工的部分留着加工，其余直接卖（直接卖已经算在种植收入里）
        # 高价值的先分配：同一天的收获按最好的候选“每天多赚多少”排序
        for units, options in sorted(arrivals.get(day, []), key=lambda a: a[1][0].rate, reverse=True):
            remaining = units
            for batch in options:  # 最赚钱的机器排满了，再试下一种
                if remaining <= 1e-9:
                    break
                count = machines.get(batch.machine, 0)
                busy_days = sum(max(0, min(finish, SEASON_DAYS + 1) - day) * u
                                for u, finish, _, _, _ in running.get(batch.machine, []))
                queued_days = sum(b.units / b.per * b.days for b in queues[batch.machine])
                free_slot_days = count * (SEASON_DAYS - day + 1) - busy_days - queued_days
                take = min(remaining, max(0.0, free_slot_days) / batch.days * batch.per)
                if take <= 1e-9:
                    continue
                queues[batch.machine].append(_Batch(batch.crop, batch.product, batch.machine, take, batch.direct,
                                                    batch.artisan, batch.days, batch.per))
                add_cash(day, -take * batch.direct)
                result.held_value += take * batch.direct
                remaining -= take

        # 3) 开工：空闲的机器先做“每天多赚最多”的
        for machine, queue in queues.items():
            free = machines.get(machine, 0) - sum(job[0] for job in running.get(machine, []))
            queue.sort(key=lambda b: b.rate, reverse=True)
            while queue and free > 1e-9:
                batch = queue[0]
                start = min(batch.units / batch.per, free)  # 开工的件数（每件占一台机器）
                running.setdefault(machine, []).append((start, day + batch.days, batch.product, batch.artisan, False))
                free -= start
                batch.units -= start * batch.per
                if batch.units <= 1e-9:
                    queue.pop(0)

    # 季末：还没开工的（按理不会有）当天直接卖；还在机器里的带到下一季
    for machine, queue in queues.items():
        for batch in queue:
            add_cash(SEASON_DAYS, batch.units * batch.direct)
            result.held_value -= batch.units * batch.direct
    for machine, jobs in running.items():
        for units, finish, product, price, _ in jobs:
            result.goods_out.append(GoodsInProcess(product, machine, units, units * price, finish - SEASON_DAYS))

    result.lines = _describe(result, machines)
    return result


def _describe(r: ProcessingResult, machines: dict[str, int]) -> list[str]:
    if not any(machines.values()) and not r.completed and not r.goods_out:
        return []
    have = "、".join(f"{m} {n} 个" for m, n in machines.items() if n) or "无"
    lines = []
    if r.completed or r.held_value:
        done = "、".join(f"{p} {n:.1f} 件" for p, n in r.completed.items()) or "无"
        lines.append(f"加工（{have}）：本季完成 {done}，加工品收入 {r.completed_value:.0f}g；"
                     f"留作加工的作物原本直接卖值 {r.held_value:.0f}g；加工净增 {r.net:+.0f}g")
        if r.carried_value_in:
            lines.append(f"其中上季机器里的货品本季完成，收入 {r.carried_value_in:.0f}g")
    for g in r.goods_out:
        lines.append(f"季末仍在{g.machine}里：{g.product} {g.units:.1f} 件，下季第 {g.finish_day} 天完成，约 {g.value:.0f}g（计入下季）")
    return lines
