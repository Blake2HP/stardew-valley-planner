"""职业与肥料加成（数据见 data/bonuses.json，加速天数见 data/crops.json 的 speed_grow_days）。

- 农耕人 Tiller：作物售价 +10%
- 工匠 Artisan：加工品售价 +40%
- 农业学家 Agriculturist：生长速度 +10%
- 生长激素 / 高级生长激素 / 顶级生长激素：生长速度 +10% / 25% / 33%，可与农业学家叠加
- 普通 / 高级 / 顶级肥料：提高作物品质（银星 / 金星 / 铱星）的几率；每格只能施一种肥料
- 作物品质：售价 ×1.25 / ×1.5 / ×2.0；概率由耕种等级和肥料等级决定（公式见 data/bonuses.json）

价格一律逐步向下取整（与游戏一致）。用 Decimal 计算，避免 400 × 1.4 这类浮点误差导致少 1g。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal
from math import floor
from pathlib import Path
from typing import Iterable

from .data_loader import DEFAULT_DATA_DIR, SEASON_ZH, ArtisanRule, Crop


@dataclass(frozen=True)
class Profession:
    key: str
    name_zh: str
    level: int
    requires: str | None = None
    excludes: tuple[str, ...] = ()
    crop_price_multiplier: float = 1.0
    applies_to_categories: tuple[str, ...] = ()
    artisan_price_multiplier: float = 1.0
    growth_speed_percent: int = 0
    note: str = ""


@dataclass(frozen=True)
class Fertilizer:
    key: str
    name_zh: str
    name_en: str
    price: int | None
    source: str
    growth_speed_percent: int = 0  # 加速肥料
    quality_level: int = 0         # 品质肥料：普通 1 / 高级 2 / 顶级 3
    sold_from_year: int | None = None  # 从第几年春季起在商店出售（None = 商店不卖）
    sold_from_day: int = 1
    craft_level: int | None = None     # 达到这个耕种等级可以自制（None = 需要另外取得配方）


QUALITIES = ("normal", "silver", "gold", "iridium")
DEFAULT_QUALITY_MULTIPLIERS = {"normal": 1.0, "silver": 1.25, "gold": 1.5, "iridium": 2.0}
DEFAULT_QUALITY_NAMES = {"normal": "普通", "silver": "银星", "gold": "金星", "iridium": "铱星"}
MAX_FARMING_LEVEL = 14  # 10 级 + 食物增益


@dataclass(frozen=True)
class BonusRules:
    professions: dict[str, Profession]
    fertilizers: dict[str, Fertilizer]
    quality_multipliers: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_QUALITY_MULTIPLIERS))
    quality_names: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_QUALITY_NAMES))
    max_farming_level: int = MAX_FARMING_LEVEL


def load_bonus_rules(path: str | Path = DEFAULT_DATA_DIR / "bonuses.json") -> BonusRules:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    professions = {
        k: Profession(key=k, **{**v, "excludes": tuple(v.get("excludes") or ()),
                               "applies_to_categories": tuple(v.get("applies_to_categories") or ())})
        for k, v in raw["professions"].items()
    }
    fertilizers = {k: Fertilizer(key=k, **v) for k, v in raw["fertilizers"].items()}
    quality = raw.get("quality", {})
    return BonusRules(
        professions, fertilizers,
        quality_multipliers=dict(quality.get("multipliers", DEFAULT_QUALITY_MULTIPLIERS)),
        quality_names=dict(quality.get("names_zh", DEFAULT_QUALITY_NAMES)),
        max_farming_level=quality.get("max_farming_level", MAX_FARMING_LEVEL),
    )


def gold_chance(farming_level: int, fertilizer_level: int = 0) -> float:
    """金星几率 = 0.2 × (耕种等级 / 10) + 0.2 × 肥料等级 × ((耕种等级 + 2) / 12) + 0.01（wiki 公式）。"""
    return 0.2 * (farming_level / 10) + 0.2 * fertilizer_level * ((farming_level + 2) / 12) + 0.01


def quality_odds(farming_level: int, fertilizer_level: int = 0) -> dict[str, float]:
    """一次收获中第一个作物的品质分布。

    游戏依次独立掷骰：铱星（仅顶级肥料，几率 g / 2）→ 金星（g）→ 银星（min(0.75, 2g)）→ 普通；
    顶级肥料不会产出普通品质，落到普通的改为银星。
    """
    g = gold_chance(farming_level, fertilizer_level)
    s = min(0.75, 2 * g)
    iridium = min(1.0, g / 2) if fertilizer_level >= 3 else 0.0
    gold = (1 - iridium) * min(1.0, g)  # 高等级 + 顶级肥料时 g 会超过 1，掷骰必中
    if fertilizer_level >= 3:
        return {"normal": 0.0, "silver": 1 - iridium - gold, "gold": gold, "iridium": iridium}
    silver = (1 - gold) * s
    return {"normal": 1 - gold - silver, "silver": silver, "gold": gold, "iridium": 0.0}


def floor_mul(value: int, multiplier: float) -> int:
    """⌊value × multiplier⌋，用十进制精确计算。"""
    return floor(Decimal(value) * Decimal(str(multiplier)))


@dataclass(frozen=True)
class Bonuses:
    professions: tuple[Profession, ...] = ()
    fertilizer: Fertilizer | None = None
    notes: tuple[str, ...] = field(default=(), compare=False)  # 例如“已自动加入前置职业农耕人”
    farming_level: int | None = None  # 耕种等级；给出（或用了品质肥料）时才计入作物品质
    quality_multipliers: tuple[tuple[str, float], ...] = tuple(DEFAULT_QUALITY_MULTIPLIERS.items())
    quality_names: tuple[tuple[str, str], ...] = field(default=tuple(DEFAULT_QUALITY_NAMES.items()), compare=False)

    # ---- 生长 ----
    @property
    def growth_speed_percent(self) -> int:
        fert = self.fertilizer.growth_speed_percent if self.fertilizer else 0
        return fert + sum(p.growth_speed_percent for p in self.professions)

    def grow_days(self, crop: Crop) -> int:
        """加速后的生长天数：查 crops.json 的 speed_grow_days（不能按百分比直接换算）。"""
        pct = self.growth_speed_percent
        if pct == 0:
            return crop.grow_days
        try:
            return crop.speed_grow_days[str(pct)]
        except KeyError:
            raise ValueError(f"数据库中没有{crop.name}在加速 {pct}% 时的生长天数（data/crops.json speed_grow_days）") from None

    def speed_sources(self) -> str:
        parts = [f"{self.fertilizer.name_zh} {self.fertilizer.growth_speed_percent}%"] if self.fertilizer else []
        parts += [f"{p.name_zh} {p.growth_speed_percent}%" for p in self.professions if p.growth_speed_percent]
        return " + ".join(parts)

    # ---- 品质 ----
    @property
    def fertilizer_quality_level(self) -> int:
        return self.fertilizer.quality_level if self.fertilizer else 0

    @property
    def quality_active(self) -> bool:
        """是否计入作物品质：给出了耕种等级，或用了品质肥料。否则按全部普通品质计算（与旧版一致）。"""
        return self.farming_level is not None or self.fertilizer_quality_level > 0

    @property
    def effective_farming_level(self) -> int:
        """耕种等级；没给时取所选职业要求的最低等级（农耕人 5、工匠 / 农业学家 10），都没有则为 0。"""
        if self.farming_level is not None:
            return self.farming_level
        return max((p.level for p in self.professions), default=0)

    def quality_odds(self) -> dict[str, float]:
        if not self.quality_active:
            return {"normal": 1.0, "silver": 0.0, "gold": 0.0, "iridium": 0.0}
        return quality_odds(self.effective_farming_level, self.fertilizer_quality_level)

    def quality_name(self, quality: str) -> str:
        return dict(self.quality_names)[quality]

    # ---- 价格 ----
    def crop_price(self, crop: Crop, quality: str = "normal") -> int:
        """售价 = ⌊基础售价 × 品质倍率⌋，农耕人再 ⌊× 1.1⌋（与 wiki 各作物四档售价一致）。"""
        price = crop.sell_price
        mult = dict(self.quality_multipliers)[quality]
        if mult != 1.0:
            price = floor_mul(price, mult)
        for p in self.professions:
            if p.crop_price_multiplier != 1.0 and crop.category in p.applies_to_categories:
                price = floor_mul(price, p.crop_price_multiplier)
        return price

    def expected_crop_value(self, crop: Crop) -> float:
        """一次收获的期望售价：第一个作物按品质分布，额外产出（单次产量 − 1）一律普通品质。"""
        normal = self.crop_price(crop)
        if not self.quality_active:
            return normal * crop.harvest_yield
        first = sum(p * self.crop_price(crop, q) for q, p in self.quality_odds().items() if p)
        return first + (crop.harvest_yield - 1) * normal

    def artisan_unit_price(self, rule: ArtisanRule, crop: Crop) -> int | float:
        """折算到每个原料的加工品价格（咖啡：150 ÷ 5 = 30g/颗）；整除时返回整数。"""
        price, n = self.artisan_price(rule, crop), max(1, rule.input_count)
        return price // n if price % n == 0 else round(price / n, 2)

    def artisan_price(self, rule: ArtisanRule, crop: Crop) -> int:
        price = rule.price_for(crop)  # 加工品价格只看原料基础售价，不受农耕人影响
        for p in self.professions:
            if p.artisan_price_multiplier != 1.0:
                price = floor_mul(price, p.artisan_price_multiplier)
        return price

    @property
    def fertilizer_cost(self) -> int:
        """每格每季的肥料成本：肥料整季留在土里，补种不需要再施。自制或无法购买时记为 0。"""
        return (self.fertilizer.price or 0) if self.fertilizer else 0

    # ---- 描述 ----
    @property
    def is_empty(self) -> bool:
        return not self.professions and self.fertilizer is None and self.farming_level is None

    def describe(self) -> str:
        prof = "、".join(p.name_zh for p in self.professions) or "无"
        fert = self.fertilizer.name_zh if self.fertilizer else "无"
        text = f"职业：{prof}；肥料：{fert}"
        if self.quality_active:
            text += f"；耕种等级 {self.effective_farming_level}"
        return text

    def describe_quality(self) -> str:
        """例如“普通 0.0%、银星 11.3%、金星 48.2%、铱星 40.5%”。"""
        return "、".join(f"{self.quality_name(q)} {p:.1%}" for q, p in self.quality_odds().items()
                        if p or q in ("normal", "silver", "gold"))


NO_BONUSES = Bonuses()


def fertilizer_availability_note(bonuses: Bonuses, season: str, year: int, first_day: int) -> str | None:
    """规划开始用肥料时，商店是否已经在卖。买不到但能自制时给提示，都不行时给警告；在卖或商店本来不卖时返回 None。"""
    f = bonuses.fertilizer
    if f is None or f.sold_from_year is None:
        return None
    if year > f.sold_from_year or (year == f.sold_from_year and (season != "spring" or first_day >= f.sold_from_day)):
        return None
    when = f"第 {f.sold_from_year} 年起" if f.sold_from_day == 1 else f"第 {f.sold_from_year} 年春季第 {f.sold_from_day} 天起"
    level = bonuses.effective_farming_level
    if f.craft_level is not None and level >= f.craft_level:
        return f"ℹ️ {f.name_zh}{when}才在商店出售；耕种等级 {level} 已达到自制要求（{f.craft_level} 级），可以自制，费用仍按售价估算。"
    craft = f"，自制需要耕种等级 {f.craft_level}（当前按 {level} 级）" if f.craft_level is not None else ""
    return (f"⚠️ {f.name_zh}{when}才在商店出售，第 {year} 年{SEASON_ZH[season]}第 {first_day} 天买不到{craft}；"
            "资金预期里的肥料费用和生长、品质加成可能不成立。")


def _match(name: str, table: dict, aliases: dict[str, str], kind: str) -> str:
    key = name.strip().lower().replace("-", "_").replace(" ", "_")
    key = aliases.get(key, key)
    if key not in table:
        raise ValueError(f"未知{kind}: {name!r}，可选: {', '.join(table)}")
    return key


def resolve_bonuses(
    professions: Iterable[str] = (),
    fertilizer: str | None = None,
    rules: BonusRules | None = None,
    farming_level: int | None = None,
) -> Bonuses:
    """把命令行 / 调用方给的名字（英文 key、英文名或中文名）解析成 Bonuses，并检查职业冲突。

    选了 10 级职业（工匠 / 农业学家）会自动加入前置的 5 级职业农耕人；工匠和农业学家只能二选一。
    每格土地只能施一种肥料，所以加速肥料和品质肥料二选一。
    farming_level 给出时会检查是否达到所选职业的等级要求（农耕人 5 级、工匠 / 农业学家 10 级）。
    """
    rules = rules or load_bonus_rules()
    prof_alias = {p.name_zh: k for k, p in rules.professions.items()}
    fert_alias = {f.name_zh: k for k, f in rules.fertilizers.items()}
    fert_alias.update({f.name_en.lower().replace("-", "_").replace(" ", "_"): k for k, f in rules.fertilizers.items()})

    keys: list[str] = []
    for name in professions:
        for part in str(name).replace("，", ",").split(","):
            if part.strip():
                k = _match(part, rules.professions, prof_alias, "职业")
                if k not in keys:
                    keys.append(k)

    notes = []
    for k in list(keys):
        req = rules.professions[k].requires
        if req and req not in keys:
            keys.insert(0, req)
            notes.append(f"{rules.professions[k].name_zh}需要先选择{rules.professions[req].name_zh}，已自动加入")
    for k in keys:
        clash = [x for x in rules.professions[k].excludes if x in keys]
        if clash:
            names = "、".join(rules.professions[x].name_zh for x in [k, *clash])
            raise ValueError(f"职业冲突：{names}只能选一个")

    if farming_level is not None:
        if not isinstance(farming_level, int) or not 0 <= farming_level <= rules.max_farming_level:
            raise ValueError(f"耕种等级必须是 0-{rules.max_farming_level} 的整数（10 级 + 食物增益），收到 {farming_level!r}")
        too_high = [rules.professions[k] for k in keys if rules.professions[k].level > farming_level]
        if too_high:
            names = "、".join(f"{p.name_zh}（{p.level} 级）" for p in too_high)
            raise ValueError(f"耕种等级 {farming_level} 还不能选择{names}")

    fert = rules.fertilizers[_match(fertilizer, rules.fertilizers, fert_alias, "肥料")] if fertilizer else None
    return Bonuses(
        tuple(rules.professions[k] for k in keys), fert, tuple(notes), farming_level,
        tuple(rules.quality_multipliers.items()), tuple(rules.quality_names.items()),
    )
