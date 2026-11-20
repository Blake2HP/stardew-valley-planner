"""Steam 成就目标（数据见 data/achievements.json，wiki Achievements / Friendship）。

用户用 --achievements 指定想完成的成就，--achievement-progress 补充已有进度（不给时从零算）：
- 程序算出本季的“缺口”，写进【数据参考】的【🏆 成就规划目标】：
  - 混合栽培：本季能买到种子、还没运够的作物各要直接出货几个；
  - 单一栽培：本季选一种作物，要直接出货几个（还差的个数平均分到今年剩下的季节）；
  - 友谊类（新朋友……万人迷）：本季要新结交几位村民（还差的人数平均分到今年剩下的季节）；
  - 赚钱类（新手……千万富翁）：还差多少钱，只提示；
  - 其他成就（钓鱼、烹饪、博物馆……）：程序没有相应数据，只把条件写进 Prompt 作为长期目标，不校验；
- 生成后校验：出货类按预计直接出货个数、友谊类按每周送礼和本季新结交人数，问题以“⚠️ 成就校验：”开头，
  交给 --revise-rounds 的自动修正；
- 跨季连续规划时，进度按每季的预计出货、送礼和收入累计到下一季。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from .data_loader import DEFAULT_DATA_DIR

PREFIX = "⚠️ 成就校验："
KINDS = ("earnings", "friendship", "ship_each_crop", "ship_one_crop", "info")


@dataclass(frozen=True)
class Achievement:
    key: str
    name: str          # 官方中文名（zh wiki）
    name_en: str
    kind: str          # 见 KINDS
    target: int = 0    # 金币 / 人数 / 每种作物的个数 / 一种作物的个数；info 为 0
    description: str = ""
    aliases: tuple[str, ...] = ()
    crops: tuple[str, ...] = ()   # 出货类：作物英文名
    hearts: int = 0               # friendship：几颗心
    points_per_heart: int = 250
    category: str = ""            # 网页上分组用

    @property
    def names(self) -> tuple[str, ...]:
        return (self.name, *self.aliases)

    @property
    def checked(self) -> bool:
        """程序能按数值校验（info 只写进 Prompt）。"""
        return self.kind != "info"


@dataclass(frozen=True)
class FriendshipRules:
    talk_daily: int = 20
    gift_love: int = 80
    gift_like: int = 45
    gifts_per_week: int = 2
    full_week_bonus: int = 10
    bouquet_cap_hearts: int = 8   # 可以结婚的村民送花束之前最多几颗心


@dataclass(frozen=True)
class AchievementRules:
    achievements: tuple[Achievement, ...] = ()
    friendship: FriendshipRules = FriendshipRules()

    def find(self, name: str) -> Achievement | None:
        key = str(name).strip()
        return next((a for a in self.achievements
                     if key in a.names or key.lower() in (a.name_en.lower(), a.key)), None)


@dataclass(frozen=True)
class Goal:
    """用户指定的一个成就；label 是用户写的名字（如“多作种植”），提示里沿用。"""
    achievement: Achievement
    label: str


@dataclass(frozen=True)
class Progress:
    """成就进度：用户给出的已有进度 + 本次跨季规划中前几季的累计。"""
    shipped: dict = field(default_factory=dict)   # 作物中文名 → 已运送个数
    friends: dict = field(default_factory=dict)   # 友谊类成就 key → 已经达到要求心数的人数
    befriended: tuple[str, ...] = ()              # 本次规划前几季已经开始送礼结交的村民
    earned: int = 0                               # 累计赚取的金币
    seasons_done: int = 0                         # 本次规划已经完成的季节数


def load_achievement_rules(path: str | Path = DEFAULT_DATA_DIR / "achievements.json") -> AchievementRules | None:
    """文件不存在时为 None（--achievements 会报错说明没有数据）。"""
    path = Path(path)
    if not path.exists():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    items = []
    for a in raw.get("achievements", []):
        if a["kind"] not in KINDS:
            raise ValueError(f"achievements.json：未知的成就类型 {a['kind']}（{a['name']}）")
        items.append(Achievement(
            a["key"], a["name"], a["name_en"], a["kind"], int(a.get("target", 0)), a.get("description", ""),
            tuple(a.get("aliases", ())), tuple(a.get("crops", ())), int(a.get("hearts", 0)),
            int(a.get("points_per_heart", 250)), a.get("category", ""),
        ))
    f = raw.get("friendship") or {}
    return AchievementRules(tuple(items), FriendshipRules(
        int(f.get("talk_daily", 20)), int(f.get("gift_love", 80)), int(f.get("gift_like", 45)),
        int(f.get("gifts_per_week", 2)), int(f.get("full_week_bonus", 10)), int(f.get("bouquet_cap_hearts", 8))))


def _split(spec) -> list[str]:
    if spec is None:
        return []
    parts = re.split(r"[,，、;；]", spec) if isinstance(spec, str) else [str(x) for x in spec]
    return [p.strip() for p in parts if p and p.strip()]


def resolve_goals(spec, rules: AchievementRules | None) -> list[Goal]:
    """"混合栽培,万人迷" 或 ["万人迷"] → [Goal]；写错时报 ValueError，列出可选的成就。"""
    names = _split(spec)
    if not names:
        return []
    if rules is None or not rules.achievements:
        raise ValueError("没有成就数据（data/achievements.json），不能使用 --achievements")
    goals, seen = [], set()
    for name in names:
        a = rules.find(name)
        if a is None:
            known = "、".join(x.name for x in rules.achievements)
            raise ValueError(f"未知的成就：{name}（可选：{known}；也可以写英文名）")
        if a.key not in seen:
            seen.add(a.key)
            goals.append(Goal(a, name))
    return goals


def parse_progress(spec, rules: AchievementRules | None, crops) -> Progress:
    """已有进度："防风草=15,土豆=7,万人迷=12,千万富翁=50000"（或 dict）。

    作物名 → 已运送个数（出货类）；友谊类成就名 → 已经达到该成就要求心数的人数；赚钱类成就名 → 已累计赚取的金币。
    """
    if not spec:
        return Progress()
    if isinstance(spec, dict):
        pairs = [(str(k), v) for k, v in spec.items()]
    else:
        pairs = []
        for part in _split(spec):
            m = re.fullmatch(r"(.+?)\s*[=:：]\s*(\d+)", part)
            if not m:
                raise ValueError(f"成就进度写法应为“防风草=15,万人迷=12”，收到：{part}")
            pairs.append((m.group(1), m.group(2)))
    by_name = {}
    for c in crops:
        for n in (c.name, c.name_en, c.name_en.lower()):
            by_name[n] = c
    shipped, friends, earned = {}, {}, 0
    for name, value in pairs:
        try:
            n = int(value)
        except (TypeError, ValueError):
            raise ValueError(f"成就进度里 {name} 的数值必须是整数，收到 {value!r}") from None
        if n < 0:
            raise ValueError(f"成就进度里 {name} 不能为负数")
        name = name.strip()
        crop = by_name.get(name) or by_name.get(name.lower())
        a = rules.find(name) if rules else None
        if crop is not None:
            shipped[crop.name] = n
        elif a is not None and a.kind == "friendship":
            friends[a.key] = n
        elif a is not None and a.kind == "earnings":
            earned = max(earned, n)
        else:
            raise ValueError(f"成就进度里的 {name} 既不是作物，也不是友谊或赚钱类成就")
    return Progress(shipped, friends, (), earned)


# ---------------------------------------------------------------------------
# 本季缺口
# ---------------------------------------------------------------------------

DIRECT_SOURCES = ("皮埃尔杂货店", "复活节商店")  # 本季能直接买到种子的渠道；沙漠绿洲、旅行货车只算可选
SEASONS_PER_YEAR = 4


def _ceil_div(a: int, b: int) -> int:
    return -(-a // b)


@dataclass(frozen=True)
class CropGap:
    crop: object       # data_loader.Crop
    need: int          # 本季还要直接出货的个数
    source: str = ""


@dataclass(frozen=True)
class FriendGap:
    goal: Goal
    left: int          # 还差几个人（扣掉已有进度和本次规划前几季结交的）
    per_season: int    # 本季要新结交的人数 = 还差的人数平均分到今年剩下的季节


@dataclass
class SeasonTargets:
    goals: list[Goal]
    progress: Progress
    seasons_left: int = SEASONS_PER_YEAR
    crops: list[CropGap] = field(default_factory=list)      # 混合栽培：本季必须直接出货的作物
    optional: list[CropGap] = field(default_factory=list)   # 本季能种、但种子要去特殊渠道买的作物
    later: list[str] = field(default_factory=list)          # 本季作物，但今年还买不到种子
    shipped_done: list[str] = field(default_factory=list)   # 已经运够的作物
    mono: list[CropGap] = field(default_factory=list)       # 单一栽培：本季可选的作物，选一种出货 need 个
    mono_done: str = ""                                     # 单一栽培已经运够的作物
    friends: list[FriendGap] = field(default_factory=list)
    earnings: list[tuple[Goal, int]] = field(default_factory=list)  # (成就, 还差多少金币)
    info: list[Goal] = field(default_factory=list)          # 只写进 Prompt 的长期目标

    def title(self, g: Goal) -> str:
        """“多作种植（混合栽培：运送 15 份每种农作物）”；用户写的就是官方名时不重复。"""
        a = g.achievement
        return f"{g.label}（{a.description}）" if g.label == a.name else f"{g.label}（{a.name}：{a.description}）"

    def goal(self, kind: str) -> Goal | None:
        return next((g for g in self.goals if g.achievement.kind == kind), None)

    @property
    def friend_gap(self) -> FriendGap | None:
        """本季要求最高的友谊类成就（新结交人数最多；一样多时选还差得多的）。"""
        open_gaps = [f for f in self.friends if f.left > 0]
        return max(open_gaps, key=lambda f: (f.per_season, f.left), default=None)


def season_targets(goals: list[Goal], progress: Progress, crops, season: str, year: int) -> SeasonTargets:
    t = SeasonTargets(list(goals), progress, max(1, SEASONS_PER_YEAR - progress.seasons_done))
    by_en = {c.name_en: c for c in crops}
    for g in goals:
        a = g.achievement
        if a.kind == "ship_each_crop":
            for name_en in a.crops:
                crop = by_en.get(name_en)
                if crop is None or season not in crop.seasons:
                    continue
                need = a.target - int(progress.shipped.get(crop.name, 0))
                if need <= 0:
                    t.shipped_done.append(crop.name)
                elif crop.available_year > year:
                    t.later.append(f"{crop.name}（第 {crop.available_year} 年起）")
                elif crop.seed_source.startswith(DIRECT_SOURCES):
                    t.crops.append(CropGap(crop, need, crop.seed_source))
                else:
                    t.optional.append(CropGap(crop, need, crop.seed_source.split("（")[0]))
        elif a.kind == "ship_one_crop":
            eligible = [by_en[n] for n in a.crops if n in by_en]
            done = [c for c in eligible if progress.shipped.get(c.name, 0) >= a.target]
            if done:
                t.mono_done = done[0].name
                continue
            for crop in eligible:
                if season in crop.seasons and crop.available_year <= year:
                    left = a.target - int(progress.shipped.get(crop.name, 0))
                    t.mono.append(CropGap(crop, _ceil_div(left, t.seasons_left), crop.seed_source.split("（")[0]))
            t.mono.sort(key=lambda c: (c.need, c.crop.name))
        elif a.kind == "friendship":
            left = max(0, a.target - progress.friends.get(a.key, 0) - len(progress.befriended))
            t.friends.append(FriendGap(g, left, _ceil_div(left, t.seasons_left)))
        elif a.kind == "earnings":
            t.earnings.append((g, max(0, a.target - progress.earned)))
        else:
            t.info.append(g)
    return t


def _mono_text(t: SeasonTargets) -> str:
    """单一栽培的候选作物：按要出货的个数分组，“75 个：防风草、土豆……”。"""
    groups: dict[int, list[str]] = {}
    for c in t.mono:
        groups.setdefault(c.need, []).append(c.crop.name)
    return "；".join(f"{need} 个：{'、'.join(names)}" for need, names in sorted(groups.items()))


def describe_targets(t: SeasonTargets, rules: AchievementRules) -> str:
    """【数据参考】里的【🏆 成就规划目标】一节。"""
    lines = ["以下是为了达成玩家指定的成就，本季**必须**完成的硬性指标（程序根据成就条件和已有进度计算）："]
    if g := t.goal("ship_each_crop"):
        a = g.achievement
        head = f"- {t.title(g)}："
        if t.crops:
            need = "、".join(f"{c.crop.name} {c.need} 个" for c in t.crops)
            lines.append(f"{head}本季必须种下并收获、直接出货：{need}。按预计收获个数计算（多次收获的作物不用种 "
                         f"{a.target} 格）；程序会先留出这些数量直接出货，剩下的才交给机器加工；留给收集包的不算出货，要多种一些。")
        else:
            lines.append(f"{head}本季没有必须种的作物。")
        if t.optional:
            opt = "、".join(f"{c.crop.name} {c.need} 个（{c.source}）" for c in t.optional)
            lines.append(f"  - 可选（种子要去特殊渠道买，本季不强制）：{opt}")
        if t.later:
            lines.append(f"  - 今年还买不到种子：{'、'.join(t.later)}")
        if t.shipped_done:
            lines.append(f"  - 已经运够：{'、'.join(t.shipped_done)}")
    if g := t.goal("ship_one_crop"):
        a = g.achievement
        if t.mono_done:
            lines.append(f"- {t.title(g)}：{t.mono_done}已经运够 {a.target} 个，本季不用再安排。")
        elif t.mono:
            lines.append(f"- {t.title(g)}：本季集中种一种作物，直接出货至少这么多（还差的个数平均分到今年剩下的 "
                         f"{t.seasons_left} 个季节，以后几季要继续种同一种）：{_mono_text(t)}。程序会先留出这些数量直接出货。")
        else:
            lines.append(f"- {t.title(g)}：本季没有可以计入的作物，本季不用安排。")
    if gap := t.friend_gap:
        f = rules.friendship
        a = gap.goal.achievement
        points = a.hearts * a.points_per_heart
        others = [x for x in t.friends if x is not gap]
        lines.append(
            f"- {t.title(gap.goal)}：还差 {gap.left} 人，今年还剩 {t.seasons_left} 个季节，"
            f"本季至少要给 {gap.per_season} 位新的村民送礼（之前季节结交过的村民不算新的）。"
            f"每颗心 {a.points_per_heart} 好感度，{a.hearts} 颗心 = {a.hearts} × {a.points_per_heart} = {points} 点；"
            f"每天对话 +{f.talk_daily}，送最爱的礼物 +{f.gift_love}、喜欢的 +{f.gift_like}，每周最多送 "
            f"{f.gifts_per_week} 次礼物，一周送满 {f.gifts_per_week} 次周日再 +{f.full_week_bonus}。"
            "每周（第 1-7、8-14、15-21、22-28 天）至少安排一天送礼，写成“送{村民}{礼物}”，礼物选该村民最爱或喜欢的。")
        for x in others:
            lines.append(f"  - 同时计入：{t.title(x.goal)}，还差 {x.left} 人" if x.left else
                         f"  - {t.title(x.goal)}：按已有进度已经达成")
        if any(x.goal.achievement.hearts > f.bouquet_cap_hearts for x in t.friends):
            lines.append(f"  - 可以结婚的村民送花束之前最多 {f.bouquet_cap_hearts} 颗心，要到更高的心数先选不能结婚的村民。")
    elif t.friends:
        names = "、".join(x.goal.label for x in t.friends)
        lines.append(f"- {names}：按已有进度已经达成，本季不用再安排。")
    for g, gap in t.earnings:
        lines.append(f"- {t.title(g)}：还差 {gap:,}g（已赚取 {t.progress.earned:,}g）。"
                     "出货、加工品和其他收入都算；请在策略里说明本季怎么提高收入（程序不强制校验金额）。")
    if t.info:
        names = "；".join(t.title(g) for g in t.info)
        lines.append(f"- 长期目标（程序没有相关数据，只供参考、不校验）：{names}。请结合玩法风格，在日程里安排推进这些成就的活动。")
    lines.append("请在 macro_strategy.achievement_plan 里专门写一段成就实现思路，在 achievement_milestones 里列出关键节点，"
                 "并在 daily_goals 里显式安排相关动作（例如“种 15 颗土豆（混合栽培）”“送海莉黄水仙（万人迷）”）。")
    return "\n".join(lines)


def advance(progress: Progress, units: dict, gifted, earned: int) -> Progress:
    """一季结束后的进度：预计出货计入已运送、新送礼的村民计入已结交、收入计入已赚取。"""
    shipped = dict(progress.shipped)
    for name, n in units.items():
        shipped[name] = shipped.get(name, 0) + int(n)
    befriended = progress.befriended + tuple(sorted(set(gifted) - set(progress.befriended)))
    return Progress(shipped, dict(progress.friends), befriended, progress.earned + max(0, int(earned)),
                    progress.seasons_done + 1)


def summary_lines(t: SeasonTargets) -> list[str]:
    """攻略里“🏆 成就规划”一节的程序计算部分（Markdown 列表）。"""
    out = []
    if g := t.goal("ship_each_crop"):
        need = "、".join(f"{c.crop.name} {c.need} 个" for c in t.crops) or "无"
        extra = []
        if t.optional:
            extra.append("可选：" + "、".join(f"{c.crop.name}（{c.source}）" for c in t.optional))
        if t.shipped_done:
            extra.append("已运够：" + "、".join(t.shipped_done))
        out.append(f"- **{t.title(g)}**：本季要收获并直接出货 {need}" + (f"（{'；'.join(extra)}）" if extra else ""))
    if g := t.goal("ship_one_crop"):
        text = (f"{t.mono_done}已经运够" if t.mono_done else
                f"本季选一种作物直接出货（{_mono_text(t)}）" if t.mono else "本季没有可以计入的作物")
        out.append(f"- **{t.title(g)}**：{text}")
    for x in t.friends:
        text = (f"还差 {x.left} 人，本季至少给 {x.per_season} 位新的村民送礼，每周至少送一次" if x.left
                else "按已有进度已经达成")
        out.append(f"- **{t.title(x.goal)}**：{text}")
    for g, gap in t.earnings:
        out.append(f"- **{t.title(g)}**：还差 {gap:,}g（已赚取 {t.progress.earned:,}g）")
    if t.info:
        out.append("- **长期目标**（程序不校验）：" + "；".join(t.title(g) for g in t.info))
    return out


# ---------------------------------------------------------------------------
# 成就校验（问题交给 --revise-rounds 的自动修正）
# ---------------------------------------------------------------------------

WEEKS = ((1, 7), (8, 14), (15, 21), (22, 28))


def _num(x: float) -> str:
    return f"{x:.1f}".rstrip("0").rstrip(".")


def _mono_choice(t: SeasonTargets, harvested: dict) -> CropGap | None:
    """单一栽培：本季预计收获最多的候选作物（模型选中的那一种）。"""
    planted = [c for c in t.mono if harvested.get(c.crop.name, 0) > 0]
    return max(planted, key=lambda c: harvested[c.crop.name], default=None)


def keep_for_shipping(t: SeasonTargets, bundle_units: dict, harvested: dict | None = None) -> dict[str, float]:
    """加工前先留下的个数：出货类成就本季要直接出货的，加上同一作物留给收集包的（一件出货同时计入两个成就）。"""
    keep = {c.crop.name: float(c.need) for c in t.crops + t.optional}
    if choice := _mono_choice(t, harvested or {}):
        keep[choice.crop.name] = max(keep.get(choice.crop.name, 0), choice.need)
    return {name: n + bundle_units.get(name, 0) for name, n in keep.items()}


def check_achievements(t: SeasonTargets, units: dict, planted: set, gifts_by_day: dict, macro: dict,
                       milestones: list, harvested: dict | None = None, bundle_units: dict | None = None) -> list[str]:
    """units：本季每种作物预计直接出货的个数（收获 − 加工 − 留给收集包，见 farm_state.shipped_units）；
    harvested / bundle_units：预计收获和留给收集包的个数，只用来说明差在哪里；planted：本季种了的作物名；
    gifts_by_day：{日: [送礼的村民名]}（season_rules.gift_tasks 识别）。"""
    harvested = harvested or {}
    problems = []

    def shortfall(name: str, got: float) -> str:
        total = harvested.get(name, got)
        kept = (bundle_units or {}).get(name, 0)
        why = []
        if kept:
            why.append(f"{_num(kept)} 个留给收集包")
        if total - kept - got > 1e-6:
            why.append(f"{_num(total - kept - got)} 个交给机器加工")
        return f"（预计收获 {_num(total)} 个，其中 {'、'.join(why)}）" if why else ""

    if g := t.goal("ship_each_crop"):
        for c in t.crops:
            got = units.get(c.crop.name, 0)
            if got >= c.need:
                continue
            if c.crop.name not in planted:
                problems.append(f"{PREFIX}目标包含“{g.label}”，但本季没有安排种植{c.crop.name}"
                                f"（要收获并出货 {c.need} 个）。请修改种植清单！")
            else:
                problems.append(f"{PREFIX}目标包含“{g.label}”，但本季安排种植的{c.crop.name}预计只能直接出货 "
                                f"{_num(got)} 个{shortfall(c.crop.name, got)}，不足 {c.need} 个。请修改种植清单！")
    if (g := t.goal("ship_one_crop")) and t.mono and not t.mono_done:
        if not any(units.get(c.crop.name, 0) >= c.need for c in t.mono):
            choice = _mono_choice(t, harvested)
            if choice is None:
                problems.append(f"{PREFIX}目标包含“{g.label}”，但本季没有种可以计入的作物"
                                f"（选一种集中种，直接出货至少 {t.mono[0].need} 个）。请修改种植清单！")
            else:
                got = units.get(choice.crop.name, 0)
                problems.append(f"{PREFIX}目标包含“{g.label}”，但本季出货最多的{choice.crop.name}预计只能直接出货 "
                                f"{_num(got)} 个{shortfall(choice.crop.name, got)}，不足 {choice.need} 个。"
                                "请集中种一种作物！")
    if gap := t.friend_gap:
        g = gap.goal
        empty = [f"第 {a}-{b} 天" for a, b in WEEKS if not any(gifts_by_day.get(d) for d in range(a, b + 1))]
        if empty:
            problems.append(f"{PREFIX}目标包含“{g.label}”，但{'、'.join(empty)}没有安排送礼。"
                            "请每周至少安排一天“送{村民}{礼物}”！")
        new = sorted({n for names in gifts_by_day.values() for n in names} - set(t.progress.befriended))
        if len(new) < gap.per_season:
            who = f"（{'、'.join(new)}）" if new else ""
            problems.append(f"{PREFIX}目标包含“{g.label}”，但本季只给 {len(new)} 位新的村民安排了送礼{who}，"
                            f"至少需要 {gap.per_season} 位。请增加送礼对象！")
    if not str(macro.get("achievement_plan") or "").strip():
        problems.append(f"{PREFIX}macro_strategy 里没有 achievement_plan，请专门写一段成就实现思路")
    if not milestones:
        problems.append(f"{PREFIX}没有给出 achievement_milestones，请列出本季为了成就达成的关键节点")
    return problems


def list_achievements(rules: AchievementRules | None) -> str:
    """--list-achievements 的输出：按类别分组，✔ 表示程序会算缺口（赚钱类只提示，不校验金额）。"""
    if rules is None:
        return "没有成就数据（data/achievements.json）\n"
    groups: dict[str, list[Achievement]] = {}
    for a in rules.achievements:
        groups.setdefault(a.category or "其他", []).append(a)
    lines = ["全部成就（✔ = 程序按数据算本季缺口，出货和友谊类还会校验；其他只写进 Prompt 作为长期目标）：", ""]
    for category, items in groups.items():
        lines.append(f"【{category}】")
        lines += [f"  {'✔' if a.checked else ' '} {a.name}（{a.name_en}）：{a.description}" for a in items]
        lines.append("")
    return "\n".join(lines)
