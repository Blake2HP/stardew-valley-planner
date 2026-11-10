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
            hit = next(((seg.find(n), n) for n in crop.names if n in seg), None)
            if hit is None or crop in found:
                continue
            pos, name = hit
            verb = _governing_verb(seg, pos, pos + len(name))
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
    greenhouse: bool = False,
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
        # 温室已修好且日程写明“温室”：不查季节、开售日和成熟日（种子可以提前买好，作物不会因换季枯死）
        in_greenhouse = greenhouse and "温室" in daily_goals[day]
        for crop in _planted_crops(daily_goals[day], data.crops):
            if year == 1 and crop.requires_bus and (season in crop.seasons or in_greenhouse):
                warnings.append(SeasonWarning(
                    day, crop.name,
                    f"{crop.name}种子在{crop.seed_source}出售，要先修好巴士才能买到；第 1 年如果还没修好，这次种植无法进行",
                    f"种子来源 = {crop.seed_source}",
                ))
            if season not in crop.seasons and not in_greenhouse:
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
            elif day < crop.earliest_available_day and not in_greenhouse:
                warnings.append(SeasonWarning(
                    day, crop.name,
                    f"{crop.name}种子要到第 {crop.earliest_available_day} 天才能买到（{crop.seed_source}）",
                    f"种植日 {day} < 最早开售日 {crop.earliest_available_day}",
                ))
            elif in_greenhouse or survives_season_change(crop, season):
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


def check_plantings_match(
    daily_goals: dict[int, str], plantings, data: GameData, already: set[tuple[int, str]] = frozenset()
) -> list[SeasonWarning]:
    """日程和种植清单互相核对：资金只按清单计算，两边对不上时资金预期没有意义。

    连续几天目标相同时算作一段（“第 1-3 天：种土豆”只需要清单里有其中一天）；
    already 是日程校验已经报过的 (日期, 作物)，不重复警告。
    """
    listed = {(p.day, p.crop.name) for p in plantings}
    runs: list[list[int]] = []
    for day in sorted(daily_goals):
        if runs and runs[-1][-1] == day - 1 and daily_goals[runs[-1][-1]] == daily_goals[day]:
            runs[-1].append(day)
        else:
            runs.append([day])

    warnings = []
    for run in runs:
        goal = daily_goals[run[0]]
        for crop in _planted_crops(goal, data.crops):
            if any((d, crop.name) in listed or (d, crop.name) in already for d in run):
                continue
            warnings.append(SeasonWarning(run[0], crop.name, f"日程安排种{crop.name}，但种植清单没有这一条，资金预期没有计入",
                                          f"目标：{goal}"))
    for p in plantings:
        goal = daily_goals.get(p.day, "")
        if not any(n in goal for n in p.crop.names):
            warnings.append(SeasonWarning(p.day, p.crop.name, f"种植清单里的{p.crop.name}在当天日程没有提到",
                                          f"目标：{goal or '无'}"))
    return sorted(warnings, key=lambda w: w.day)


def withering_crops(data: GameData, season: str, year: int | None = None) -> list[Crop]:
    """本季作物中，换季后会枯死的那些（传入 year 时只列当年能种的）。"""
    return [c for c in data.crops_in_season(season, year) if not survives_season_change(c, season)]


def cleanup_goal(keep: Iterable[str] = ()) -> str:
    """第 28 天的清理目标；有跨季作物时注明保留，避免把会继续生长的作物也清掉。"""
    keep = list(dict.fromkeys(keep))
    return f"{CLEANUP_GOAL}（保留{'、'.join(keep)}）" if keep else CLEANUP_GOAL


KEEP_WORDS = ("保留", "留下", "别动", "不动", "不清理", "不要清理", "别清理")


def _marked_as_kept(goal: str, crop: str) -> bool:
    """同一子句里同时有作物名和“保留”类说法才算；“收获玉米；清理农场”只是提到了玉米。"""
    return any(crop in seg and any(w in seg for w in KEEP_WORDS) for seg in _SEGMENT_SPLIT.split(goal))


def enforce_season_end(daily_goals: dict[int, str], keep: Iterable[str] = ()) -> tuple[dict[int, str], bool]:
    """确保第 28 天包含“清理农场/停止种植”。返回 (新日程, 是否做了修改)。

    keep 是会跨入下一季继续生长的作物名，写进清理目标里（例如“清理农场/停止种植（保留玉米）”）。
    目标里已经有清理任务、但没说明保留哪些跨季作物时，也会补上，避免玩家把它们铲掉。
    """
    goals = dict(daily_goals)
    current = goals.get(SEASON_DAYS, "").strip()
    if "清理农场" in current and "停止种植" in current:
        missing = [k for k in dict.fromkeys(keep) if not _marked_as_kept(current, k)]
        if not missing:
            return goals, False
        goals[SEASON_DAYS] = f"{current}（保留{'、'.join(missing)}）"
        return goals, True
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
    names = {name for n in nd.npcs for i in (*n.loves, *n.likes, *n.dislikes, *n.hates) for name in i.names}
    names |= {name for i in nd.universal_loves for name in i.names}
    names |= {name for g in (nd.universal_likes, nd.universal_neutrals, nd.universal_dislikes, nd.universal_hates)
              for i in g.items for name in i.names}
    names |= {n for c in data.crops for n in c.names} | {r.product for r in data.artisan_rules}
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


# 作物 / 加工品在 wiki 类别说明里的叫法
_CATEGORY_WORDS = {"vegetable": "Vegetables", "fruit": "Fruit", "flower": "Flowers", "artisan": "Artisan Goods"}
LOVE, LIKE, NEUTRAL, DISLIKE, HATE = "love", "like", "neutral", "dislike", "hate"


def _category_of(item: str, data: GameData) -> tuple[str, str] | None:
    """作物或加工品的 (英文名, 类别)；其他物品返回 None。"""
    for c in data.crops:
        if item in c.names:
            return c.name_en, c.category
    for r in data.artisan_rules:
        if item == r.product:
            return r.product_en, "artisan"
    return None


def _bullet_covers(bullet: str, word: str, name_en: str) -> bool:
    """“All Vegetables (except Hops, Tea Leaves, & Wheat)”这类说明是否包括这件物品。"""
    if not bullet.startswith(f"All {word}"):
        return False
    rest = bullet[len(f"All {word}"):]
    return not (rest.lstrip().startswith("(except") and name_en in rest) and not rest[:1].isalpha()


def gift_taste(npc: NPC, item: str, data: GameData) -> str | None:
    """NPC 对一件礼物的态度：love / like / neutral / dislike / hate；数据里查不到返回 None。

    先看 NPC 页面逐项列出的物品，再看页面上的类别说明（作物和加工品），最后看通用列表（wiki Friendship）。
    """
    nd = data.npc_data
    has = lambda items: any(item in i.names for i in items)  # noqa: E731
    if has(npc.hates):
        return HATE
    if has(npc.dislikes):
        return DISLIKE
    if has(nd.loved_items(npc)):
        return LOVE
    if has(npc.likes):
        return LIKE
    found = _category_of(item, data)
    if found:
        name_en, category = found
        word = _CATEGORY_WORDS.get(category)
        if word:
            for key, taste in (("hates", HATE), ("dislikes", DISLIKE), ("loves", LOVE), ("likes", LIKE)):
                if any(_bullet_covers(b, word, name_en) for b in npc.gift_notes.get(key, ())):
                    return taste
    for group, taste in ((nd.universal_hates, HATE), (nd.universal_dislikes, DISLIKE),
                         (nd.universal_neutrals, NEUTRAL), (nd.universal_likes, LIKE)):
        if has(group.items):
            return taste
    if found and found[1] == "seed":
        return DISLIKE  # wiki Friendship：All Seeds 是通用不喜欢（咖啡豆）
    if found and _CATEGORY_WORDS.get(found[1]) in ("Vegetables", "Flowers", "Artisan Goods"):
        word = _CATEGORY_WORDS[found[1]]
        likes_note = " ".join(npc.gift_notes.get("likes", ()))
        excluded = "All Universal Likes (except" in likes_note and word in likes_note.split("All Universal Likes", 1)[1]
        return NEUTRAL if excluded else LIKE   # 通用喜欢：所有蔬菜、花、工匠物品
    if found:
        return NEUTRAL  # 普通水果不在通用喜欢里
    return None


_TASTE_TEXT = {DISLIKE: ("不喜欢", "−20"), HATE: ("讨厌", "−40")}


def check_gifts(daily_goals: dict[int, str], data: GameData, season: str) -> list[GiftWarning]:
    """核对送礼任务：礼物最好是该 NPC 最爱 / 喜欢的，不能是不喜欢 / 讨厌的；可以结婚的村民生日当天须有送礼任务。"""
    season = normalize_season(season)
    nd = data.npc_data
    warnings: list[GiftWarning] = []
    gifted_on: dict[str, set[int]] = {}

    for day in sorted(daily_goals):
        for npc, items in gift_tasks(daily_goals[day], data):
            gifted_on.setdefault(npc.name, set()).add(day)
            prefs = (f"最爱：{'、'.join(i.name for i in nd.loved_items(npc))}；"
                     f"喜欢：{'、'.join(i.name for i in npc.likes) or '无专属物品'}")
            if not items:
                warnings.append(GiftWarning(day, npc.name, f"给{npc.name}的送礼任务没有写明礼物，或礼物不在数据库中",
                                            prefs))
            for item in items:
                taste = gift_taste(npc, item, data)
                if taste in _TASTE_TEXT:
                    word, points = _TASTE_TEXT[taste]
                    warnings.append(GiftWarning(day, npc.name, f"{npc.name}{word}{item}（{points} 好感度），不要送", prefs))
                elif taste == NEUTRAL:
                    warnings.append(GiftWarning(day, npc.name, f"{item}对{npc.name}只是中立礼物（+20 好感度），"
                                                "换成最爱或喜欢的更划算", prefs))
                elif taste is None:
                    note = f"；通用喜欢：{npc.universal_likes_note}" if npc.universal_likes_note else ""
                    warnings.append(GiftWarning(day, npc.name, f"{item}不在 npcs.json 记录的{npc.name}喜好中",
                                                prefs + note))

    for npc in nd.birthdays_in_season(season):
        if npc.marriage and npc.birthday_day not in gifted_on.get(npc.name, set()):
            warnings.append(GiftWarning(
                npc.birthday_day, npc.name,
                f"{npc.name}生日当天没有送礼任务",
                f"生日送最爱 = 80 × 8 = 640 好感度；最爱：{'、'.join(i.name for i in npc.loves)}",
            ))
    return sorted(warnings, key=lambda w: w.day)
