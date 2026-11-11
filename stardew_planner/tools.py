"""工具升级时间轴：在铁匠铺升级工具要 2 天，期间这件工具不在手上（数据见 data/tools.json）。

模型可以在输出 JSON 里给出 tool_upgrades：[{"tool": "喷壶", "day": 5, "level": "铜"}]，表示第 5 天交给克林特。
程序据此：
- 算出取回日：第 D + 2 天早上；那天铁匠铺因节日关门时再往后顺延；
- 校验：交送日、取回日的目标要写明；工具不在手上的日子（第 D + 1 天到取回日前一天）不能安排要用它的工作
  （喷壶 → 浇水、新播种；十字镐 → 下矿；斧头 → 砍树；锄头 → 开垦），否则报冲突，交给模型重写；
- 资金：升级费用在交送日扣掉（金属锭要自己准备，不计）。
"""

from __future__ import annotations

from dataclasses import dataclass

SEASON_DAYS = 28


@dataclass(frozen=True)
class Tool:
    name: str
    name_en: str
    aliases: tuple[str, ...] = ()
    conflict_words: tuple[str, ...] = ()
    blocks_planting: bool = False

    @property
    def names(self) -> tuple[str, ...]:
        return (self.name, *self.aliases)


@dataclass(frozen=True)
class UpgradeLevel:
    name: str
    name_en: str
    bar: str
    bars: int
    price: int


@dataclass(frozen=True)
class ToolRules:
    tools: tuple[Tool, ...]
    levels: tuple[UpgradeLevel, ...]
    upgrade_days: int = 2
    open_festivals: tuple[str, ...] = ()

    def find_tool(self, name: str) -> Tool | None:
        name = name.strip()
        return next((t for t in self.tools if name in t.names or name.lower() == t.name_en.lower()), None)

    def find_level(self, name: str) -> UpgradeLevel | None:
        name = str(name).strip().removesuffix("级")
        return next((lv for lv in self.levels if name in (lv.name, lv.name_en.lower(), lv.name_en)), None)


@dataclass(frozen=True)
class Upgrade:
    tool: Tool
    day: int                      # 交送日
    level: UpgradeLevel | None    # 升到哪一级；没写时不计费用
    ready_day: int                # 可以取回的日子（可能 > 28，跨到下一季）

    @property
    def away_days(self) -> range:
        """整天不在手上的日子（交送日当天交出前还能用，不算）。"""
        return range(self.day + 1, self.ready_day)

    @property
    def label(self) -> str:
        level = f"升级到{self.level.name}" if self.level else "升级"
        return f"{self.tool.name}{level}（第 {self.day} 天交）"


def parse_upgrades(raw, rules: ToolRules, closed_days: set[int]) -> tuple[list[Upgrade], list[str]]:
    """模型输出的 tool_upgrades → Upgrade；写错的条目丢弃并给出问题（交给模型修正，不当格式错误重试）。"""
    if raw in (None, []):
        return [], []
    if not isinstance(raw, list):
        return [], ["⚠️ tool_upgrades 必须是数组，例如 [{\"tool\": \"喷壶\", \"day\": 5, \"level\": \"铜\"}]"]
    upgrades, problems = [], []
    for i, item in enumerate(raw):
        try:
            tool = rules.find_tool(str(item["tool"]))
            day = int(item.get("day", item.get("drop_day")))
        except (TypeError, KeyError, ValueError, AttributeError):
            tool, day = None, None
        if tool is None or day is None or not 1 <= day <= SEASON_DAYS:
            known = "、".join(t.name for t in rules.tools)
            problems.append(f"⚠️ tool_upgrades[{i}] 无效（tool 只能是{known}，day 为 1-28）：{item!r}")
            continue
        level = rules.find_level(item.get("level", "")) if item.get("level") else None
        ready = day + rules.upgrade_days
        while ready in closed_days:  # 取货日铁匠铺关门，再等一天
            ready += 1
        upgrades.append(Upgrade(tool, day, level, ready))
    return upgrades, problems


def check_upgrades(upgrades: list[Upgrade], goals: dict[int, str], planting_days: set[int]) -> list[str]:
    """交送日、取回日要写进日程；工具不在手上的日子不能安排要用它的工作。"""
    problems = []
    for u in upgrades:
        names = u.tool.names
        drop_goal = goals.get(u.day, "")
        if not any(n in drop_goal for n in names) or not any(w in drop_goal for w in ("升级", "交", "送")):
            problems.append(f"⚠️ 第 {u.day} 天的目标没有写“交送升级{u.tool.name}”（tool_upgrades 里安排了这天交给铁匠铺）")
        if u.ready_day <= SEASON_DAYS:
            pick_goal = goals.get(u.ready_day, "")
            if not any(n in pick_goal for n in names) or "取" not in pick_goal:
                problems.append(f"⚠️ 第 {u.ready_day} 天的目标没有写“取回{u.tool.name}”（升级要 2 天，这天早上可以去铁匠铺取）")
        for d in u.away_days:
            if d > SEASON_DAYS:
                break
            goal = goals.get(d, "")
            hit = next((w for w in u.tool.conflict_words if w in goal), None)
            if hit:
                problems.append(f"⚠️ 工具冲突：第 {d} 天{u.tool.name}还在铁匠铺升级（第 {u.day} 天交、第 {u.ready_day} 天取），"
                                f"但目标里安排了“{hit}”")
            elif u.tool.blocks_planting and d in planting_days:
                problems.append(f"⚠️ 工具冲突：第 {d} 天{u.tool.name}还在铁匠铺升级，新播种的作物没法浇水；"
                                "把播种挪到取回之后，或者换个不种地的日子升级")
    return problems


def upgrade_costs(upgrades: list[Upgrade]) -> list[tuple[int, int, str]]:
    """(交送日, 金币, 说明)；没写升级到哪一级的不计费用。"""
    out = []
    for u in upgrades:
        if u.level:
            out.append((u.day, u.level.price,
                        f"工具升级：{u.label} = {u.level.price}g + {u.level.bars} 个{u.level.bar}（金属锭需自备，不计）"))
    return out


def describe_tools(rules: ToolRules) -> str:
    """Prompt【数据参考】里的工具升级一节。"""
    levels = "；".join(f"{lv.name}：{lv.price}g + {lv.bars} 个{lv.bar}" for lv in rules.levels)
    return (f"在铁匠铺升级工具要 {rules.upgrade_days} 天：第 D 天交，第 D + {rules.upgrade_days} 天早上取，"
            f"期间这件工具不在手上；取货日如果是节日、铁匠铺关门，要再等一天。每次只能升一级。费用（交送时支付）：{levels}。"
            "升级工具时在 tool_upgrades 里写明，并在日程里写“交送升级X”和“取回X”；"
            "喷壶不在时不能浇水和播种，十字镐不在时不能下矿，斧头不在时不能砍树，锄头不在时不能开垦。")
