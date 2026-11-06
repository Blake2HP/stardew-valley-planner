"""跨季规则校验（确定性代码，不依赖大模型）。

规则：
1. 成熟日 = 种植日 + 生长天数，必须 ≤ 28；否则换季时作物枯死（下一季仍可生长的作物除外）。
2. 第 28 天（换季前一天）的目标必须包含“清理农场/停止种植”。
3. 送礼：日程里送给 NPC 的礼物必须在 data/npcs.json 记录的最爱 / 喜欢中；
   当季过生日的 NPC，生日当天必须有送礼任务。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from .bonuses import NO_BONUSES, Bonuses
from .data_loader import NPC, SEASON_ZH, Crop, GameData, normalize_season

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


@dataclass(frozen=True)
class GiftWarning:
    day: int
    npc: str
    message: str
    detail: str

    def __str__(self) -> str:
        return f"第 {self.day} 天：{self.message}（{self.detail}）"


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
    daily_goals: dict[int, str],
    data: GameData,
    season: str,
    bonuses: Bonuses = NO_BONUSES,
    year: int | None = None,
    carried: Iterable[str] = (),
) -> list[SeasonWarning]:
    """检查日程里的种植安排。

    传入 year 时还会检查种子在当年是否已开售；carried 是从上季延续、仍在地里的作物名，
    本季再“种”它们时会提示（已付过种子钱，通常不需要重新种植）。
    """
    carried = set(carried)
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
            elif crop.name in carried:
                warnings.append(SeasonWarning(
                    day, crop.name,
                    f"{crop.name}已从上季延续在地里，无需重新种植（如果是扩种可以忽略）",
                    f"{crop.name}可种季节 = {'/'.join(SEASON_ZH[s] for s in crop.seasons)}，上季种下后仍在生长",
                ))
            elif year is not None and year < crop.available_year:
                warnings.append(SeasonWarning(
                    day, crop.name,
                    f"{crop.name}种子第 {crop.available_year} 年起才出售（{crop.seed_source}），第 {year} 年买不到",
                    f"当前年份 {year} < 开售年份 {crop.available_year}",
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


def withering_crops(data: GameData, season: str, year: int | None = None) -> list[Crop]:
    """本季作物中，换季后会枯死的那些（传入 year 时只列当年能种的）。"""
    return [c for c in data.crops_in_season(season, year) if not survives_season_change(c, season)]


def cleanup_goal(keep: Iterable[str] = ()) -> str:
    """第 28 天的清理目标；有跨季作物时注明保留，避免把会继续生长的作物也清掉。"""
    keep = list(dict.fromkeys(keep))
    return f"{CLEANUP_GOAL}（保留{'、'.join(keep)}）" if keep else CLEANUP_GOAL


def enforce_season_end(daily_goals: dict[int, str], keep: Iterable[str] = ()) -> tuple[dict[int, str], bool]:
    """确保第 28 天包含“清理农场/停止种植”。返回 (新日程, 是否做了修改)。

    keep 是会跨入下一季继续生长的作物名，写进清理目标里（例如“清理农场/停止种植（保留玉米）”）。
    """
    goals = dict(daily_goals)
    current = goals.get(SEASON_DAYS, "").strip()
    if "清理农场" in current and "停止种植" in current:
        return goals, False
    goal = cleanup_goal(keep)
    goals[SEASON_DAYS] = f"{current}；{goal}" if current else goal
    return goals, True


# ---------------------------------------------------------------------------
# 送礼校验
# ---------------------------------------------------------------------------

GIFT_VERBS = ("送", "赠")


def _gift_vocabulary(data: GameData) -> list[str]:
    """能识别为礼物的物品名：npcs.json 里的所有物品 + 作物 + 加工品。按长度倒序，先匹配长的（“南瓜汤”优先于“南瓜”）。"""
    nd = data.npc_data
    names = {name for n in nd.npcs for i in (*n.loves, *n.likes) for name in i.names}
    names |= {name for i in nd.universal_loves for name in i.names}
    names |= {c.name for c in data.crops} | {r.product for r in data.artisan_rules}
    return sorted(names, key=len, reverse=True)


def _find_items(seg: str, vocabulary: list[str]) -> list[str]:
    found = []
    for name in vocabulary:
        pos = seg.find(name)
        while pos != -1:
            found.append((pos, name))
            seg = seg[:pos] + "□" * len(name) + seg[pos + len(name):]  # 遮住，避免“南瓜汤”里再匹配出“南瓜”
            pos = seg.find(name)
    return [name for _, name in sorted(found)]


def gift_tasks(goal: str, data: GameData) -> list[tuple[NPC, list[str]]]:
    """找出某天目标里的送礼任务：[(NPC, 识别出的礼物名)]。

    子句里同时出现 NPC 名字和“送 / 赠”才算送礼；“不要送”“别送”等否定说法不算；括号内视为说明。
    """
    vocabulary = _gift_vocabulary(data)
    text = _NOT_NEGATION.sub(lambda m: "□" * len(m.group()), _PARENTHETICAL.sub("", goal))
    tasks = []
    for seg in _SEGMENT_SPLIT.split(text):
        npcs = [n for n in data.npc_data.npcs if n.name in seg]
        verbs = [i for i, ch in enumerate(seg) if ch in GIFT_VERBS]
        if not npcs or not verbs or all(_is_negated(seg, v) for v in verbs):
            continue
        masked = seg
        for n in npcs:
            masked = masked.replace(n.name, "□" * len(n.name))
        items = _find_items(masked, vocabulary)
        tasks += [(n, items) for n in npcs]
    return tasks


def check_gifts(daily_goals: dict[int, str], data: GameData, season: str) -> list[GiftWarning]:
    """核对送礼任务：礼物须在该 NPC 的最爱 / 喜欢中；当季生日当天须有送礼任务。"""
    season = normalize_season(season)
    nd = data.npc_data
    warnings: list[GiftWarning] = []
    gifted_on: dict[str, set[int]] = {}

    for day in sorted(daily_goals):
        for npc, items in gift_tasks(daily_goals[day], data):
            gifted_on.setdefault(npc.name, set()).add(day)
            loved = {name for i in nd.loved_items(npc) for name in i.names}
            liked = {name for i in npc.likes for name in i.names}
            hated = {name for i in nd.hated_universal(npc) for name in i.names}
            prefs = (f"最爱：{'、'.join(i.name for i in nd.loved_items(npc))}；"
                     f"喜欢：{'、'.join(i.name for i in npc.likes) or '无专属物品'}")
            if not items:
                warnings.append(GiftWarning(day, npc.name, f"给{npc.name}的送礼任务没有写明礼物，或礼物不在数据库中",
                                            prefs))
            for item in items:
                if item in hated:
                    warnings.append(GiftWarning(day, npc.name, f"{npc.name}讨厌{item}（通用最爱的例外），不要送",
                                                prefs))
                elif item not in loved and item not in liked:
                    note = f"；通用喜欢未逐项收录：{npc.universal_likes_note}" if npc.universal_likes_note else ""
                    warnings.append(GiftWarning(day, npc.name, f"{item}不在 npcs.json 记录的{npc.name}最爱 / 喜欢中",
                                                prefs + note))

    for npc in nd.birthdays_in_season(season):
        if npc.birthday_day not in gifted_on.get(npc.name, set()):
            warnings.append(GiftWarning(
                npc.birthday_day, npc.name,
                f"{npc.name}生日当天没有送礼任务",
                f"生日送最爱 = 80 × 8 = 640 好感度；最爱：{'、'.join(i.name for i in npc.loves)}",
            ))
    return sorted(warnings, key=lambda w: w.day)
