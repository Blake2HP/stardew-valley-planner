"""基于结构化数值的确定性计算：在调用大模型之前先算好，作为 Prompt 的“事实依据”，
避免模型凭记忆编造数字。职业、肥料与作物品质见 bonuses.py。

品质与加工（sale_plan）：加工品不继承原料品质，所以同一种加工品对每个品质的原料价格相同，
而直接卖的价格随品质升高。“加工比直接卖多赚的钱”因此在普通品质最大、铱星最小。
机器不够时，按这个差额从大到小分配加工份额：低星先加工，高星直接卖。"""

from __future__ import annotations

from dataclasses import dataclass, field

from .bonuses import NO_BONUSES, QUALITIES, Bonuses
from .data_loader import NO_GUIDES_TEXT, SEASON_ZH, ArtisanRule, Crop, GameData, GiftItem, normalize_season
from .farm_state import NO_PRIOR_STATE_TEXT, alive_seasons

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
    sell_price: int = 0         # 实际售价（普通品质，已计入农耕人）
    sale_plan: "SalePlan | None" = None  # 按品质决定直接卖还是加工
    profit_with_plan: int | None = None  # 按出售策略（部分加工）的单格整季净利
    seasons_alive: int = 1      # 从本季起能连续生长几季；> 1 时收获次数和净利按完整存活期计算

    @property
    def available_days(self) -> int:
        return available_growing_days(self.plant_day) + SEASON_DAYS * (self.seasons_alive - 1)

    @property
    def profit_per_season(self) -> float:
        """平均每季净利：跨季作物占地时间更长，比较时不能直接用总额。"""
        return self.profit_per_tile / self.seasons_alive

    @property
    def span_text(self) -> str:
        return f"（跨 {self.seasons_alive} 季）" if self.seasons_alive > 1 else ""

    @property
    def profit_text(self) -> str:
        if self.seasons_alive == 1:
            return f"{self.profit_per_tile}g"
        return f"{self.profit_per_tile}g（跨 {self.seasons_alive} 季，平均每季 {self.profit_per_season:.0f}g）"

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


def seeds_to_buy(crop: Crop, harvests: int, seeds: int) -> float:
    """需要购买的种子数（期望值）。

    非再生作物每次收获后立即补种，共补种 seeds − 1 次；每次收获平均掉落 seed_drop 颗种子，
    补种时先用掉落的种子，不够的部分才买。向日葵平均每次掉 1 颗，所以只需买第一次的种子。
    """
    if crop.regrow_days or not crop.seed_drop or harvests == 0:
        return seeds
    replants = seeds - 1
    return seeds - replants * min(1.0, crop.seed_drop)


def season_profit(
    crop: Crop,
    plant_day: int = 1,
    bonuses: Bonuses = NO_BONUSES,
    value_per_harvest: float | None = None,
    season: str | None = None,
) -> tuple[int, int]:
    """返回 (收获次数, 单格整季净利润期望值)。

    - 种植日会被推迟到种子最早开售日（草莓按第 13 天起算：可用 28 − 13 + 1 = 16 天）；
    - 生长天数按加速肥料 / 农业学家查表，再生天数不变；
    - 每次收获的收入 = 品质期望售价（第一个作物按品质分布，额外产出为普通品质），计入农耕人；
      不计入品质时就是 售价 × 单次产量；
    - 肥料每格每季施一次，成本只扣一次；
    - value_per_harvest 用来替换每次收获的收入（例如按出售策略部分加工后的期望值）；
    - 传入 season 时按完整存活期计算：夏季种的玉米一直算到秋季末；不传则只算当季 28 天。
    """
    seasons = alive_seasons(crop, season) if season else 1
    days_left = available_growing_days(effective_plant_day(crop, plant_day)) + SEASON_DAYS * (seasons - 1)
    harvests, seeds = harvest_plan(bonuses.grow_days(crop), crop.regrow_days, days_left)
    value = bonuses.expected_crop_value(crop) if value_per_harvest is None else value_per_harvest
    revenue = harvests * value
    bought = seeds_to_buy(crop, harvests, seeds)
    return harvests, int(revenue - bought * crop.seed_price - bonuses.fertilizer_cost)


# ---------------------------------------------------------------------------
# 品质与加工：高星直接卖，低星加工
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class QualityOption:
    quality: str
    name: str
    units: float              # 每次收获中这一品质的期望个数（额外产出计入普通）
    direct_price: int         # 直接卖的单价（已计入品质与农耕人）
    artisan_price: int | None  # 加工后的单价（加工品不继承品质，已计入工匠）
    processed_share: float = 0.0  # 这一品质中被加工的比例（0-1）

    @property
    def gain(self) -> int | None:
        """加工比直接卖每个多赚多少（负数表示直接卖更划算）。"""
        return None if self.artisan_price is None else self.artisan_price - self.direct_price

    @property
    def value(self) -> float:
        processed = self.processed_share * (self.artisan_price or 0)
        return self.units * (processed + (1 - self.processed_share) * self.direct_price)


@dataclass(frozen=True)
class SalePlan:
    crop: Crop
    artisan_label: str | None      # 例如“罐头瓶→腌菜”
    options: tuple[QualityOption, ...]
    processing_share: float = 1.0  # 机器能加工的比例（占每次收获总个数）
    quality_active: bool = False

    @property
    def expected_direct(self) -> float:
        """每次收获全部直接卖的期望收入。"""
        return sum(o.units * o.direct_price for o in self.options)

    @property
    def expected_best(self) -> float:
        """每次收获按本策略（部分加工）的期望收入；不计机器成本和加工时间。"""
        return sum(o.value for o in self.options)

    @property
    def processed(self) -> list[QualityOption]:
        return [o for o in self.options if o.processed_share > 0]

    @property
    def sold(self) -> list[QualityOption]:
        return [o for o in self.options if o.processed_share < 1 and o.units > 0]

    def summary(self) -> str:
        """一句话策略，例如“铱星、金星直接卖；普通、银星加工成腌菜”。"""
        if self.artisan_label is None:
            return "直接卖（无加工规则）"
        product = self.artisan_label.split("→")[-1]
        gains = "、".join(f"{o.name} {o.gain:+d}g" for o in self.options if o.units > 0)
        if not self.processed:
            return f"全部直接卖（加工每个多赚：{gains}）"

        def names(opts, partial):
            return "、".join(o.name + ("（部分）" if partial(o) else "") for o in opts)

        process = names(self.processed, lambda o: o.processed_share < 1)
        if not self.sold:
            text = f"全部加工成{product}"
            if self.quality_active:
                text += f"（加工每个多赚：{gains}；机器不够时先加工低星，高星直接卖）"
            return text
        sell = names(self.sold, lambda o: o.processed_share > 0)
        return f"{sell}直接卖；{process}加工成{product}（加工每个多赚：{gains}）"


def sale_plan(
    crop: Crop, rules: list[ArtisanRule], bonuses: Bonuses = NO_BONUSES, processing_share: float = 1.0
) -> SalePlan:
    """按品质决定每个作物直接卖还是加工。

    - 加工品不继承品质：加工单价对所有品质相同（取最贵的加工方式）；
    - 每个品质的“加工收益差” = 加工单价 − 直接卖单价，普通最大、铱星最小；
    - 机器只能加工每次收获的 processing_share（0-1）时，按收益差从大到小分配（贪心即最优），
      收益差 ≤ 0 的品质永远直接卖。
    """
    if not 0 <= processing_share <= 1:
        raise ValueError(f"加工比例必须在 0-1 之间，收到 {processing_share}")
    best = max(rules, key=lambda r: bonuses.artisan_price(r, crop), default=None)
    artisan = bonuses.artisan_price(best, crop) if best else None
    odds = bonuses.quality_odds()
    extra = crop.harvest_yield - 1  # 额外产出一律普通品质
    options = [QualityOption(q, bonuses.quality_name(q), odds[q] + (extra if q == "normal" else 0),
                             bonuses.crop_price(crop, q), artisan) for q in QUALITIES]

    capacity = processing_share * crop.harvest_yield
    allocated = {}
    for o in sorted(options, key=lambda o: o.gain if o.gain is not None else -1, reverse=True):
        if o.gain is None or o.gain <= 0 or o.units <= 0 or capacity <= 1e-12:
            continue
        take = min(o.units, capacity)
        allocated[o.quality] = take / o.units
        capacity -= take
    options = [QualityOption(o.quality, o.name, o.units, o.direct_price, o.artisan_price,
                             allocated.get(o.quality, 0.0)) for o in options]
    label = f"{best.machine}→{best.product}" if best else None
    return SalePlan(crop, label, tuple(options), processing_share, bonuses.quality_active)


def rank_crops(
    data: GameData,
    season: str,
    top_n: int = 6,
    bonuses: Bonuses = NO_BONUSES,
    year: int | None = None,
    processing_share: float = 1.0,
) -> list[CropProfit]:
    """本季作物按平均每季净利（直接卖）排序；传入 year 时过滤掉当年买不到种子的作物（如第 1 年的大蒜）。

    跨季作物按完整存活期计算，并且始终列出（即使排在 top_n 之后），否则模型看不到这个选项。
    """
    results = []
    for crop in data.crops_in_season(season, year):
        harvests, profit = season_profit(crop, bonuses=bonuses, season=season)
        rules = data.artisan_rules_for(crop)
        best = max(rules, key=lambda r: bonuses.artisan_price(r, crop), default=None)
        plan = sale_plan(crop, rules, bonuses, processing_share)
        _, planned = season_profit(crop, bonuses=bonuses, value_per_harvest=plan.expected_best, season=season)
        results.append(CropProfit(
            crop=crop,
            harvests=harvests,
            profit_per_tile=profit,
            best_artisan=f"{best.machine}→{best.product}" if best else None,
            artisan_unit_price=bonuses.artisan_price(best, crop) if best else None,
            plant_day=effective_plant_day(crop),
            grow_days=bonuses.grow_days(crop),
            sell_price=bonuses.crop_price(crop),
            sale_plan=plan,
            profit_with_plan=planned if best else None,
            seasons_alive=alive_seasons(crop, season),
        ))
    results.sort(key=lambda r: r.profit_per_season, reverse=True)
    return results[:top_n] + [r for r in results[top_n:] if r.seasons_alive > 1]


def build_data_context(
    data: GameData,
    season: str,
    guide_excerpts: str = NO_GUIDES_TEXT,
    bonuses: Bonuses = NO_BONUSES,
    year: int | None = None,
    carryover: str = NO_PRIOR_STATE_TEXT,
    processing_share: float = 1.0,
) -> dict[str, str]:
    """把结构化数值整理成可直接塞进 Prompt 的文本片段；攻略片段由 search_guides() 检索后传入。"""
    crop_lines = ["| 作物 | 种子价 | 售价 | 生长/再生(天) | 最早种植日 | 整季收获次数 | 单格整季净利 | 最佳加工(单价) | 种子来源 "
                  "| 按品质出售策略 | 按策略单格净利 |",
                  "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rank_crops(data, season, bonuses=bonuses, year=year, processing_share=processing_share):
        c = r.crop
        regrow = f"{r.grow_days}/{c.regrow_days}" if c.regrow_days else f"{r.grow_days}/-"
        artisan = f"{r.best_artisan}({r.artisan_unit_price}g)" if r.best_artisan else "-"
        crop_lines.append(
            f"| {c.name} | {c.seed_price}g | {r.sell_price}g | {regrow} | 第 {r.plant_day} 天 | {r.harvests}{r.span_text} | "
            f"{r.profit_text} | {artisan} | {c.seed_source} | {r.sale_plan.summary()} | "
            + (f"{r.profit_with_plan}g |" if r.profit_with_plan is not None else "- |")
        )

    zh = {c.name_en: c.name for c in data.crops}
    artisan_lines = [
        f"- {r.machine}："
        + ("、".join(zh.get(n, n) for n in r.input_crops) if r.input_crops else r.input_category)
        + f" → {r.product}，"
        + (f"固定 {r.fixed_price}g" if r.fixed_price is not None else f"售价 ×{r.multiplier} + {r.bonus}")
        + f"，耗时约 {r.processing_days} 天"
        + ("（该作物不再适用同一机器的类别规则）" if r.input_crops else "")
        for r in data.artisan_rules
    ]

    event_lines = [f"- 第 {e.day} 天【{e.name}】：{e.tip}" for e in data.events_in_season(season)]

    notes = [crop_note(r.crop) for r in rank_crops(data, season, bonuses=bonuses, year=year)]
    notes = [n for n in notes if n]
    if notes:
        crop_lines += ["", "作物备注（程序计算）："] + [f"- {n}" for n in notes]

    return {
        "crop_table": "\n".join(crop_lines),
        "artisan_rules": "\n".join(artisan_lines),
        "calendar_events": "\n".join(event_lines) or "（无）",
        "guide_excerpts": guide_excerpts or NO_GUIDES_TEXT,
        "bonuses": describe_bonuses(bonuses),
        "carryover": carryover,
        "npc_birthdays": describe_birthdays(data, season),
    }


def crop_note(crop: Crop) -> str:
    """种子掉落、副产物等不在表格列里的信息。"""
    parts = []
    if crop.seed_drop:
        parts.append(f"每次收获平均掉落 {crop.seed_drop:g} 颗种子，补种可直接用（净利已按此少算种子钱）")
    for b in crop.byproducts:
        value = f"售价 {b.sell_price}g" if b.sell_price else "不计入收入"
        parts.append(f"收获时 {b.chance:.0%} 几率掉 1 个{b.name}（{b.note}；{value}）")
    return f"{crop.name}：" + "；".join(parts) if parts else ""


LOVED_POINTS, LIKED_POINTS, BIRTHDAY_MULTIPLIER = 80, 45, 8  # wiki Friendship


def _items_text(items: tuple[GiftItem, ...], data: GameData, season: str) -> str:
    """物品列表；本季能种的作物注明出来，方便模型安排种植和预留。"""
    crops = {c.name_en: c for c in data.crops}
    out = []
    for i in items:
        crop = crops.get(i.name_en)
        if crop is None:
            out.append(i.name)
        else:
            when = "本季可种" if season in crop.seasons else f"{'/'.join(SEASON_ZH[s] for s in crop.seasons)}作物"
            out.append(f"{i.name}（{when}）")
    return "、".join(out) or "无专属物品"


def describe_birthdays(data: GameData, season: str) -> str:
    """本季过生日的 NPC 及其最爱 / 喜欢的物品（data/npcs.json），注入 Prompt【数据参考】。"""
    season = normalize_season(season)
    npc_data = data.npc_data
    birthdays = npc_data.birthdays_in_season(season)
    if not birthdays:
        covered = "、".join(n.name for n in npc_data.npcs) or "无"
        return f"（本季没有收录的 NPC 过生日；目前收录：{covered}）"
    lines = [f"生日送最爱的礼物 = {LOVED_POINTS} × {BIRTHDAY_MULTIPLIER} = {LOVED_POINTS * BIRTHDAY_MULTIPLIER} 好感度，"
             f"喜欢的 = {LIKED_POINTS} × {BIRTHDAY_MULTIPLIER} = {LIKED_POINTS * BIRTHDAY_MULTIPLIER}；生日礼物不占每周 2 次的送礼次数。"]
    universal = [i for i in npc_data.universal_loves]
    for n in birthdays:
        hated = npc_data.hated_universal(n)
        uni = "、".join(i.name for i in universal if i not in hated)
        except_text = f"；讨厌{'、'.join(i.name for i in hated)}（通用最爱的例外）" if hated else ""
        lines.append(
            f"- 第 {n.birthday_day} 天【{n.name}生日】最爱：{_items_text(n.loves, data, season)}；"
            f"通用最爱：{uni}{except_text}；喜欢：{_items_text(n.likes, data, season)}"
            + (f"（另有通用喜欢：{n.universal_likes_note}）" if n.universal_likes_note else "")
        )
    return "\n".join(lines)


def describe_bonuses(bonuses: Bonuses) -> str:
    """Prompt 中的加成说明：表格里的数字已经计入了哪些加成。"""
    if bonuses.is_empty:
        return "无（表中为基础数值）"
    lines = [bonuses.describe() + "（表中售价、生长天数、净利已计入）"]
    if bonuses.quality_active:
        lines.append(f"- 作物品质（耕种等级 {bonuses.effective_farming_level}"
                     f"{'，' + bonuses.fertilizer.name_zh if bonuses.fertilizer_quality_level else ''}）："
                     f"{bonuses.describe_quality()}；表中“售价”为普通品质，“单格整季净利”为按品质分布的期望值；"
                     "额外产出一律普通品质；加工品不继承品质")
    for p in bonuses.professions:
        lines.append(f"- {p.name_zh}：{p.note}")
    if bonuses.fertilizer:
        f = bonuses.fertilizer
        cost = f"每格每季 {f.price}g" if f.price else "无法直接购买，成本按 0 计"
        effect = (f"生长速度 +{f.growth_speed_percent}%，不缩短再生时间" if f.growth_speed_percent
                  else f"提高作物品质（肥料等级 {f.quality_level}）")
        lines.append(f"- {f.name_zh}：{effect}；{cost}；来源：{f.source}")
    return "\n".join(lines)
