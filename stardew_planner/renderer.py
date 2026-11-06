"""把宏观策略、关键节点、合并后的日程拼接成 Markdown 攻略。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .analysis import rank_crops
from .data_loader import SEASON_ZH, GameData

if TYPE_CHECKING:
    from .generator import StrategyResult

SEASON_ICON = {"spring": "🌸", "summer": "☀️", "fall": "🍂", "winter": "❄️"}


def _cell(text) -> str:
    """表格单元格转义：竖线和换行会破坏 Markdown 表格。"""
    return str(text).replace("|", "\\|").replace("\n", " ")


def render_markdown(result: "StrategyResult", data: GameData | None = None) -> str:
    season_zh = SEASON_ZH[result.season]
    icon = SEASON_ICON[result.season]
    macro = result.macro_strategy or {}
    out: list[str] = []

    # ---- 标题 ----
    out += [
        f"# {icon} 星露谷物语 · 第 {result.year} 年{season_zh}攻略",
        "",
        f"> **玩法风格**：{result.playstyle}　|　**节奏偏好**：{result.pacing}"
        + ("" if result.bonuses.is_empty else f"　|　**{result.bonuses.describe()}**"),
        "",
    ]

    # ---- 宏观策略 ----
    out += ["## 🧭 宏观策略指导", ""]
    if macro.get("summary"):
        out += [f"**核心思路**：{macro['summary']}", ""]
    for i, p in enumerate(macro.get("pillars", []), 1):
        out.append(f"{i}. **{p.get('title', '')}**：{p.get('detail', '')}")
    if macro.get("season_goals"):
        out += ["", "**🎯 本季目标**", ""]
        out += [f"- [ ] {g}" for g in macro["season_goals"]]
    out.append("")

    # ---- 关键节点 ----
    out += ["## 📌 关键时间节点", "", "| 日期 | 事件 | 准备事项 | 最晚准备日 |", "|:---:|---|---|:---:|"]
    for m in result.key_milestones:
        by = m.get("prepare_by_day")
        out.append(
            f"| 第 {m['day']} 天 | **{_cell(m.get('event', ''))}** | "
            f"{_cell(m.get('preparation', ''))} | {f'第 {by} 天' if by else '-'} |"
        )
    out.append("")

    # ---- 跨季校验 ----
    if result.season_warnings:
        out += ["## ⚠️ 换季校验", ""]
        out += [f"> {w}  " for w in result.season_warnings]
        out.append("")

    # ---- 合并后的日程 ----
    events_by_day: dict[int, list[str]] = {}
    if data:
        for e in data.events_in_season(result.season):
            events_by_day.setdefault(e.day, []).append(e.name)

    out += ["## 📅 每日核心目标", ""]
    for block in result.schedule:
        tags = [f"{name}({d}日)" for d in range(block.start, block.end + 1) for name in events_by_day.get(d, [])]
        suffix = f"　🎉 *{'、'.join(tags)}*" if tags else ""
        out.append(f"- **{block.label}**：{block.goal}{suffix}")
    out.append("")

    # ---- 数据附录（来自本地数值，非模型生成）----
    if data:
        out += ["## 📊 数据参考：本季作物收益排行", "",
                "| 作物 | 种子价 | 售价 | 生长天数 | 最早种植 | 整季收获 | 单格净利 | 最佳加工 |",
                "|---|---:|---:|:---:|:---:|:---:|---:|---|"]
        for r in rank_crops(data, result.season, bonuses=result.bonuses, year=result.year):
            artisan = f"{r.best_artisan} ({r.artisan_unit_price}g)" if r.best_artisan else "-"
            out.append(f"| {r.crop.name} | {r.crop.seed_price}g | {r.sell_price}g | {r.grow_days} 天 | 第 {r.plant_day} 天 | "
                       f"{r.harvests} 次 | {r.profit_per_tile}g | {artisan} |")
        applied = "未计入职业与肥料加成" if result.bonuses.is_empty else f"已计入 {result.bonuses.describe()}"
        out += ["", "> 收益为本地数据计算的估算值（从种子最早开售日起种植、收获后立即补种），"
                f"{applied}，未计入作物品质加成。数值来源：Stardew Valley Wiki（1.6）。", ""]

    return "\n".join(out)
