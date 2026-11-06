"""把宏观策略、关键节点、合并后的日程拼接成 Markdown 攻略。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .analysis import rank_crops
from .data_loader import SEASON_ZH, GameData
from .farm_state import describe_finance, render_state_for_prompt, season_label

if TYPE_CHECKING:
    from .generator import PlanResult, StrategyResult

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

    # ---- 上季延续 ----
    if result.prior_state is not None:
        out += ["## 🔄 上季延续状态", ""]
        out += [f"> {line}  " for line in render_state_for_prompt(result.prior_state).splitlines()]
        out.append("")

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

    # ---- 种植清单与资金（程序计算）----
    if result.plantings:
        out += ["## 🌱 种植清单", "", "| 播种日 | 作物 | 格数 | 种子支出 | 收获日 |", "|:---:|---|---:|---:|---|"]
        for p in result.plantings:
            days = "、".join(season_label(result.season, d) for d in p.harvests) or "来不及成熟"
            out.append(f"| 第 {p.day} 天 | {p.crop.name} | {p.tiles} | {p.tiles * p.crop.seed_price}g | {days} |")
        out.append("")
    if result.finance is not None:
        out += ["## 💰 资金预期", ""]
        out += [f"- {line}" if not line.startswith("- ") else f"  {line}" for line in describe_finance(result.finance)]
        out += ["", "> 种植收支由程序根据种植清单和 data/ 计算；其他收入是模型估算，仅供参考。", ""]

    # ---- 数据附录（来自本地数值，非模型生成）----
    if data:
        out += ["## 📊 数据参考：本季作物收益排行", "",
                "| 作物 | 种子价 | 售价 | 生长天数 | 最早种植 | 整季收获 | 单格净利 | 最佳加工 | 按品质出售策略 |",
                "|---|---:|---:|:---:|:---:|:---:|---:|---|---|"]
        for r in rank_crops(data, result.season, bonuses=result.bonuses, year=result.year,
                            processing_share=result.processing_share):
            artisan = f"{r.best_artisan} ({r.artisan_unit_price}g)" if r.best_artisan else "-"
            plan = r.sale_plan.summary() if r.sale_plan else "-"
            if r.profit_with_plan is not None:
                plan += f"，按此单格净利 {r.profit_with_plan}g"
            out.append(f"| {r.crop.name} | {r.crop.seed_price}g | {r.sell_price}g | {r.grow_days} 天 | 第 {r.plant_day} 天 | "
                       f"{r.harvests} 次 | {r.profit_per_tile}g | {artisan} | {_cell(plan)} |")
        applied = "未计入职业与肥料加成" if result.bonuses.is_empty else f"已计入 {result.bonuses.describe()}"
        quality = (f"售价为普通品质，净利按品质分布（{result.bonuses.describe_quality()}）取期望值"
                   if result.bonuses.quality_active else "未计入作物品质（全部按普通品质）")
        out += ["", "> 收益为本地数据计算的估算值（从种子最早开售日起种植、收获后立即补种），"
                f"{applied}，{quality}。出售策略不计机器成本与加工时间"
                + ("" if result.processing_share >= 1 else f"，并假设机器只够加工 {result.processing_share:.0%} 的收获")
                + "。数值来源：Stardew Valley Wiki（1.6）。", ""]

    return "\n".join(out)


def _gold(value: int | None) -> str:
    return "未知" if value is None else f"{value}g"


def render_plan(plan: "PlanResult", data: GameData | None = None) -> str:
    """跨季连续规划：总览表 + 各季攻略（各季标题降一级）。"""
    first, last = plan.results[0], plan.results[-1]
    span = (f"第 {first.year} 年{SEASON_ZH[first.season]} → 第 {last.year} 年{SEASON_ZH[last.season]}"
            if len(plan.results) > 1 else f"第 {first.year} 年{SEASON_ZH[first.season]}")
    out = [
        f"# 🗓️ 星露谷物语 · 跨季连续规划（{span}）",
        "",
        f"> **玩法风格**：{first.playstyle}　|　**节奏偏好**：{first.pacing}"
        + ("" if first.bonuses.is_empty else f"　|　**{first.bonuses.describe()}**"),
        "",
        "## 📈 各季衔接总览",
        "",
        "| 季节 | 期初资金 | 种植净收支 | 期末（只算种植） | 期末（含模型估算） | 跨入下一季的作物 |",
        "|---|---:|---:|---:|---:|---|",
    ]
    for r in plan.results:
        f, s = r.finance, r.end_state
        carry = "、".join(f"{c.crop.name} ×{c.tiles}" for c in s.carry) if s and s.carry else "-"
        out.append(f"| 第 {r.year} 年{SEASON_ZH[r.season]} | {_gold(f.start_gold)} | {f.crop_net}g | "
                   f"{_gold(f.end_gold)} | {_gold(f.end_gold_estimated)} | {carry} |")
    out += ["", "> 种植收支由程序计算；“含模型估算”另加了模型对钓鱼、挖矿等收入的估算，仅供参考。", ""]
    for r in plan.results:
        md = r.markdown or render_markdown(r, data)
        out += ["---", ""]
        out += [("#" + line) if line.startswith("#") else line for line in md.splitlines()]
        out.append("")
    return "\n".join(out)
