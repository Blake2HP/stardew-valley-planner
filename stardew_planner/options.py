"""规划参数：generate_strategy / generate_plan、命令行、网页、存档导入共用的一份定义。

新加一个规划参数时，只要在 PlanOptions 里加一个字段（写清说明和默认值）：
- generate_strategy / generate_plan 直接按字段名接收（关键字参数或 options=PlanOptions(...)）；
- 命令行（main.py）按同名的 argparse dest 取值，存档导入（save_parser.SaveState.generator_kwargs）按字段名填；
- 网页（web/app.py）按同名的请求字段取值；
- 只用于第一季的参数列在 FIRST_SEASON_ONLY 里，跨季规划时 next_season() 会把它们清掉。
基础的取值检查集中在 validate()；需要游戏数据才能检查的（作物名、成就名、动物名……）仍由生成器负责。
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields, replace
from typing import Any

SEASON_DAYS = 28


@dataclass(frozen=True)
class PlanOptions:
    # ---- 角色与加成 ----
    professions: tuple = ()
    """职业，如 ("tiller", "artisan") 或中文名；10 级职业会自动加入前置的农耕人。"""
    fertilizer: str | None = None
    """肥料：加速 speed_gro / deluxe_speed_gro / hyper_speed_gro，品质 basic / quality / deluxe_fertilizer（或中文名）。"""
    farming_level: int | None = None
    """耕种等级（0-14）；给出或用了品质肥料时计入作物品质。"""
    processing_share: float = 1.0
    """机器能加工的收获比例（0-1），决定“高星直接卖、低星加工”的分界。"""

    # ---- 起点 ----
    start_gold: int | None = None
    """期初资金；不传时第 1 年春季第 1 天用开局金币（data/farm.json），其他情况为未知。只用于第一季。"""
    start_day: int = 1
    """季中重新规划时今天是本季第几天（1-28）。只规划第 start_day 天到第 28 天：收益按这天起算，
    更早的种植、工具升级、建筑会被报出来交给模型修正；第 1 年春季从第 2 天起不再默认开局金币和赠送的种子。只用于第一季。"""
    farm_type: str | None = None
    """农场地图（默认标准农场）；草原农场开局没有赠送的防风草种子。"""

    # ---- 设施 ----
    kegs: int = 0
    """小桶数量。给出时按机器数量和加工时间模拟加工（见 processing.py），加工净增计入资金。"""
    jars: int = 0
    """罐头瓶数量。"""
    bee_houses: int = 0
    """室外蜂房数量。每 4 天一罐，按室外开着的最值钱的花算花蜜（见 honey.py）；冬季不产。"""
    greenhouse: bool = False
    """温室已修好：可以把作物种在温室（不受季节限制、可再生作物不会枯死，最多 120 格）。"""
    location: str | None = None
    """种植地点："pelican_town"（默认）或 "ginger_island"（姜岛农场：不受季节限制、换季不枯死）。"""
    route: str | None = None
    """"community_center"：社区中心献祭路线，收集包要用的作物先留下、不卖，没留下时报献祭校验（见 bundles.py）。"""

    # ---- 已有的东西（存档导入时自动填） ----
    animals: Any = None
    """本季第 1 天（季中开始时为 start_day）新买的动物，"鸡:2,牛:1" 或 {"鸡": 2}；买动物的钱计入资金。只用于第一季。"""
    owned_animals: Any = None
    """已经养着的成年动物，格式同 animals；不计购买费，从第一天起就有产出。只用于第一季（之后随季末状态带下去）。"""
    in_ground: Any = None
    """地里已经种着的作物：[{"crop": "蓝莓", "tiles": 20, "ready_day": 15, "place": "field"}]，
    ready_day 是本季第几天可以收获；种子钱已付过，收获计入资金。只用于第一季。"""
    bundle_done: Any = None
    """已经交过的收集包作物：[("Spring Crops Bundle", "Parsnip"), ...]（收集包英文名, 作物英文名），例如从存档导入；
    献祭路线不再要求预留这些作物。只用于第一季（之后随季末状态带下去）。"""

    # ---- 联机与成就 ----
    players: int = 1
    """联机人数（1-4）。多人时 daily_goals 每天按玩家写成对象（见 coop.py）。"""
    roles: Any = None
    """联机分工，"P1:种田, P2:下矿" 或 {"P1": "种田"}。"""
    achievements: Any = None
    """想完成的 Steam 成就，"混合栽培,万人迷" 或列表（见 data/achievements.json）。"""
    achievement_progress: Any = None
    """已有的成就进度，"防风草=15,万人迷=12"、dict 或 achievements.Progress；不给时从零算。"""

    # ---- 生成过程 ----
    revise_rounds: int = 1
    """自动修正的最多轮数（0 = 关闭）。程序校验发现问题时反馈给模型重新生成，问题变少才采用。"""

    @classmethod
    def names(cls) -> tuple[str, ...]:
        return tuple(f.name for f in fields(cls))

    @classmethod
    def build(cls, options: "PlanOptions | None" = None, **overrides) -> "PlanOptions":
        """在 options（默认全部用默认值）的基础上覆盖关键字参数；写错参数名时报 TypeError。"""
        unknown = sorted(set(overrides) - set(cls.names()))
        if unknown:
            raise TypeError(f"未知的规划参数：{', '.join(unknown)}")
        base = options or cls()
        if "professions" in overrides and overrides["professions"] is not None:
            overrides["professions"] = tuple(overrides["professions"])
        return replace(base, **overrides) if overrides else base

    @classmethod
    def pick(cls, source, **extra) -> "PlanOptions":
        """从任意对象（argparse.Namespace、pydantic 请求）里取出同名的属性，没有的用默认值。"""
        values = {n: getattr(source, n) for n in cls.names() if hasattr(source, n)}
        return cls.build(**{**values, **extra})

    def validate(self) -> "PlanOptions":
        """不需要游戏数据的取值检查；不合法时报 ValueError。"""
        if self.start_gold is not None and self.start_gold < 0:
            raise ValueError(f"期初资金必须 ≥ 0，收到 {self.start_gold}")
        day = self.start_day
        if isinstance(day, bool) or not isinstance(day, int) or not 1 <= day <= SEASON_DAYS:
            raise ValueError(f"起始日必须是 1-{SEASON_DAYS} 的整数，收到 {day!r}")
        if not 0 <= self.processing_share <= 1:
            raise ValueError(f"加工比例必须在 0-1 之间，收到 {self.processing_share}")
        if self.revise_rounds < 0:
            raise ValueError(f"自动修正轮数必须 ≥ 0，收到 {self.revise_rounds}")
        if self.kegs < 0 or self.jars < 0 or self.bee_houses < 0:
            raise ValueError(f"机器数量不能为负数，收到小桶 {self.kegs}、罐头瓶 {self.jars}、蜂房 {self.bee_houses}")
        return self

    @property
    def machines(self) -> dict[str, int]:
        return {name: n for name, n in (("小桶", self.kegs), ("罐头瓶", self.jars), ("蜂房", self.bee_houses)) if n}

    def next_season(self, achievement_progress=None) -> "PlanOptions":
        """跨季规划的下一季：只用于第一季的参数清掉，成就进度换成上一季结束时的。"""
        cleared = {name: getattr(PlanOptions, name) for name in FIRST_SEASON_ONLY}
        if achievement_progress is not None:
            cleared["achievement_progress"] = achievement_progress
        return replace(self, **cleared)


# 只用于第一季：之后的季节由上季的季末状态接上（资金、动物、地里的作物、已交的收集包），并从第 1 天开始
FIRST_SEASON_ONLY = ("start_gold", "start_day", "animals", "owned_animals", "in_ground", "bundle_done")
