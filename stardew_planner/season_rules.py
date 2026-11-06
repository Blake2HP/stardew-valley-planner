"""跨季规则校验（确定性代码，不依赖大模型）。

规则：
1. 成熟日 = 种植日 + 生长天数，必须 ≤ 28；否则换季时作物枯死（下一季仍可生长的作物除外）。
2. 第 28 天（换季前一天）的目标必须包含“清理农场/停止种植”。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .bonuses import NO_BONUSES, Bonuses
from .data_loader import SEASON_ZH, Crop, GameData, normalize_season

SEASON_DAYS = 28
SEASON_ORDER = ["spring", "summer", "fall", "winter"]
CLEANUP_GOAL = "清理农场/停止种植"
PLANT_KEYWORDS = ("种", "播")
NEGATIONS = ("不", "别", "勿", "停止", "无需", "禁止", "避免")
NEGATION_WINDOW = 4  # 种植动词前多少个字内出现否定词，视为否定
_NOT_NEGATION = re.compile(r"别忘了?|别忘记|不要忘记?|不忘")  # “别忘了种土豆”是提醒，不是否定
_SEGMENT_SPLIT = re.compile(r"[，,；;。：:！!？?]")  # 只按句读切分；“和/、/并”连接的是同一动作的多个对象
_PARENTHETICAL = re.compile(r"（[^）]*）|\([^)]*\)")


@dataclass(frozen=True)
class SeasonWarning:
    day: int
    crop: str
    message: str
    formula: str

    def __str__(self) -> str:
        return f"第 {self.day} 天：{self.message}（{self.formula}）"


def next_season(season: str) -> str:
    i = SEASON_ORDER.index(normalize_season(season))
    return SEASON_ORDER[(i + 1) % 4]


def survives_season_change(crop: Crop, season: str) -> bool:
    return next_season(season) in crop.seasons


def last_safe_plant_day(crop: Crop, bonuses: Bonuses = NO_BONUSES) -> int:
    """最晚种植日 = 28 − 生长天数（生长天数已计入加速肥料 / 农业学家）。"""
    return SEASON_DAYS - bonuses.grow_days(crop)


def _governing_verb(seg: str, crop_pos: int, crop_end: int) -> int | None:
    """找支配该作物的种植动词位置。

    中文动词一般在宾语前（“补种土豆”），取作物前最近的“种/播”；
    只有作物后紧跟“种子”时（“买土豆种子并种下”）才往后找。
    “种子”是名词，先替换掉，避免把“买种子”当成种植。
    """
    masked = seg.replace("种子", "□□")
    before = [i for i, ch in enumerate(masked[:crop_pos]) if ch in PLANT_KEYWORDS]
    if before:
        return before[-1]
    if seg.startswith("种子", crop_end):
        after = [i for i, ch in enumerate(masked) if i >= crop_end and ch in PLANT_KEYWORDS]
        if after:
            return after[0]
    return None


def _is_negated(seg: str, verb_pos: int) -> bool:
    window = seg[max(0, verb_pos - NEGATION_WINDOW):verb_pos]
    return any(n in window for n in NEGATIONS)


def _planted_crops(goal: str, crops: list[Crop]) -> list[Crop]:
    """找出某天目标里真正被安排种植的作物。

    - 括号内视为解释说明，先去掉（如“不再补种（补种土豆会在第 32 天成熟）”）；
    - 按句读切成子句，避免“收获花椰菜，补种土豆”把花椰菜误判为种植；
    - 种植动词前 4 个字内有否定词（不/别/停止…）不算种植，如“不再补种土豆”“种完土豆后别再种花椰菜”。
    """
    text = _NOT_NEGATION.sub(lambda m: "□" * len(m.group()), _PARENTHETICAL.sub("", goal))
    found = []
    for seg in _SEGMENT_SPLIT.split(text):
        for crop in crops:
            pos = seg.find(crop.name)
            if pos == -1 or crop in found:
                continue
            verb = _governing_verb(seg, pos, pos + len(crop.name))
            if verb is not None and not _is_negated(seg, verb):
                found.append(crop)
    return found


def check_plantings(
    daily_goals: dict[int, str], data: GameData, season: str, bonuses: Bonuses = NO_BONUSES
) -> list[SeasonWarning]:
    season = normalize_season(season)
    nxt = next_season(season)
    warnings = []
    for day in sorted(daily_goals):
        for crop in _planted_crops(daily_goals[day], data.crops):
            if season not in crop.seasons:
                allowed = "/".join(SEASON_ZH[s] for s in crop.seasons)
                warnings.append(SeasonWarning(
                    day, crop.name,
                    f"{crop.name}不是{SEASON_ZH[season]}作物，种下会立即枯萎",
                    f"{crop.name}可种季节 = {allowed}",
                ))
            elif day < crop.earliest_available_day:
                warnings.append(SeasonWarning(
                    day, crop.name,
                    f"{crop.name}种子要到第 {crop.earliest_available_day} 天才能买到（{crop.seed_source}）",
                    f"种植日 {day} < 最早开售日 {crop.earliest_available_day}",
                ))
            elif survives_season_change(crop, season):
                continue
            elif day + bonuses.grow_days(crop) > SEASON_DAYS:
                grow = bonuses.grow_days(crop)
                speed = f"（已计入 {bonuses.speed_sources()}）" if bonuses.growth_speed_percent else ""
                warnings.append(SeasonWarning(
                    day, crop.name,
                    f"{crop.name}来不及成熟，进入{SEASON_ZH[nxt]}时会枯死",
                    f"成熟日 = 种植日 {day} + 生长 {grow} 天{speed} = {day + grow} > {SEASON_DAYS}；"
                    f"最晚种植日 = {SEASON_DAYS} − {grow} = {last_safe_plant_day(crop, bonuses)}",
                ))
    return warnings


def withering_crops(data: GameData, season: str) -> list[Crop]:
    """本季作物中，换季后会枯死的那些。"""
    return [c for c in data.crops_in_season(season) if not survives_season_change(c, season)]


def enforce_season_end(daily_goals: dict[int, str]) -> tuple[dict[int, str], bool]:
    """确保第 28 天包含“清理农场/停止种植”。返回 (新日程, 是否做了修改)。"""
    goals = dict(daily_goals)
    current = goals.get(SEASON_DAYS, "").strip()
    if "清理农场" in current and "停止种植" in current:
        return goals, False
    goals[SEASON_DAYS] = f"{current}；{CLEANUP_GOAL}" if current else CLEANUP_GOAL
    return goals, True
