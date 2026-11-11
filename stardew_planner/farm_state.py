"""跨季连续规划：种植清单 → 跨季收获模拟 → 本季资金 → 季末农场状态。

所有数字由程序根据 data/ 计算，不采信模型自己写的金额；
模型给出的钓鱼、挖矿等“其他收入”只作为估算单独显示。

约定：
- 收获日从“本季第 1 天”起算，> 28 表示落在之后的季节（例如 30 = 下一季第 2 天）；
- 作物能一直生长到它连续可种的最后一个季节的第 28 天（玉米：夏季种下可活到秋季末）；
- 每条种植记录是一次播种；非再生作物收获后补种需另起一条；
- 同一天先结算收获，再支付种子钱；
- 肥料留在土里，收获后在同一格补种不必重新施肥：肥料用量 = 本季新播种同时占用的最多格数。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .bonuses import NO_BONUSES, Bonuses
from .processing import GoodsInProcess, simulate_processing
from .data_loader import SEASON_ZH, Crop, GameData, HoneyRule, normalize_season
from .honey import simulate_honey
from .livestock import Herd, simulate_livestock

BEE_HOUSE = "蜂房"  # machines 里的蜂房数量：不参与小桶 / 罐头瓶的加工模拟，单独按开着的花算蜂蜜

SEASON_DAYS = 28
SEASON_ORDER = ["spring", "summer", "fall", "winter"]
GREENHOUSE = "greenhouse"
ISLAND = "island"  # 姜岛农场：和温室一样不受季节限制
SEASON_FREE = (GREENHOUSE, ISLAND)
LOCATIONS = ("pelican_town", "ginger_island")
GREENHOUSE_HORIZON_SEASONS = 8  # 温室里的可再生作物不会枯死：模拟往后 8 季（两年）的收获


def following_season(season: str, year: int) -> tuple[str, int]:
    """下一季及其年份：冬季之后进入下一年的春季。"""
    i = SEASON_ORDER.index(normalize_season(season))
    return SEASON_ORDER[(i + 1) % 4], year + (1 if i == 3 else 0)


def alive_seasons(crop: Crop, season: str) -> int:
    """从本季起，作物能连续生长几个季节（玉米在夏季 = 2，在秋季 = 1）。"""
    n, s = 1, normalize_season(season)
    while n < 4:
        s, _ = following_season(s, 0)
        if s not in crop.seasons:
            break
        n += 1
    return n


def harvest_days(crop: Crop, plant_day: int, season: str, bonuses: Bonuses = NO_BONUSES,
                 greenhouse: bool = False) -> list[int]:
    """一次播种的全部收获日（从本季第 1 天起算，可跨季）。

    首次收获日 = 种植日 + 生长天数；可再生作物之后每隔再生天数收获一次，
    直到作物能存活的最后一天（28 × 可存活季数）。成熟前就枯死则返回空列表。
    温室和姜岛农场不受季节限制（greenhouse=True），按往后 GREENHOUSE_HORIZON_SEASONS 季计算。
    """
    seasons = GREENHOUSE_HORIZON_SEASONS if greenhouse else alive_seasons(crop, season)
    limit = SEASON_DAYS * seasons
    first = plant_day + bonuses.grow_days(crop)
    if first > limit:
        return []
    if not crop.regrow_days:
        return [first]
    return list(range(first, limit + 1, crop.regrow_days))


def season_label(season: str, day: int) -> str:
    """把跨季的天数写成“秋季第 2 天”。"""
    s = normalize_season(season)
    while day > SEASON_DAYS:
        s, _ = following_season(s, 0)
        day -= SEASON_DAYS
    return f"{SEASON_ZH[s]}第 {day} 天"


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Planting:
    day: int
    crop: Crop
    tiles: int
    harvests: tuple[int, ...] = ()  # 收获日（从本季第 1 天起算）
    place: str = "field"            # field = 室外农田；greenhouse = 温室

    @property
    def in_greenhouse(self) -> bool:
        return self.place == GREENHOUSE

    @property
    def season_free(self) -> bool:
        return self.place in SEASON_FREE


@dataclass(frozen=True)
class CarryCrop:
    """季末仍在地里、会在下一季继续生长的作物。"""
    crop: Crop
    tiles: int
    origin: str                     # 例如“夏季第 1 天种下”
    harvest_days: tuple[int, ...]   # 从“下一季第 1 天”起算的收获日
    place: str = "field"

    def describe(self, season: str) -> str:
        days = "、".join(str(d) for d in self.harvest_days if d <= SEASON_DAYS)
        later = [d for d in self.harvest_days if d > SEASON_DAYS]
        where = {GREENHOUSE: "，温室", ISLAND: "，姜岛"}.get(self.place, "")
        text = f"{self.crop.name} ×{self.tiles} 格（{self.origin}{where}）：{SEASON_ZH[season]}第 {days} 天可收获" if days \
            else f"{self.crop.name} ×{self.tiles} 格（{self.origin}{where}）：本季无收获"
        if later:
            text += f"，之后还会在{season_label(season, later[0])}起继续收获"
        return text


@dataclass(frozen=True)
class OtherIncome:
    """模型估算的非种植收入（钓鱼、挖矿、采集……），只作参考。"""
    source: str
    gold: int
    note: str = ""


@dataclass
class SeasonFinance:
    start_gold: int | None              # 期初资金（种植链，程序计算）；未知时为 None
    start_gold_estimated: int | None    # 期初资金（含之前各季的模型估算）
    crop_revenue: int
    seed_cost: int
    fertilizer_cost: int
    other_income: list[OtherIncome] = field(default_factory=list)
    lines: list[str] = field(default_factory=list)       # 带公式的明细
    seeds_left: dict[str, float] = field(default_factory=dict)  # 季末手上剩余的种子（期望值）
    warnings: list[str] = field(default_factory=list)    # 现金不足等
    processing_net: int = 0   # 加工净增：完成的加工品 − 留作加工的作物原本的直接卖价（没有机器时为 0）
    goods_out: list[GoodsInProcess] = field(default_factory=list)  # 季末还在机器里的货品，带到下一季
    honey_income: int = 0     # 蜂房产的蜂蜜收入（没有蜂房时为 0）
    livestock_net: int = 0    # 畜牧净收支：动物产品收入 − 买动物 − 冬季干草（没有动物时为 0）
    bundle_value: int = 0     # 留给社区中心收集包、没有卖的作物原本的售价（献祭路线）
    bundle_done: tuple[tuple[str, str], ...] = ()  # 到本季为止已经留下的 (收集包英文名, 作物英文名)
    tool_cost: int = 0        # 工具升级费用（交送时支付）
    herds_out: tuple[tuple[str, int], ...] = ()  # 季末拥有的动物（名字, 数量），带到下一季

    @property
    def crop_net(self) -> int:
        return (self.crop_revenue - self.seed_cost - self.fertilizer_cost + self.processing_net
                + self.honey_income + self.livestock_net - self.bundle_value - self.tool_cost)

    @property
    def other_total(self) -> int:
        return sum(i.gold for i in self.other_income)

    @property
    def end_gold(self) -> int | None:
        return None if self.start_gold is None else self.start_gold + self.crop_net

    @property
    def end_gold_estimated(self) -> int | None:
        base = self.start_gold_estimated
        return None if base is None else base + self.crop_net + self.other_total


@dataclass(frozen=True)
class FarmState:
    """一季结束时的农场状态，用来衔接下一季。"""
    season: str                         # 刚结束的季节
    year: int
    next_season: str
    next_year: int
    gold: int | None                    # 期末资金（只算种植收支，程序计算）
    gold_estimated: int | None          # 期末资金（含模型估算的其他收入）
    carry: tuple[CarryCrop, ...] = ()
    cleared: tuple[str, ...] = ()       # 本季种过、换季时清空的作物
    planned_goals: tuple[str, ...] = ()  # 上季的计划目标（模型输出，不代表已完成）
    seeds: tuple[tuple[str, float], ...] = ()  # 季末手上剩余的种子（作物名, 数量），下一季先用
    goods: tuple[GoodsInProcess, ...] = ()     # 季末还在机器里的加工品，下一季完成时入账
    animals: tuple[tuple[str, int], ...] = ()  # 季末拥有的动物（名字, 数量），下一季已成年
    bundle_done: tuple[tuple[str, str], ...] = ()  # 已经留给社区中心的 (收集包英文名, 作物英文名)

    @property
    def carried_names(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(c.crop.name for c in self.carry))


# ---------------------------------------------------------------------------
# 种植清单
# ---------------------------------------------------------------------------

def resolve_plantings(
    raw: list[dict], data: GameData, season: str, year: int, bonuses: Bonuses = NO_BONUSES,
    greenhouse: bool = False, notes: list[str] | None = None, location: str = "pelican_town",
) -> tuple[list[Planting], list[str]]:
    """把模型输出的 plantings 转成 Planting；不合法的条目剔除并给出警告（不计入资金）。

    greenhouse 表示温室已修好；"place": "greenhouse" 的条目不受季节限制。
    notes 用来收集说明类提示（比如温室里种非当季作物，种子要提前买好），不算问题。
    location="ginger_island" 时所有种植都在姜岛农场：和温室一样不受季节限制、换季不枯死。
    """
    season = normalize_season(season)
    by_name = {n: c for c in data.crops for n in c.names} | {c.name_en.lower(): c for c in data.crops}
    plantings, warnings = [], []
    for item in raw:
        name, day, tiles = item["crop"], item["day"], item["tiles"]
        place = ISLAND if location == "ginger_island" else item.get("place", "field")
        crop = by_name.get(name) or by_name.get(name.lower())
        where = f"种植清单第 {day} 天的{name}" + {GREENHOUSE: "（温室）", ISLAND: "（姜岛）"}.get(place, "")
        if crop is None:
            warnings.append(f"⚠️ {where}不在数据库中，未计入资金")
        elif place == GREENHOUSE and not greenhouse:
            warnings.append(f"⚠️ {where}：温室还没修好（需要完成社区中心茶水间收集包，或在 Joja 花 35,000g），未计入资金")
        elif place not in SEASON_FREE and season not in crop.seasons:
            warnings.append(f"⚠️ {where}不是{SEASON_ZH[season]}作物，未计入资金")
        elif year < crop.available_year:
            warnings.append(f"⚠️ {where}：种子第 {crop.available_year} 年起才出售，未计入资金")
        elif day < crop.earliest_available_day and place not in SEASON_FREE:
            warnings.append(f"⚠️ {where}：种子第 {crop.earliest_available_day} 天才开售，未计入资金")
        else:
            in_gh = place in SEASON_FREE
            if in_gh and season not in crop.seasons and notes is not None:
                allowed = "/".join(SEASON_ZH[s] for s in crop.seasons)
                into = "带上岛" if place == ISLAND else "带进温室"
                notes.append(f"ℹ️ {where}：商店只在{allowed}卖这种种子，需要提前买好{into}")
            plantings.append(Planting(day, crop, tiles, tuple(harvest_days(crop, day, season, bonuses, in_gh)), place))
    return sorted(plantings, key=lambda p: p.day), warnings


def greenhouse_warnings(plantings: list[Planting], carry_in: tuple[CarryCrop, ...], limit: int,
                        place: str = GREENHOUSE) -> list[str]:
    """温室（或姜岛农场）同时占用的格数不能超过上限（可再生作物一直占着，单次收获的作物收获当天空出来）。"""
    if not any(p.place == place for p in plantings) and not any(c.place == place for c in carry_in):
        return []
    label = "温室" if place == GREENHOUSE else "姜岛农场"
    for day in range(1, SEASON_DAYS + 1):
        used = sum(c.tiles for c in carry_in if c.place == place
                   and (c.crop.regrow_days or (c.harvest_days and day < c.harvest_days[0])))
        for p in plantings:
            if p.place == place and p.day <= day and (p.crop.regrow_days or not p.harvests or day < p.harvests[0]):
                used += p.tiles
        if used > limit:
            return [f"⚠️ 第 {day} 天{label}要用 {used} 格，超过{label}的 {limit} 格"]
    return []


def bus_warnings(plantings: list[Planting], year: int, already: set[tuple[int, str]] = frozenset()) -> list[str]:
    """第 1 年的种植清单里需要先修好巴士才买得到的作物；already 是日程校验已经报过的 (日期, 作物)。"""
    if year != 1:
        return []
    return [f"⚠️ 种植清单第 {p.day} 天的{p.crop.name}：种子在{p.crop.seed_source}出售，要先修好巴士才能买到；"
            "第 1 年如果还没修好，这笔支出和收益都不成立"
            for p in plantings if p.crop.requires_bus and (p.day, p.crop.name) not in already]


def late_planting_warnings(
    plantings: list[Planting], season: str, bonuses: Bonuses = NO_BONUSES, already: set[tuple[int, str]] = frozenset()
) -> list[str]:
    """种植清单里来不及成熟的播种：没有任何收获，种子钱仍计入支出。

    already 是日程校验已经报过的 (日期, 作物)，不重复警告。
    """
    out = []
    for p in plantings:
        if p.harvests or (p.day, p.crop.name) in already:
            continue
        grow = bonuses.grow_days(p.crop)
        limit = SEASON_DAYS * alive_seasons(p.crop, season)
        out.append(f"⚠️ 种植清单第 {p.day} 天的{p.crop.name}来不及成熟，这次播种没有收获，种子钱仍计入支出"
                   f"（成熟日 = 种植日 {p.day} + 生长 {grow} 天 = {p.day + grow} > 最后存活日 {limit}）")
    return out


# ---------------------------------------------------------------------------
# 资金
# ---------------------------------------------------------------------------

def _new_fertilized_tiles(plantings: list[Planting]) -> dict[int, int]:
    """每天需要新施肥的格数：占用格数创新高时才买，补种到已施肥的格子不用再买。

    本季新播种的占用：非再生作物收获当天就空出来，可再生作物一直占到季末。
    """
    peak, purchases = 0, {}
    for day in range(1, SEASON_DAYS + 1):
        used = 0
        for p in plantings:
            if p.day > day:
                continue
            if p.crop.regrow_days or not p.harvests:
                used += p.tiles
            elif day < p.harvests[0]:
                used += p.tiles
        if used > peak:
            purchases[day] = used - peak
            peak = used
    return purchases


def _peak_tiles(plantings: list[Planting]) -> int:
    """本季新播种同时占用的最多格数。"""
    return sum(_new_fertilized_tiles(plantings).values())


def compute_finance(
    plantings: list[Planting],
    carry_in: tuple[CarryCrop, ...] = (),
    *,
    start_gold: int | None = None,
    start_gold_estimated: int | None = None,
    other_income: list[OtherIncome] | None = None,
    bonuses: Bonuses = NO_BONUSES,
    seed_stock: dict[str, float] | None = None,
    stock_label: str = "开局赠送",
    machines: dict[str, int] | None = None,
    rules_for=None,
    goods_in: tuple[GoodsInProcess, ...] = (),
    season: str | None = None,
    honey: HoneyRule | None = None,
    honey_all_seasons: bool = False,
    herds: tuple[Herd, ...] = (),
    hay_price: int = 50,
    set_aside=(),
    bundle_done_in: tuple[tuple[str, str], ...] = (),
    fixed_costs: list[tuple[int, int, str]] = (),
) -> SeasonFinance:
    """本季种植收支（只算本季内发生的收获；跨季收获计入下一季）。

    seed_stock 是季初已有的种子（作物名 → 数量），例如开局赠送的 15 颗防风草种子、上季剩下的种子；
    播种时最先使用，明细里标为 stock_label。季末剩下的种子记在 seeds_left，带到下一季。
    machines（机器名 → 台数）和 rules_for（作物 → 加工规则）给出时模拟加工，见 processing.py；
    goods_in 是上季还在机器里的货品。
    machines 里的“蜂房”按室外开着的花算蜂蜜（见 honey.py），需要同时给出 season 和 honey。
    herds 是本季的动物（见 livestock.py），需要同时给出 season；不给时不计畜牧。
    set_aside 是留给社区中心收集包的作物（bundles.Reservation），留下的按售价从收入里扣掉。
    fixed_costs 是其他固定支出 (日, 金币, 说明)，例如工具升级费。
    """
    revenue_by_day: dict[int, float] = {}
    drops_by_day: dict[tuple[str, int], float] = {}  # (作物, 日) → 掉落的种子数（期望）
    harvest_events: list[tuple[int, Crop, float]] = []  # (日, 作物, 格数)，给加工模拟用
    lines = []

    def add_harvests(crop: Crop, tiles: int, days, label: str):
        days = [d for d in days if d <= SEASON_DAYS]
        if not days:
            return
        for d in days:
            if crop.seed_drop:
                drops_by_day[(crop.name, d)] = drops_by_day.get((crop.name, d), 0) + tiles * crop.seed_drop
        price = bonuses.crop_price(crop)
        value = bonuses.expected_crop_value(crop)  # 每格每次收获的期望收入（计入品质）
        each = tiles * value
        for d in days:
            revenue_by_day[d] = revenue_by_day.get(d, 0) + each
            harvest_events.append((d, crop, tiles))
        if bonuses.quality_active:
            unit = f" × 品质期望收入 {value:.2f}g"
        else:
            unit = ("" if crop.harvest_yield == 1 else f" × 单次产量 {crop.harvest_yield}") + f" × {price}g"
        lines.append(f"{label}：{len(days)} 次 × {tiles} 格{unit} = {len(days) * each:g}g"
                     f"（第 {'、'.join(map(str, days))} 天）")

    for c in carry_in:
        add_harvests(c.crop, c.tiles, c.harvest_days, f"{c.crop.name}（上季延续）")
    for p in plantings:
        add_harvests(p.crop, p.tiles, p.harvests, f"{p.crop.name}（第 {p.day} 天种）")

    # 种子：先用季初已有的种子，再用收获掉落的种子，不够再买（同一天先收获、再播种）
    stock: dict[str, float] = dict(seed_stock or {})
    inventory: dict[str, float] = {}
    spent_by_day: dict[int, float] = {}
    terms, seed_total = [], 0.0
    by_day: dict[int, list[Planting]] = {}
    for p in plantings:
        by_day.setdefault(p.day, []).append(p)
    for day in range(1, SEASON_DAYS + 1):
        for (name, d), n in drops_by_day.items():
            if d == day:
                inventory[name] = inventory.get(name, 0) + n
        for p in by_day.get(day, []):
            from_stock = min(stock.get(p.crop.name, 0), p.tiles)
            stock[p.crop.name] = stock.get(p.crop.name, 0) - from_stock
            used = min(inventory.get(p.crop.name, 0), p.tiles - from_stock)
            inventory[p.crop.name] = inventory.get(p.crop.name, 0) - used
            cost = (p.tiles - from_stock - used) * p.crop.seed_price
            spent_by_day[day] = spent_by_day.get(day, 0) + cost
            seed_total += cost
            minus = (f" − {stock_label} {from_stock:g}" if from_stock else "") + (f" − 掉落种子 {used:g}" if used else "")
            terms.append(f"({p.tiles}{minus}) × {p.crop.seed_price}" if minus else f"{p.tiles} × {p.crop.seed_price}")
    seed_cost = int(seed_total)
    seeds_left = {}
    for name in dict.fromkeys([*stock, *inventory]):
        left = stock.get(name, 0) + inventory.get(name, 0)
        if left > 1e-9:
            seeds_left[name] = left
    if plantings:
        lines.append("种子支出 = " + " + ".join(terms) + f" = {seed_cost}g")

    fert_cost = 0
    fert = bonuses.fertilizer
    if fert and plantings:
        peak = _peak_tiles(plantings)
        fert_cost = peak * (fert.price or 0)
        price = f"{fert.price}g" if fert.price else "0g（无法直接购买，按 0 计）"
        lines.append(f"肥料支出 = 同时占用的最多格数 {peak} × {fert.name_zh} {price} = {fert_cost}g")
        for day, tiles in _new_fertilized_tiles(plantings).items():
            spent_by_day[day] = spent_by_day.get(day, 0) + tiles * (fert.price or 0)

    machines = dict(machines or {})
    bee_houses = machines.pop(BEE_HOUSE, 0)
    processing = None
    if (any(machines.values()) and rules_for is not None) or goods_in:
        processing = simulate_processing(harvest_events, rules_for or (lambda crop: []), machines,
                                         bonuses, tuple(goods_in))
        lines += processing.lines

    honey_result = None
    if bee_houses and honey is not None and season is not None:
        # 室外的花：从第一次可收获那天起算开着（温室里的花够不着室外的蜂房）
        blooms = [(c.crop, c.harvest_days[0]) for c in carry_in
                  if c.crop.category == "flower" and c.place != GREENHOUSE and c.harvest_days
                  and c.harvest_days[0] <= SEASON_DAYS]
        blooms += [(p.crop, p.harvests[0]) for p in plantings
                   if p.crop.category == "flower" and not p.in_greenhouse and p.harvests and p.harvests[0] <= SEASON_DAYS]
        honey_season = "spring" if honey_all_seasons else normalize_season(season)  # 姜岛的蜂房冬季也产蜜
        honey_result = simulate_honey(blooms, honey_season, bee_houses, honey, bonuses)
        lines += honey_result.lines

    bundle_value, done = 0, list(bundle_done_in)
    first_harvest = {e[1].name: e[0] for e in sorted(harvest_events, key=lambda e: e[0])}
    for r in set_aside:
        if not r.reserved:
            continue
        n = r.need
        bundle_value += r.value
        day = first_harvest.get(n.crop.name, SEASON_DAYS)
        spent_by_day[day] = spent_by_day.get(day, 0) + r.value
        done.append((n.bundle.name_en, n.crop.name_en))
        lines.append(f"献祭预留：{n.label} = {n.count} × {r.value // n.count}g = {r.value}g（不卖，从收入里扣掉）")

    tool_cost = 0
    for day, gold, label in fixed_costs:
        tool_cost += gold
        spent_by_day[day] = spent_by_day.get(day, 0) + gold
        lines.append(label)

    livestock = None
    if herds and season is not None:
        livestock = simulate_livestock(list(herds), normalize_season(season), hay_price)
        lines += livestock.lines

    finance = SeasonFinance(
        start_gold=start_gold,
        start_gold_estimated=start_gold_estimated,
        crop_revenue=int(sum(revenue_by_day.values())),
        seed_cost=seed_cost,
        fertilizer_cost=fert_cost,
        other_income=list(other_income or []),
        lines=lines,
        seeds_left=seeds_left,
        processing_net=int(round(processing.net)) if processing else 0,
        goods_out=list(processing.goods_out) if processing else [],
        honey_income=honey_result.income if honey_result else 0,
        livestock_net=livestock.net if livestock else 0,
        herds_out=_merge_herds(herds),
        bundle_value=bundle_value,
        bundle_done=tuple(dict.fromkeys(done)),
        tool_cost=tool_cost,
    )

    # 逐日现金流（只算种植收支）：找出第一次买不起种子或肥料的日子
    if start_gold is not None:
        cash = float(start_gold)
        for day in range(1, SEASON_DAYS + 1):
            cash += revenue_by_day.get(day, 0) - spent_by_day.get(day, 0)
            if processing:
                cash += processing.cash_by_day.get(day, 0)
            if honey_result:
                cash += honey_result.cash_by_day.get(day, 0)
            if livestock:
                cash += livestock.cash_by_day.get(day, 0)
            if cash < 0:
                what = "买种子、肥料和动物" if livestock else "买种子和肥料"
                what += "、升级工具" if tool_cost else ""
                finance.warnings.append(
                    f"⚠️ 第 {day} 天{what}后现金为 {int(cash)}g：只算种植收支时资金不足"
                    f"（未计入钓鱼、挖矿等其他收入，请确认届时有足够现金）")
                break
    return finance


def season_harvest_units(plantings: list[Planting], carry_in: tuple[CarryCrop, ...] = ()) -> dict[str, float]:
    """本季每种作物的期望收获个数（只算第 1-28 天的收获）。"""
    out: dict[str, float] = {}
    for crop, tiles, days in [(c.crop, c.tiles, c.harvest_days) for c in carry_in] + \
            [(p.crop, p.tiles, p.harvests) for p in plantings]:
        n = sum(1 for d in days if d <= SEASON_DAYS)
        if n:
            out[crop.name] = out.get(crop.name, 0) + n * tiles * crop.harvest_yield
    return out


def _merge_herds(herds) -> tuple[tuple[str, int], ...]:
    out: dict[str, int] = {}
    for h in herds:
        out[h.animal.name] = out.get(h.animal.name, 0) + h.count
    return tuple(out.items())


def describe_finance(f: SeasonFinance) -> list[str]:
    """资金预期的文字说明：程序计算的部分和模型估算的部分分开写。"""
    start = f"{f.start_gold}g" if f.start_gold is not None else "未知"
    out = [f"期初资金：{start}"]
    out += [f"- {line}" for line in f.lines]
    processing = f" + 加工净增 {f.processing_net}" if f.processing_net else ""
    processing += f" + 蜂蜜 {f.honey_income}" if f.honey_income else ""
    processing += f" {'+' if f.livestock_net >= 0 else '−'} 畜牧 {abs(f.livestock_net)}" if f.livestock_net else ""
    processing += f" − 献祭预留 {f.bundle_value}" if f.bundle_value else ""
    processing += f" − 工具升级 {f.tool_cost}" if f.tool_cost else ""
    net = (f"种植净收支 = 收入 {f.crop_revenue} − 种子 {f.seed_cost} − 肥料 {f.fertilizer_cost}{processing}"
           f" = {f.crop_net}g（程序计算）")
    out.append(net)
    if f.end_gold is not None:
        out.append(f"期末资金（只算种植）= {f.start_gold} + {f.crop_net} = {f.end_gold}g")
    if f.other_income:
        items = "、".join(f"{i.source} 约 {i.gold}g" for i in f.other_income)
        out.append(f"其他收入（模型估算，仅供参考）：{items}，合计约 {f.other_total}g")
        if f.end_gold_estimated is not None:
            out.append(f"期末资金（含估算）≈ {f.start_gold_estimated} + {f.crop_net} + {f.other_total}"
                       f" = {f.end_gold_estimated}g")
    return out


# ---------------------------------------------------------------------------
# 季末状态
# ---------------------------------------------------------------------------

def extract_farm_state(
    season: str,
    year: int,
    plantings: list[Planting],
    finance: SeasonFinance,
    carry_in: tuple[CarryCrop, ...] = (),
    planned_goals: list[str] | None = None,
) -> FarmState:
    """季末状态：仍在地里的跨季作物（收获日换算到下一季）、清空的作物、资金。"""
    season = normalize_season(season)
    nxt, nxt_year = following_season(season, year)
    carry, cleared = [], []

    for c in carry_in:  # 上季延续来的作物，如果还能再活一季，继续带下去
        later = tuple(d - SEASON_DAYS for d in c.harvest_days if d > SEASON_DAYS)
        if later:
            carry.append(CarryCrop(c.crop, c.tiles, c.origin, later, c.place))
    for p in plantings:
        later = tuple(d - SEASON_DAYS for d in p.harvests if d > SEASON_DAYS)
        if later:
            carry.append(CarryCrop(p.crop, p.tiles, f"{SEASON_ZH[season]}第 {p.day} 天种下", later, p.place))
        elif p.crop.name not in cleared:
            cleared.append(p.crop.name)

    return FarmState(
        season=season, year=year, next_season=nxt, next_year=nxt_year,
        gold=finance.end_gold, gold_estimated=finance.end_gold_estimated,
        carry=tuple(carry), cleared=tuple(cleared),
        planned_goals=tuple(str(g) for g in planned_goals or () if isinstance(g, (str, int, float))),
        seeds=tuple(finance.seeds_left.items()),
        goods=tuple(finance.goods_out),
        animals=finance.herds_out,
        bundle_done=finance.bundle_done,
    )


NO_PRIOR_STATE_TEXT = "（这是规划的第一季，没有上季状态）"


def render_state_for_prompt(
    state: FarmState | None, start_gold: int | None = None, seed_stock: dict[str, int] | None = None
) -> str:
    """注入下一季 Prompt【数据参考】的文本。"""
    if state is None:
        gold = f"期初资金：{start_gold}g（开局金币，来源 data/farm.json）" if start_gold is not None \
            else "期初资金：未知"
        text = f"{NO_PRIOR_STATE_TEXT}\n- {gold}"
        if seed_stock:
            seeds = "、".join(f"{name}种子 {n} 颗" for name, n in seed_stock.items())
            text += f"\n- 开局赠送：{seeds}（种植时先用这些，不花钱；请写进 plantings）"
        return text
    prev = f"第 {state.year} 年{SEASON_ZH[state.season]}"
    lines = [f"上一季：{prev}"]
    if state.gold is not None:
        lines.append(f"- 期初资金（只算种植收支，程序计算）：{state.gold}g")
    if state.gold_estimated is not None and state.gold_estimated != state.gold:
        lines.append(f"- 期初资金（含上季模型估算的钓鱼等收入，仅供参考）：约 {state.gold_estimated}g")
    if state.carry:
        lines.append("- 地里仍在生长（已付过种子钱，不要重复种植，按下列日期安排收获）：")
        lines += [f"  - {c.describe(state.next_season)}" for c in state.carry]
    else:
        lines.append("- 地里仍在生长：无（上季作物已在换季时收获或枯死）")
    if state.cleared:
        lines.append(f"- 换季时清空：{'、'.join(state.cleared)}")
    if state.animals:
        herd = "、".join(f"{name} ×{n}" for name, n in state.animals)
        lines.append(f"- 已有动物（已成年，从第 1 天起产出，程序自动计入资金）：{herd}")
    if state.goods:
        goods = "、".join(f"{g.product}约 {g.units:.1f} 件（第 {g.finish_day} 天完成）" for g in state.goods)
        lines.append(f"- 机器里还在加工（完成当天程序自动入账，完成前占着机器）：{goods}")
    if state.seeds:
        seeds = "、".join(f"{name}种子约 {n:.0f} 颗" for name, n in state.seeds if round(n) >= 1)
        if seeds:
            lines.append(f"- 手上剩余种子（程序按期望值计算）：{seeds}；在能种的季节先用这些，不花钱")
    if state.planned_goals:
        lines.append(f"- 上季计划目标（模型输出，不代表已完成）：{'；'.join(state.planned_goals)}")
    return "\n".join(lines)
