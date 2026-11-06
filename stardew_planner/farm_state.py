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
from .data_loader import SEASON_ZH, Crop, GameData, normalize_season

SEASON_DAYS = 28
SEASON_ORDER = ["spring", "summer", "fall", "winter"]


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


def harvest_days(crop: Crop, plant_day: int, season: str, bonuses: Bonuses = NO_BONUSES) -> list[int]:
    """一次播种的全部收获日（从本季第 1 天起算，可跨季）。

    首次收获日 = 种植日 + 生长天数；可再生作物之后每隔再生天数收获一次，
    直到作物能存活的最后一天（28 × 可存活季数）。成熟前就枯死则返回空列表。
    """
    limit = SEASON_DAYS * alive_seasons(crop, season)
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


@dataclass(frozen=True)
class CarryCrop:
    """季末仍在地里、会在下一季继续生长的作物。"""
    crop: Crop
    tiles: int
    origin: str                     # 例如“夏季第 1 天种下”
    harvest_days: tuple[int, ...]   # 从“下一季第 1 天”起算的收获日

    def describe(self, season: str) -> str:
        days = "、".join(str(d) for d in self.harvest_days if d <= SEASON_DAYS)
        later = [d for d in self.harvest_days if d > SEASON_DAYS]
        text = f"{self.crop.name} ×{self.tiles} 格（{self.origin}）：{SEASON_ZH[season]}第 {days} 天可收获" if days \
            else f"{self.crop.name} ×{self.tiles} 格（{self.origin}）：本季无收获"
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
    warnings: list[str] = field(default_factory=list)    # 现金不足等

    @property
    def crop_net(self) -> int:
        return self.crop_revenue - self.seed_cost - self.fertilizer_cost

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

    @property
    def carried_names(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(c.crop.name for c in self.carry))


# ---------------------------------------------------------------------------
# 种植清单
# ---------------------------------------------------------------------------

def resolve_plantings(
    raw: list[dict], data: GameData, season: str, year: int, bonuses: Bonuses = NO_BONUSES
) -> tuple[list[Planting], list[str]]:
    """把模型输出的 plantings 转成 Planting；不合法的条目剔除并给出警告（不计入资金）。"""
    season = normalize_season(season)
    by_name = {c.name: c for c in data.crops} | {c.name_en.lower(): c for c in data.crops}
    plantings, warnings = [], []
    for item in raw:
        name, day, tiles = item["crop"], item["day"], item["tiles"]
        crop = by_name.get(name) or by_name.get(name.lower())
        where = f"种植清单第 {day} 天的{name}"
        if crop is None:
            warnings.append(f"⚠️ {where}不在数据库中，未计入资金")
        elif season not in crop.seasons:
            warnings.append(f"⚠️ {where}不是{SEASON_ZH[season]}作物，未计入资金")
        elif year < crop.available_year:
            warnings.append(f"⚠️ {where}：种子第 {crop.available_year} 年起才出售，未计入资金")
        elif day < crop.earliest_available_day:
            warnings.append(f"⚠️ {where}：种子第 {crop.earliest_available_day} 天才开售，未计入资金")
        else:
            plantings.append(Planting(day, crop, tiles, tuple(harvest_days(crop, day, season, bonuses))))
    return sorted(plantings, key=lambda p: p.day), warnings


# ---------------------------------------------------------------------------
# 资金
# ---------------------------------------------------------------------------

def _peak_tiles(plantings: list[Planting]) -> int:
    """本季新播种同时占用的最多格数：非再生作物收获当天就空出来，可再生作物一直占到季末。"""
    peak = 0
    for day in range(1, SEASON_DAYS + 1):
        used = 0
        for p in plantings:
            if p.day > day:
                continue
            if p.crop.regrow_days or not p.harvests:
                used += p.tiles
            elif day < p.harvests[0]:
                used += p.tiles
        peak = max(peak, used)
    return peak


def compute_finance(
    plantings: list[Planting],
    carry_in: tuple[CarryCrop, ...] = (),
    *,
    start_gold: int | None = None,
    start_gold_estimated: int | None = None,
    other_income: list[OtherIncome] | None = None,
    bonuses: Bonuses = NO_BONUSES,
) -> SeasonFinance:
    """本季种植收支（只算本季内发生的收获；跨季收获计入下一季）。"""
    revenue_by_day: dict[int, float] = {}
    lines = []

    def add_harvests(crop: Crop, tiles: int, days, label: str):
        days = [d for d in days if d <= SEASON_DAYS]
        if not days:
            return
        price = bonuses.crop_price(crop)
        each = tiles * crop.harvest_yield * price
        for d in days:
            revenue_by_day[d] = revenue_by_day.get(d, 0) + each
        yield_text = "" if crop.harvest_yield == 1 else f" × 单次产量 {crop.harvest_yield}"
        lines.append(f"{label}：{len(days)} 次 × {tiles} 格{yield_text} × {price}g = {len(days) * each:g}g"
                     f"（第 {'、'.join(map(str, days))} 天）")

    for c in carry_in:
        add_harvests(c.crop, c.tiles, c.harvest_days, f"{c.crop.name}（上季延续）")
    for p in plantings:
        add_harvests(p.crop, p.tiles, p.harvests, f"{p.crop.name}（第 {p.day} 天种）")

    seed_cost = sum(p.tiles * p.crop.seed_price for p in plantings)
    if plantings:
        lines.append("种子支出 = " + " + ".join(f"{p.tiles} × {p.crop.seed_price}" for p in plantings)
                     + f" = {seed_cost}g")

    fert_cost = 0
    fert = bonuses.fertilizer
    if fert and plantings:
        peak = _peak_tiles(plantings)
        fert_cost = peak * (fert.price or 0)
        price = f"{fert.price}g" if fert.price else "0g（无法直接购买，按 0 计）"
        lines.append(f"肥料支出 = 同时占用的最多格数 {peak} × {fert.name_zh} {price} = {fert_cost}g")

    finance = SeasonFinance(
        start_gold=start_gold,
        start_gold_estimated=start_gold_estimated,
        crop_revenue=int(sum(revenue_by_day.values())),
        seed_cost=seed_cost,
        fertilizer_cost=fert_cost,
        other_income=list(other_income or []),
        lines=lines,
    )

    # 逐日现金流（只算种植收支）：找出第一次买不起种子的日子
    if start_gold is not None:
        cash = float(start_gold)
        spent_by_day: dict[int, int] = {}
        for p in plantings:
            spent_by_day[p.day] = spent_by_day.get(p.day, 0) + p.tiles * p.crop.seed_price
        for day in range(1, SEASON_DAYS + 1):
            cash += revenue_by_day.get(day, 0) - spent_by_day.get(day, 0)
            if cash < 0:
                finance.warnings.append(
                    f"⚠️ 第 {day} 天买种子后现金为 {int(cash)}g：只算种植收支时资金不足"
                    f"（未计入钓鱼、挖矿等其他收入，请确认届时有足够现金）")
                break
    return finance


def describe_finance(f: SeasonFinance) -> list[str]:
    """资金预期的文字说明：程序计算的部分和模型估算的部分分开写。"""
    start = f"{f.start_gold}g" if f.start_gold is not None else "未知"
    out = [f"期初资金：{start}"]
    out += [f"- {line}" for line in f.lines]
    net = (f"种植净收支 = 收入 {f.crop_revenue} − 种子 {f.seed_cost} − 肥料 {f.fertilizer_cost}"
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
            carry.append(CarryCrop(c.crop, c.tiles, c.origin, later))
    for p in plantings:
        later = tuple(d - SEASON_DAYS for d in p.harvests if d > SEASON_DAYS)
        if later:
            carry.append(CarryCrop(p.crop, p.tiles, f"{SEASON_ZH[season]}第 {p.day} 天种下", later))
        elif p.crop.name not in cleared:
            cleared.append(p.crop.name)

    return FarmState(
        season=season, year=year, next_season=nxt, next_year=nxt_year,
        gold=finance.end_gold, gold_estimated=finance.end_gold_estimated,
        carry=tuple(carry), cleared=tuple(cleared),
        planned_goals=tuple(str(g) for g in planned_goals or () if isinstance(g, (str, int, float))),
    )


NO_PRIOR_STATE_TEXT = "（这是规划的第一季，没有上季状态）"


def render_state_for_prompt(state: FarmState | None, start_gold: int | None = None) -> str:
    """注入下一季 Prompt【数据参考】的文本。"""
    if state is None:
        gold = f"期初资金：{start_gold}g（开局金币，来源 data/farm.json）" if start_gold is not None \
            else "期初资金：未知"
        return f"{NO_PRIOR_STATE_TEXT}\n- {gold}"
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
    if state.planned_goals:
        lines.append(f"- 上季计划目标（模型输出，不代表已完成）：{'；'.join(state.planned_goals)}")
    return "\n".join(lines)
