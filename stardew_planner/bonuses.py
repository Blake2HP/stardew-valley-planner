"""职业与肥料加成（数据见 data/bonuses.json，加速天数见 data/crops.json 的 speed_grow_days）。

- 农耕人 Tiller：作物售价 +10%
- 工匠 Artisan：加工品售价 +40%
- 农业学家 Agriculturist：生长速度 +10%
- 生长激素 / 高级生长激素 / 顶级生长激素：生长速度 +10% / 25% / 33%，可与农业学家叠加

价格一律逐步向下取整（与游戏一致）。用 Decimal 计算，避免 400 × 1.4 这类浮点误差导致少 1g。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal
from math import floor
from pathlib import Path
from typing import Iterable

from .data_loader import DEFAULT_DATA_DIR, ArtisanRule, Crop


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
    growth_speed_percent: int
    price: int | None
    source: str


@dataclass(frozen=True)
class BonusRules:
    professions: dict[str, Profession]
    fertilizers: dict[str, Fertilizer]


def load_bonus_rules(path: str | Path = DEFAULT_DATA_DIR / "bonuses.json") -> BonusRules:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    professions = {
        k: Profession(key=k, **{**v, "excludes": tuple(v.get("excludes") or ()),
                               "applies_to_categories": tuple(v.get("applies_to_categories") or ())})
        for k, v in raw["professions"].items()
    }
    fertilizers = {k: Fertilizer(key=k, **v) for k, v in raw["fertilizers"].items()}
    return BonusRules(professions, fertilizers)


def floor_mul(value: int, multiplier: float) -> int:
    """⌊value × multiplier⌋，用十进制精确计算。"""
    return floor(Decimal(value) * Decimal(str(multiplier)))


@dataclass(frozen=True)
class Bonuses:
    professions: tuple[Profession, ...] = ()
    fertilizer: Fertilizer | None = None
    notes: tuple[str, ...] = field(default=(), compare=False)  # 例如“已自动加入前置职业农耕人”

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

    # ---- 价格 ----
    def crop_price(self, crop: Crop) -> int:
        price = crop.sell_price
        for p in self.professions:
            if p.crop_price_multiplier != 1.0 and crop.category in p.applies_to_categories:
                price = floor_mul(price, p.crop_price_multiplier)
        return price

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
        return not self.professions and self.fertilizer is None

    def describe(self) -> str:
        prof = "、".join(p.name_zh for p in self.professions) or "无"
        fert = self.fertilizer.name_zh if self.fertilizer else "无"
        return f"职业：{prof}；肥料：{fert}"


NO_BONUSES = Bonuses()


def _match(name: str, table: dict, aliases: dict[str, str], kind: str) -> str:
    key = name.strip().lower().replace("-", "_").replace(" ", "_")
    key = aliases.get(key, key)
    if key not in table:
        raise ValueError(f"未知{kind}: {name!r}，可选: {', '.join(table)}")
    return key


def resolve_bonuses(
    professions: Iterable[str] = (), fertilizer: str | None = None, rules: BonusRules | None = None
) -> Bonuses:
    """把命令行 / 调用方给的名字（英文 key、英文名或中文名）解析成 Bonuses，并检查职业冲突。

    选了 10 级职业（工匠 / 农业学家）会自动加入前置的 5 级职业农耕人；工匠和农业学家只能二选一。
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

    fert = rules.fertilizers[_match(fertilizer, rules.fertilizers, fert_alias, "肥料")] if fertilizer else None
    return Bonuses(tuple(rules.professions[k] for k in keys), fert, tuple(notes))
