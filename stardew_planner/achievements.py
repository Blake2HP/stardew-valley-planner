"""Steam 成就目标（数据见 data/achievements.json，wiki Achievements / Friendship）。

用户用 --achievements 指定想完成的成就，--achievement-progress 补充已有进度（不给时从零算）：
- 程序算出本季的“缺口”（混合栽培：本季能买到种子、还没运够的作物各要收获几个；万人迷：本季要新结交几位村民；
  百万 / 千万富翁：还差多少钱），写进【数据参考】的【🏆 成就规划目标】；
- 生成后校验：混合栽培按预计收获个数、万人迷按每周送礼和本季新结交人数，问题以“⚠️ 成就校验：”开头，
  交给 --revise-rounds 的自动修正；
- 跨季连续规划时，进度按每季的预计收获、送礼和收入累计到下一季。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from .data_loader import DEFAULT_DATA_DIR

PREFIX = "⚠️ 成就校验："
KINDS = ("earnings", "ship_each_crop", "friendship")


@dataclass(frozen=True)
class Achievement:
    key: str
    name: str          # 官方中文名（zh wiki）
    name_en: str
    kind: str          # earnings / ship_each_crop / friendship
    target: int        # 金币 / 每种作物的个数 / 人数
    description: str = ""
    aliases: tuple[str, ...] = ()
    crops: tuple[str, ...] = ()   # ship_each_crop：作物英文名
    hearts: int = 0               # friendship：几颗心
    points_per_heart: int = 250

    @property
    def names(self) -> tuple[str, ...]:
        return (self.name, *self.aliases)


@dataclass(frozen=True)
class FriendshipRules:
    talk_daily: int = 20
    gift_love: int = 80
    gift_like: int = 45
    gifts_per_week: int = 2
    full_week_bonus: int = 10


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
    friends: int = 0                              # 已经 5 心以上的人数
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
            a["key"], a["name"], a["name_en"], a["kind"], int(a["target"]), a.get("description", ""),
            tuple(a.get("aliases", ())), tuple(a.get("crops", ())), int(a.get("hearts", 0)),
            int(a.get("points_per_heart", 250)),
        ))
    f = raw.get("friendship") or {}
    return AchievementRules(tuple(items), FriendshipRules(
        int(f.get("talk_daily", 20)), int(f.get("gift_love", 80)), int(f.get("gift_like", 45)),
        int(f.get("gifts_per_week", 2)), int(f.get("full_week_bonus", 10))))


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
            known = "、".join(f"{x.name}（{x.name_en}）" for x in rules.achievements)
            raise ValueError(f"未知的成就：{name}（可选：{known}）")
        if a.key not in seen:
            seen.add(a.key)
            goals.append(Goal(a, name))
    return goals


def parse_progress(spec, rules: AchievementRules | None, crops) -> Progress:
    """已有进度："防风草=15,土豆=7,万人迷=12,千万富翁=50000"（或 dict）。

    作物名 → 已运送个数（混合栽培）；友谊类成就名 → 已经达到要求心数的人数；赚钱类成就名 → 已累计赚取的金币。
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
    shipped, friends, earned = {}, 0, 0
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
            friends = n
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


@dataclass(frozen=True)
class CropGap:
    crop: object       # data_loader.Crop
    need: int          # 本季还要收获并直接出货的个数
    source: str = ""


@dataclass
class SeasonTargets:
    goals: list[Goal]
    progress: Progress
    crops: list[CropGap] = field(default_factory=list)      # 混合栽培：本季必须收获的作物
    optional: list[CropGap] = field(default_factory=list)   # 本季能种、但种子要去特殊渠道买的作物
    later: list[str] = field(default_factory=list)          # 本季作物，但今年还买不到种子
    shipped_done: list[str] = field(default_factory=list)   # 已经运够的作物
    friends_left: int = 0       # 万人迷还差几个人（扣掉已有进度和本次规划前几季结交的）
    friend_target: int = 0      # 本季要新结交的村民数 = 还差的人数平均分到今年剩下的季节
    seasons_left: int = SEASONS_PER_YEAR
    earnings: list[tuple[Goal, int]] = field(default_factory=list)  # (成就, 还差多少金币)

    def title(self, g: Goal) -> str:
        """“多作种植（混合栽培：运送 15 份每种农作物）”；用户写的就是官方名时不重复。"""
        a = g.achievement
        return f"{g.label}（{a.description}）" if g.label == a.name else f"{g.label}（{a.name}：{a.description}）"

    def goal(self, kind: str) -> Goal | None:
        return next((g for g in self.goals if g.achievement.kind == kind), None)


def season_targets(goals: list[Goal], progress: Progress, crops, season: str, year: int) -> SeasonTargets:
    t = SeasonTargets(list(goals), progress)
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
        elif a.kind == "friendship":
            t.friends_left = max(0, a.target - progress.friends - len(progress.befriended))
            t.seasons_left = max(1, SEASONS_PER_YEAR - progress.seasons_done)
            t.friend_target = -(-t.friends_left // t.seasons_left)  # 向上取整
        elif a.kind == "earnings":
            t.earnings.append((g, max(0, a.target - progress.earned)))
    return t


def describe_targets(t: SeasonTargets, rules: AchievementRules) -> str:
    """【数据参考】里的【🏆 成就规划目标】一节。"""
    lines = ["以下是为了达成玩家指定的成就，本季**必须**完成的硬性指标（程序根据成就条件和已有进度计算）："]
    if g := t.goal("ship_each_crop"):
        a = g.achievement
        head = f"- {t.title(g)}："
        if t.crops:
            need = "、".join(f"{c.crop.name} {c.need} 个" for c in t.crops)
            lines.append(f"{head}本季必须种下并收获、直接出货：{need}。按预计收获个数计算（多次收获的作物不用种 "
                         f"{a.target} 格）；这些收获要留出这个数量直接出货，不要全部拿去加工或留作献祭。")
        else:
            lines.append(f"{head}本季没有必须种的作物。")
        if t.optional:
            opt = "、".join(f"{c.crop.name} {c.need} 个（{c.source}）" for c in t.optional)
            lines.append(f"  - 可选（种子要去特殊渠道买，本季不强制）：{opt}")
        if t.later:
            lines.append(f"  - 今年还买不到种子：{'、'.join(t.later)}")
        if t.shipped_done:
            lines.append(f"  - 已经运够：{'、'.join(t.shipped_done)}")
    if g := t.goal("friendship"):
        a = g.achievement
        f = rules.friendship
        points = a.hearts * a.points_per_heart
        lines.append(
            f"- {t.title(g)}：还差 {t.friends_left} 人，今年还剩 {t.seasons_left} 个季节，"
            f"本季至少要给 {t.friend_target} 位新的村民送礼（之前季节结交过的村民不算新的）。"
            f"每颗心 {a.points_per_heart} 好感度，{a.hearts} 颗心 = {a.hearts} × {a.points_per_heart} = {points} 点；"
            f"每天对话 +{f.talk_daily}，送最爱的礼物 +{f.gift_love}、喜欢的 +{f.gift_like}，每周最多送 "
            f"{f.gifts_per_week} 次礼物，一周送满 {f.gifts_per_week} 次周日再 +{f.full_week_bonus}。"
            "每周（第 1-7、8-14、15-21、22-28 天）至少安排一天送礼，写成“送{村民}{礼物}”，礼物选该村民最爱或喜欢的。")
    for g, gap in t.earnings:
        lines.append(f"- {t.title(g)}：还差 {gap:,}g（已赚取 {t.progress.earned:,}g）。"
                     "出货、加工品和其他收入都算；请在策略里说明本季怎么提高收入（程序不强制校验金额）。")
    lines.append("请在 macro_strategy.achievement_plan 里专门写一段成就实现思路，在 achievement_milestones 里列出关键节点，"
                 "并在 daily_goals 里显式安排相关动作（例如“种 15 颗土豆（混合栽培）”“送海莉黄水仙（万人迷）”）。")
    return "\n".join(lines)


def advance(progress: Progress, units: dict, gifted, earned: int) -> Progress:
    """一季结束后的进度：预计收获计入已运送、新送礼的村民计入已结交、收入计入已赚取。"""
    shipped = dict(progress.shipped)
    for name, n in units.items():
        shipped[name] = shipped.get(name, 0) + int(n)
    befriended = progress.befriended + tuple(sorted(set(gifted) - set(progress.befriended)))
    return Progress(shipped, progress.friends, befriended, progress.earned + max(0, int(earned)),
                    progress.seasons_done + 1)
