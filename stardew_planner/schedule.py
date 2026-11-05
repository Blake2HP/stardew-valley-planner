"""日程合并：把连续多天相同的核心目标合并成一个区间。"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class ScheduleBlock:
    start: int
    end: int
    goal: str

    @property
    def label(self) -> str:
        return f"第 {self.start} 天" if self.start == self.end else f"第 {self.start}-{self.end} 天"

    def __str__(self) -> str:
        return f"{self.label}：{self.goal}"


def _normalize(goal: str) -> str:
    """比较用的规范化：去首尾空白、压缩内部空白、去掉结尾标点，避免“钓鱼”和“钓鱼。”被当作不同目标。"""
    goal = re.sub(r"\s+", " ", goal.strip())
    return goal.rstrip("。.!！;；，,")


def merge_daily_goals(daily_goals: dict[int | str, str]) -> list[ScheduleBlock]:
    """遍历按天排序的目标，连续且相同（规范化后）的天数合并为一个区间。

    >>> merge_daily_goals({"1": "种防风草", "2": "钓鱼", "3": "钓鱼", "4": "钓鱼。"})
    [ScheduleBlock(start=1, end=1, goal='种防风草'), ScheduleBlock(start=2, end=4, goal='钓鱼')]
    """
    days = sorted((int(d), g) for d, g in daily_goals.items())
    blocks: list[ScheduleBlock] = []
    for day, goal in days:
        norm = _normalize(goal)
        last = blocks[-1] if blocks else None
        if last and last.goal == norm and last.end == day - 1:  # 天数必须连续
            blocks[-1] = ScheduleBlock(last.start, day, last.goal)
        else:
            blocks.append(ScheduleBlock(day, day, norm))
    return blocks
