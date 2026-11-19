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
    return AchievementRules(tuple(items), FriendshipRules(int(f.get("talk_daily", 20)),
                                                          int(f.get("gifts_per_week", 2)),
                                                          int(f.get("full_week_bonus", 10))))


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
