"""星露谷数值查询 / 计算 / 跨季校验工具。所有数字只来自项目 data/ 目录，所有计算都打印公式。

用法（在项目根目录执行）：
    python .claude/skills/stardew-data-guard/scripts/sv.py crop 草莓
    python .claude/skills/stardew-data-guard/scripts/sv.py list --season spring
    python .claude/skills/stardew-data-guard/scripts/sv.py artisan 草莓
    python .claude/skills/stardew-data-guard/scripts/sv.py profit 草莓 --plant-day 14
    python .claude/skills/stardew-data-guard/scripts/sv.py calendar summer
    python .claude/skills/stardew-data-guard/scripts/sv.py check-schedule plan.json --season spring [--fix]

退出码：0 成功；2 数据库中没有所需数据（不要猜，向用户索取）；3 校验发现问题。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(os.getenv("STARDEW_PROJECT_ROOT", Path(__file__).resolve().parents[4]))
sys.path.insert(0, str(PROJECT_ROOT))

from stardew_planner.analysis import available_growing_days, effective_plant_day, season_profit  # noqa: E402
from stardew_planner.data_loader import SEASON_ZH, Crop, GameData, load_game_data, normalize_season  # noqa: E402
from stardew_planner.season_rules import (  # noqa: E402
    SEASON_DAYS, check_plantings, enforce_season_end, last_safe_plant_day,
    next_season, survives_season_change, withering_crops,
)

EXIT_MISSING, EXIT_INVALID = 2, 3


def missing(what: str) -> None:
    print(f"❌ 数据库中没有「{what}」的数据（已查找 {PROJECT_ROOT / 'data'}）。")
    print("   不要猜测数值：请向用户索取确切数据，或经用户同意后补充到 data/ 下对应的 JSON 文件。")
    sys.exit(EXIT_MISSING)


def find_crop(data: GameData, name: str) -> Crop:
    key = name.strip().lower()
    for c in data.crops:
        if key in (c.name.lower(), c.name_en.lower()):
            return c
    missing(f"作物：{name}")


def fmt_yield(c: Crop) -> str:
    return "" if c.harvest_yield == 1 else f" × 单次产量 {c.harvest_yield}"


# ---------------------------------------------------------------------------

def cmd_crop(data: GameData, a) -> None:
    c = find_crop(data, a.name)
    print(f"【{c.name} / {c.name_en}】 来源: data/crops.json")
    print(f"  可种季节: {'/'.join(SEASON_ZH[s] for s in c.seasons)}")
    print(f"  种子价: {c.seed_price}g（{c.seed_source}）  售价: {c.sell_price}g  类别: {c.category}")
    print(f"  生长天数: {c.grow_days}  再生天数: {c.regrow_days or '不可再生'}  单次产量: {c.harvest_yield}")
    print(f"  最早开售日: 第 {c.earliest_available_day} 天")
    for s in c.seasons:
        tail = "，可跨入下一季继续生长" if survives_season_change(c, s) else ""
        print(f"  {SEASON_ZH[s]}最晚种植日 = {SEASON_DAYS} − {c.grow_days} = {last_safe_plant_day(c)}{tail}")


def cmd_list(data: GameData, a) -> None:
    crops = data.crops_in_season(a.season) if a.season else data.crops
    for c in crops:
        regrow = f"再生{c.regrow_days}天" if c.regrow_days else "单次"
        print(f"{c.name}\t种子{c.seed_price}g\t售价{c.sell_price}g\t生长{c.grow_days}天\t{regrow}")


def cmd_artisan(data: GameData, a) -> None:
    c = find_crop(data, a.name)
    rules = data.artisan_rules_for(c)
    if not rules:
        missing(f"{c.name}（类别 {c.category}）的加工规则")
    print(f"【{c.name} 加工收益】 来源: data/artisan.json，基础售价 {c.sell_price}g")
    for r in rules:
        if r.fixed_price is not None:
            print(f"  {r.machine}→{r.product}: 固定价格 = {r.fixed_price}g")
        else:
            bonus = f" + {r.bonus}" if r.bonus else ""
            print(f"  {r.machine}→{r.product}: 基础售价 × {r.multiplier}{bonus} = "
                  f"{c.sell_price} × {r.multiplier}{bonus} = {r.price_for(c)}g"
                  f"（溢价 = {r.price_for(c)} − {c.sell_price} = {r.price_for(c) - c.sell_price}g，耗时 {r.processing_days} 天）")


def cmd_profit(data: GameData, a) -> None:
    c, d = find_crop(data, a.name), a.plant_day
    if not 1 <= d <= SEASON_DAYS:
        print(f"❌ 种植日必须在 1-{SEASON_DAYS} 之间")
        sys.exit(EXIT_INVALID)
    print(f"【{c.name} 收益计算】计划种植日 = 第 {d} 天，来源: data/crops.json")
    if d < c.earliest_available_day:
        d = effective_plant_day(c, d)
        print(f"  ⚠️ {c.name}种子第 {c.earliest_available_day} 天才开售（{c.seed_source}），"
              f"实际种植日 = max(计划种植日, 最早开售日) = {d}")
    n = available_growing_days(d)
    print(f"  实际可用生长天数 = 28 − 种植日 {d} + 1 = {n}")
    single = c.sell_price * c.harvest_yield - c.seed_price
    print(f"  单轮日均利润 = (售价 {c.sell_price}{fmt_yield(c)} − 种子成本 {c.seed_price}) / 生长天数 {c.grow_days}"
          f" = {single:g} / {c.grow_days} = {single / c.grow_days:.2f}g/天")

    if c.grow_days > n - 1:
        print(f"  ⚠️ 成熟日 = {d} + {c.grow_days} = {d + c.grow_days} > {SEASON_DAYS}，本季无法收获")
        harvests, seeds = 0, 1
    elif c.regrow_days:
        harvests, seeds = 1 + (n - 1 - c.grow_days) // c.regrow_days, 1
        print(f"  收获次数 = 1 + ⌊(可用天数 {n} − 1 − 生长 {c.grow_days}) / 再生 {c.regrow_days}⌋ = {harvests}（种子 1 份）")
    else:
        harvests = seeds = (n - 1) // c.grow_days
        print(f"  收获次数 = ⌊(可用天数 {n} − 1) / 生长 {c.grow_days}⌋ = {harvests}（收获后立即补种，种子 {seeds} 份）")

    revenue = harvests * c.sell_price * c.harvest_yield
    profit = revenue - seeds * c.seed_price
    # 与项目代码 analysis.season_profit 保持一致（它对净利取整）
    assert (harvests, int(profit)) == season_profit(c, d), "sv.py 与 analysis.season_profit 计算不一致"
    print(f"  单格净利 = 收获次数 {harvests} × 售价 {c.sell_price}{fmt_yield(c)} − 种子数 {seeds} × 种子价 {c.seed_price}"
          f" = {revenue:g} − {seeds * c.seed_price} = {profit:g}g")
    print(f"  占地日均 = 单格净利 {profit:g} / 可用天数 {n} = {profit / n:.2f}g/天")
    print("  注：未计入品质、职业加成与肥料，需要时请向用户确认加成数值。")


def cmd_calendar(data: GameData, a) -> None:
    s = normalize_season(a.season)
    print(f"【{SEASON_ZH[s]}固定日程】 来源: data/calendar.json")
    for e in data.events_in_season(s):
        print(f"  第 {e.day} 天 {e.name}（{e.type}）：{e.tip}")
    dying = "、".join(c.name for c in withering_crops(data, s)) or "无"
    print(f"  换季：第 {SEASON_DAYS} 天后进入{SEASON_ZH[next_season(s)]}，枯死作物：{dying}")


def cmd_check_schedule(data: GameData, a) -> None:
    raw = json.loads(Path(a.file).read_text(encoding="utf-8"))
    raw = raw.get("daily_goals", raw)
    goals = {int(k): str(v) for k, v in raw.items()}
    s = normalize_season(a.season)

    warnings = check_plantings(goals, data, s)
    fixed, inserted = enforce_season_end(goals)
    for w in warnings:
        print(f"⚠️ {w}")
    if inserted:
        print(f"⚠️ 第 {SEASON_DAYS} 天缺少“清理农场/停止种植”，修正为：{fixed[SEASON_DAYS]}")
    dying = "、".join(c.name for c in withering_crops(data, s))
    if dying:
        print(f"ℹ️ 进入{SEASON_ZH[next_season(s)]}时将枯死：{dying}")
    if not warnings and not inserted:
        print("✅ 跨季校验通过")
    if a.fix:
        print(json.dumps({str(k): v for k, v in sorted(fixed.items())}, ensure_ascii=False, indent=2))
    if warnings or inserted:
        sys.exit(EXIT_INVALID)


def main() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")

    p = argparse.ArgumentParser(description="星露谷数值工具（只读 data/，计算带公式）")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("crop").add_argument("name")
    sub.add_parser("list").add_argument("--season")
    sub.add_parser("artisan").add_argument("name")
    pp = sub.add_parser("profit")
    pp.add_argument("name")
    pp.add_argument("--plant-day", type=int, default=1)
    sub.add_parser("calendar").add_argument("season")
    pc = sub.add_parser("check-schedule")
    pc.add_argument("file")
    pc.add_argument("--season", required=True)
    pc.add_argument("--fix", action="store_true", help="打印修正后的 28 天 JSON")
    a = p.parse_args()

    data = load_game_data(PROJECT_ROOT / "data")
    globals()[f"cmd_{a.cmd.replace('-', '_')}"](data, a)


if __name__ == "__main__":
    main()
