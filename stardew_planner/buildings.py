"""罗宾的建筑工期：在木匠的商店下单建农场建筑，要等几天才完工（数据见 data/buildings.json）。

模型可以在输出 JSON 里给出 constructions：[{"building": "鸡舍", "day": 3}]，day 是去罗宾那里下单付款的日子。
程序据此（规则来自 wiki Carpenter's Shop）：
- 下单日不能是周二（商店不营业）或节日；
- 下单第二天开工，节日当天不施工、工期顺延；工期 N 天 = 下单后第 N 天早上完工；
- 同一时间只能建一座：上一座完工前又下单，报冲突；
- 下单那天的目标要写明建什么；
- 资金：建筑费用在下单日扣掉（材料要自己准备，不计）。
校验发现的问题交给模型修正。
"""

from __future__ import annotations

from dataclasses import dataclass

SEASON_DAYS = 28
WEEKDAYS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")


def weekday(day: int) -> str:
    """每季第 1 天是周一。"""
    return WEEKDAYS[(day - 1) % 7]


@dataclass(frozen=True)
class Material:
    name: str
    name_en: str
    count: int


@dataclass(frozen=True)
class Building:
    name: str
    name_en: str
    price: int
    days: int
    materials: tuple[Material, ...] = ()
    upgrades_from: str | None = None


@dataclass(frozen=True)
class BuildingRules:
    buildings: tuple[Building, ...]
    closed_weekday: str = "周二"
    night_festivals: tuple[str, ...] = ()

    def find(self, name: str) -> Building | None:
        name = str(name).strip()
        return next((b for b in self.buildings if name in (b.name, b.name_en) or name.lower() == b.name_en.lower()), None)


@dataclass(frozen=True)
class Construction:
    building: Building
    day: int          # 下单日
    ready_day: int    # 完工的早上（可能 > 28，跨到下一季）

    @property
    def label(self) -> str:
        done = f"第 {self.ready_day} 天完工" if self.ready_day <= SEASON_DAYS else f"下一季第 {self.ready_day - SEASON_DAYS} 天完工"
        return f"{self.building.name}（第 {self.day} 天下单，{done}）"


def ready_day(order_day: int, days: int, no_work: set[int]) -> int:
    """第 order_day 天下单，第二天开工；节日不施工，工期顺延。返回完工的那天（早上可用）。"""
    worked, day = 0, order_day
    while worked < days:
        day += 1
        if day not in no_work:
            worked += 1
    return day if days else order_day


def parse_constructions(raw, rules: BuildingRules, no_work: set[int]) -> tuple[list[Construction], list[str]]:
    """模型输出的 constructions → Construction；写错的条目丢弃并给出问题。"""
    if raw in (None, []):
        return [], []
    if not isinstance(raw, list):
        return [], ["⚠️ constructions 必须是数组，例如 [{\"building\": \"鸡舍\", \"day\": 3}]"]
    out, problems = [], []
    for i, item in enumerate(raw):
        try:
            building = rules.find(item["building"])
            day = int(item["day"])
        except (TypeError, KeyError, ValueError, AttributeError):
            building, day = None, None
        if building is None or day is None or not 1 <= day <= SEASON_DAYS:
            known = "、".join(b.name for b in rules.buildings)
            problems.append(f"⚠️ constructions[{i}] 无效（building 只能是{known}，day 为 1-28）：{item!r}")
            continue
        out.append(Construction(building, day, ready_day(day, building.days, no_work)))
    return sorted(out, key=lambda c: c.day), problems


def check_constructions(items: list[Construction], goals: dict[int, str], rules: BuildingRules,
                        closed: set[int]) -> list[str]:
    """下单日不能是周二或节日；上一座完工前不能再下单；下单那天的目标要写明。"""
    problems = []
    for c in items:
        b = c.building
        if weekday(c.day) == rules.closed_weekday:
            problems.append(f"⚠️ 建筑校验：第 {c.day} 天是{rules.closed_weekday}，罗宾的商店不营业，没法下单建{b.name}；"
                            "换到其他日子下单")
        elif c.day in closed:
            problems.append(f"⚠️ 建筑校验：第 {c.day} 天是节日，罗宾的商店不营业，没法下单建{b.name}")
        if b.name not in goals.get(c.day, ""):
            problems.append(f"⚠️ 第 {c.day} 天的目标没有写“找罗宾建{b.name}”（constructions 里安排了这天下单）")
    for prev, nxt in zip(items, items[1:]):
        if nxt.day < prev.ready_day:
            problems.append(f"⚠️ 建筑冲突：罗宾还在建{prev.label}，第 {nxt.day} 天不能再下单建{nxt.building.name}；"
                            f"等第 {prev.ready_day} 天完工后再下单")
    return problems


def construction_costs(items: list[Construction]) -> list[tuple[int, int, str]]:
    """(下单日, 金币, 说明)。"""
    out = []
    for c in items:
        mats = "、".join(f"{m.name} {m.count}" for m in c.building.materials)
        need = f" + {mats}（材料需自备，不计）" if mats else ""
        out.append((c.day, c.building.price, f"建筑：{c.label} = {c.building.price}g{need}"))
    return out


def describe_buildings(rules: BuildingRules | None) -> str:
    """Prompt【数据参考】里的建筑一节。"""
    if rules is None:
        return "（没有建筑数据）"
    rows = "；".join(f"{b.name} {b.price}g、{b.days} 天" + (f"（由{b.upgrades_from}升级）" if b.upgrades_from else "")
                    for b in rules.buildings)
    return ("找罗宾（木匠的商店）建农场建筑：周二不营业、节日不营业，这两种日子不能下单；下单第二天开工，节日不施工、工期顺延，"
            "工期 N 天 = 下单后第 N 天早上完工；同一时间只能建一座，完工前不能再下单。每季第 1 天是周一，"
            f"周二是第 2、9、16、23 天。价格与工期（材料另备）：{rows}。")
