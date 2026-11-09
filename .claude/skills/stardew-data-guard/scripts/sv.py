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

from stardew_planner.analysis import (  # noqa: E402
    available_growing_days, crop_note, effective_plant_day, harvest_plan, sale_plan, season_profit, seeds_to_buy,
)
from stardew_planner.bonuses import QUALITIES, Bonuses, floor_mul, gold_chance, resolve_bonuses  # noqa: E402
from stardew_planner.data_loader import SEASON_ZH, Crop, GameData, load_game_data, normalize_season  # noqa: E402
from stardew_planner.farm_state import alive_seasons, harvest_days, season_label  # noqa: E402
from stardew_planner.season_rules import (  # noqa: E402
    SEASON_DAYS, check_gifts, check_plantings, enforce_season_end, last_safe_plant_day,
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


def get_bonuses(a) -> Bonuses:
    try:
        b = resolve_bonuses([a.professions], a.fertilizer, farming_level=a.farming_level)
    except ValueError as e:
        print(f"❌ {e}")
        sys.exit(EXIT_INVALID)
    for note in b.notes:
        print(f"ℹ️ {note}")
    if not b.is_empty:
        print(f"  加成：{b.describe()}（来源: data/bonuses.json）")
    return b


def explain_grow(b: Bonuses, c: Crop) -> str:
    if not b.growth_speed_percent:
        return f"生长天数 = {c.grow_days}"
    return (f"生长天数 = 查表 speed_grow_days[{b.growth_speed_percent}%]（{b.speed_sources()}）"
            f" = {b.grow_days(c)}（基础 {c.grow_days} 天，来源: data/crops.json）")


def explain_price(b: Bonuses, c: Crop) -> str:
    price, steps = c.sell_price, []
    for p in b.professions:
        if p.crop_price_multiplier != 1.0 and c.category in p.applies_to_categories:
            new = floor_mul(price, p.crop_price_multiplier)
            steps.append(f"⌊{price} × {p.name_zh} {p.crop_price_multiplier}⌋ = {new}")
            price = new
    assert price == b.crop_price(c)
    return f"售价 = {c.sell_price}" + "".join(f"；{s}" for s in steps) + f" → {price}g"


# ---------------------------------------------------------------------------

def cmd_crop(data: GameData, a) -> None:
    c = find_crop(data, a.name)
    print(f"【{c.name} / {c.name_en}】 来源: data/crops.json")
    b = get_bonuses(a)
    print(f"  可种季节: {'/'.join(SEASON_ZH[s] for s in c.seasons)}")
    print(f"  种子价: {c.seed_price}g（{c.seed_source}）  售价: {c.sell_price}g  类别: {c.category}")
    print(f"  生长天数: {c.grow_days}  再生天数: {c.regrow_days or '不可再生'}  单次产量: {c.harvest_yield}")
    print(f"  最早开售日: 第 {c.earliest_available_day} 天  开售年份: 第 {c.available_year} 年起")
    if crop_note(c):
        print(f"  备注: {crop_note(c)}")
    speeds = "  ".join(f"{k}%→{v}天" for k, v in c.speed_grow_days.items())
    print(f"  加速后生长天数: {speeds or '无数据'}")
    if not b.is_empty:
        print(f"  {explain_grow(b, c)}")
        print(f"  {explain_price(b, c)}")
    g = b.grow_days(c)
    for s in c.seasons:
        tail = "，可跨入下一季继续生长" if survives_season_change(c, s) else ""
        print(f"  {SEASON_ZH[s]}最晚种植日 = {SEASON_DAYS} − {g} = {last_safe_plant_day(c, b)}{tail}")


def cmd_list(data: GameData, a) -> None:
    crops = data.crops_in_season(a.season, a.year) if a.season else data.crops_for_year(a.year)
    for c in crops:
        regrow = f"再生{c.regrow_days}天" if c.regrow_days else "单次"
        year = f"\t第{c.available_year}年起" if c.available_year > 1 else ""
        print(f"{c.name}\t种子{c.seed_price}g\t售价{c.sell_price}g\t生长{c.grow_days}天\t{regrow}{year}")


def cmd_artisan(data: GameData, a) -> None:
    c = find_crop(data, a.name)
    rules = data.artisan_rules_for(c)
    if not rules:
        missing(f"{c.name}（类别 {c.category}）的加工规则")
    print(f"【{c.name} 加工收益】 来源: data/artisan.json，基础售价 {c.sell_price}g")
    b = get_bonuses(a)
    raw_price = b.crop_price(c)
    for r in rules:
        base = r.price_for(c)
        if r.fixed_price is not None:
            line = f"  {r.machine}→{r.product}: 固定价格 = {base}g"
        else:
            bonus = f" + {r.bonus}" if r.bonus else ""
            line = (f"  {r.machine}→{r.product}: ⌊基础售价 × {r.multiplier}{bonus}⌋ = "
                    f"⌊{c.sell_price} × {r.multiplier}{bonus}⌋ = {base}g")
        price = base
        for p in b.professions:
            if p.artisan_price_multiplier != 1.0:
                new = floor_mul(price, p.artisan_price_multiplier)
                line += f"；⌊{price} × {p.name_zh} {p.artisan_price_multiplier}⌋ = {new}g"
                price = new
        assert price == b.artisan_price(r, c)
        print(line + f"（比直接卖 {raw_price}g 多 {price - raw_price}g，耗时 {r.processing_days} 天）")
    if not b.is_empty:
        print("  注：加工品价格只看原料的基础售价，不受农耕人加成影响。")


def cmd_profit(data: GameData, a) -> None:
    c, d = find_crop(data, a.name), a.plant_day
    if not 1 <= d <= SEASON_DAYS:
        print(f"❌ 种植日必须在 1-{SEASON_DAYS} 之间")
        sys.exit(EXIT_INVALID)
    print(f"【{c.name} 收益计算】计划种植日 = 第 {d} 天，来源: data/crops.json")
    b = get_bonuses(a)
    g, price, fert = b.grow_days(c), b.crop_price(c), b.fertilizer_cost
    if not b.is_empty:
        print(f"  {explain_grow(b, c)}")
        print(f"  {explain_price(b, c)}")
    if a.year is not None and a.year < c.available_year:
        print(f"  ⚠️ {c.name}种子第 {c.available_year} 年起才出售（{c.seed_source}），"
              f"第 {a.year} 年买不到；以下按第 {c.available_year} 年计算")
    if d < c.earliest_available_day:
        d = effective_plant_day(c, d)
        print(f"  ⚠️ {c.name}种子第 {c.earliest_available_day} 天才开售（{c.seed_source}），"
              f"实际种植日 = max(计划种植日, 最早开售日) = {d}")
    n = available_growing_days(d)
    print(f"  实际可用生长天数 = 28 − 种植日 {d} + 1 = {n}")
    value = b.expected_crop_value(c)
    if b.quality_active:
        print(f"  {explain_quality_value(b, c)}")
        unit_text = f"品质期望收入 {value:.2f}"
    else:
        unit_text = f"售价 {price}{fmt_yield(c)}"
    single = value - c.seed_price
    print(f"  单轮日均利润 = ({unit_text} − 种子成本 {c.seed_price}) / 生长天数 {g}"
          f" = {single:g} / {g} = {single / g:.2f}g/天")

    harvests, seeds = harvest_plan(g, c.regrow_days, n)
    if g > n - 1:
        print(f"  ⚠️ 成熟日 = {d} + {g} = {d + g} > {SEASON_DAYS}，本季无法收获")
    elif c.regrow_days:
        print(f"  收获次数 = 1 + ⌊(可用天数 {n} − 1 − 生长 {g}) / 再生 {c.regrow_days}⌋ = {harvests}"
              f"（种子 1 份，再生天数不受加速影响）")
    else:
        print(f"  收获次数 = ⌊(可用天数 {n} − 1) / 生长 {g}⌋ = {harvests}（收获后立即补种，种子 {seeds} 份）")

    bought = seeds_to_buy(c, harvests, seeds)
    if bought != seeds:
        replants = seeds - 1
        print(f"  购买种子数 = 种子 {seeds} 份 − 补种 {replants} 次 × min(1, 每次掉落 {c.seed_drop:g} 颗) = {bought:g} 份"
              f"（补种先用收获掉落的种子）")
    revenue = harvests * value
    profit = revenue - bought * c.seed_price - fert
    # 与项目代码 analysis.season_profit 保持一致（它对净利取整）
    assert (harvests, int(profit)) == season_profit(c, d, b), "sv.py 与 analysis.season_profit 计算不一致"
    fert_text = f" − 肥料 {fert}" if b.fertilizer else ""
    print(f"  单格净利 = 收获次数 {harvests} × {unit_text} − 购买种子 {bought:g} × 种子价 {c.seed_price}"
          f"{fert_text} = {revenue:g} − {bought * c.seed_price:g}{fert_text} = {profit:g}g")
    if crop_note(c):
        print(f"  备注：{crop_note(c)}")
    print(f"  占地日均 = 单格净利 {profit:g} / 可用天数 {n} = {profit / n:.2f}g/天")
    if b.fertilizer and not b.fertilizer.price:
        print(f"  注：{b.fertilizer.name_zh}无法直接购买（{b.fertilizer.source}），肥料成本按 0 计。")
    if len(c.seasons) > 1:
        print(f"  注：这里只算当季 28 天；{c.name}可跨季生长，收益表按完整存活期计算，收获日见 sv.py simulate。")
    if b.quality_active:
        print("  注：净利是按品质分布的期望值（直接卖）；加工策略见 sv.py quality。")
    else:
        print("  注：未计入作物品质（加 --farming-level 或品质肥料即可计入）；职业与肥料只计入了上面列出的部分。")


def explain_quality_value(b: Bonuses, c: Crop) -> str:
    """每次收获的品质期望收入：Σ 概率 × 该品质售价 + 额外产出 × 普通售价。"""
    odds = b.quality_odds()
    terms = " + ".join(f"{odds[q]:.4f} × {b.crop_price(c, q)}" for q in QUALITIES if odds[q])
    extra = c.harvest_yield - 1
    extra_text = f" + 额外产出 {extra:g} × 普通 {b.crop_price(c)}" if extra else ""
    return f"品质期望收入 = {terms}{extra_text} = {b.expected_crop_value(c):.2f}g（每次收获）"


def cmd_quality(data: GameData, a) -> None:
    """品质概率、各品质售价，以及“高星直接卖、低星加工”的出售策略。"""
    c = find_crop(data, a.name)
    print(f"【{c.name} 品质与出售策略】 来源: data/bonuses.json、data/crops.json、data/artisan.json")
    b = get_bonuses(a)
    if not b.quality_active:
        print("  未给出耕种等级，也没有用品质肥料：按耕种等级 0 计算。")
        b = resolve_bonuses([p.key for p in b.professions], b.fertilizer.key if b.fertilizer else None,
                            farming_level=b.effective_farming_level)
    lv, f = b.effective_farming_level, b.fertilizer_quality_level
    g = gold_chance(lv, f)
    print(f"  金星几率 g = 0.2 × ({lv} / 10) + 0.2 × 肥料等级 {f} × (({lv} + 2) / 12) + 0.01 = {g:.4f}"
          + ("（> 1，掷骰必中）" if g > 1 else ""))
    print(f"  银星几率 = min(0.75, 2g) = {min(0.75, 2 * g):.4f}" + ("；铱星几率 = g / 2" if f >= 3 else ""))
    print(f"  品质分布（依次独立掷骰）：{b.describe_quality()}" + ("；顶级肥料不产出普通品质" if f >= 3 else ""))
    print("  售价 = ⌊基础售价 × 品质倍率⌋" + ("，农耕人再 ⌊× 1.1⌋" if any(p.crop_price_multiplier != 1 for p in b.professions) else "")
          + "：" + "、".join(f"{b.quality_name(q)} {b.crop_price(c, q)}g" for q in QUALITIES))
    print(f"  {explain_quality_value(b, c)}")
    if c.harvest_yield != 1:
        print(f"  注：品质只作用于每次收获的第一个作物，额外的 {c.harvest_yield - 1:g} 个一律普通品质。")

    plan = sale_plan(c, data.artisan_rules_for(c), b, a.processing_share)
    if plan.artisan_label is None:
        print("  出售策略：直接卖（数据库中没有这种作物的加工规则）")
        return
    artisan = plan.options[0].artisan_price
    print(f"  最佳加工：{plan.artisan_label} {artisan}g（加工品不继承原料品质）")
    for o in plan.options:
        if o.units <= 0:
            continue
        print(f"    {o.name}：直接卖 {o.direct_price}g，加工 {artisan}g，加工多赚 {o.gain:+d}g，"
              f"每次收获 {o.units:.4f} 个，其中加工 {o.processed_share:.0%}")
    share = f"机器能加工每次收获的 {a.processing_share:.0%}"
    print(f"  出售策略（{share}，按“加工多赚”从大到小分配）：{plan.summary()}")
    print(f"  每次收获期望收入：全部直接卖 {plan.expected_direct:.2f}g → 按策略 {plan.expected_best:.2f}g"
          "（不计机器成本与加工时间）")


def cmd_simulate(data: GameData, a) -> None:
    """跨季收获模拟：一次播种在本季及之后季节的全部收获日。"""
    c, d, s = find_crop(data, a.name), a.plant_day, normalize_season(a.season)
    if not 1 <= d <= SEASON_DAYS:
        print(f"❌ 种植日必须在 1-{SEASON_DAYS} 之间")
        sys.exit(EXIT_INVALID)
    if s not in c.seasons:
        print(f"❌ {c.name}不是{SEASON_ZH[s]}作物（可种季节 = {'/'.join(SEASON_ZH[x] for x in c.seasons)}）")
        sys.exit(EXIT_INVALID)
    print(f"【{c.name} 跨季收获模拟】{SEASON_ZH[s]}第 {d} 天种下，来源: data/crops.json")
    b = get_bonuses(a)
    g, n = b.grow_days(c), alive_seasons(c, s)
    limit = SEASON_DAYS * n
    print(f"  可连续生长季数 = {n}（{'/'.join(SEASON_ZH[x] for x in c.seasons)}），最后存活日 = 28 × {n} = 第 {limit} 天（从本季第 1 天起算）")
    if b.growth_speed_percent:
        print(f"  {explain_grow(b, c)}")
    days = harvest_days(c, d, s, b)
    print(f"  首次收获日 = 种植日 {d} + 生长 {g} = {d + g}")
    if not days:
        print(f"  ⚠️ {d + g} > {limit}：来不及成熟就会枯死")
        return
    if c.regrow_days:
        print(f"  之后每隔再生 {c.regrow_days} 天收获一次，直到第 {limit} 天：共 {len(days)} 次")
    labels = "、".join(season_label(s, x) for x in days)
    print(f"  收获日：{labels}")
    later = [x for x in days if x > SEASON_DAYS]
    if later:
        print(f"  其中 {len(later)} 次落在之后的季节：换季时不要清理这块地")
    if a.tiles:
        price = b.crop_price(c)
        total = len(days) * a.tiles * c.harvest_yield * price
        print(f"  总收入 = 收获 {len(days)} 次 × {a.tiles} 格{fmt_yield(c)} × 售价 {price} = {total:g}g；"
              f"种子 = {a.tiles} × {c.seed_price} = {a.tiles * c.seed_price}g")


def cmd_calendar(data: GameData, a) -> None:
    s = normalize_season(a.season)
    print(f"【{SEASON_ZH[s]}固定日程】 来源: data/calendar.json")
    for e in data.events_in_season(s):
        print(f"  第 {e.day} 天 {e.name}（{e.type}）：{e.tip}")
    dying = "、".join(c.name for c in withering_crops(data, s, a.year)) or "无"
    print(f"  换季：第 {SEASON_DAYS} 天后进入{SEASON_ZH[next_season(s)]}，枯死作物：{dying}")


def cmd_npc(data: GameData, a) -> None:
    """NPC 生日与喜好（data/npcs.json）。"""
    nd = data.npc_data
    n = nd.find(a.name)
    if n is None:
        missing(f"NPC：{a.name}（目前收录：{'、'.join(x.name for x in nd.npcs)}）")
    print(f"【{n.name} / {n.name_en}】 来源: data/npcs.json")
    print(f"  生日：{SEASON_ZH[n.birthday_season]}第 {n.birthday_day} 天")
    print(f"  最爱（专属）：{'、'.join(i.name for i in n.loves) or '无'}")
    hated = nd.hated_universal(n)
    print(f"  通用最爱：{'、'.join(i.name for i in nd.universal_loves if i not in hated)}"
          + (f"；讨厌 {'、'.join(i.name for i in hated)}（通用最爱的例外）" if hated else ""))
    print(f"  喜欢（专属）：{'、'.join(i.name for i in n.likes) or '无'}")
    if n.universal_likes_note:
        print(f"  通用喜欢（未逐项收录，wiki 原文）：{n.universal_likes_note}")
    print("  好感度：最爱 +80、喜欢 +45；生日当天 ×8（最爱 = 80 × 8 = 640）")


def cmd_birthdays(data: GameData, a) -> None:
    s = normalize_season(a.season)
    found = data.npc_data.birthdays_in_season(s)
    print(f"【{SEASON_ZH[s]}生日】 来源: data/npcs.json（收录 {len(data.npc_data.npcs)} 位 NPC）")
    for n in found:
        print(f"  第 {n.birthday_day} 天 {n.name}：最爱 {'、'.join(i.name for i in n.loves)}")
    if not found:
        print("  本季没有收录的 NPC 过生日")


def cmd_check_schedule(data: GameData, a) -> None:
    raw = json.loads(Path(a.file).read_text(encoding="utf-8"))
    raw = raw.get("daily_goals", raw)
    goals = {int(k): str(v) for k, v in raw.items()}
    s = normalize_season(a.season)

    warnings = check_plantings(goals, data, s, get_bonuses(a), a.year)
    if a.gifts:
        warnings += check_gifts(goals, data, s)
    fixed, inserted = enforce_season_end(goals)
    for w in warnings:
        print(f"⚠️ {w}")
    if inserted:
        print(f"⚠️ 第 {SEASON_DAYS} 天缺少“清理农场/停止种植”，修正为：{fixed[SEASON_DAYS]}")
    dying = "、".join(c.name for c in withering_crops(data, s, a.year))
    if dying:
        print(f"ℹ️ 进入{SEASON_ZH[next_season(s)]}时将枯死：{dying}")
    if not warnings and not inserted:
        print("✅ 跨季校验通过" + ("，送礼任务也已核对" if a.gifts else ""))
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

    def with_bonus_args(parser):
        parser.add_argument("--professions", default="", help="tiller,artisan,agriculturist（或中文名）")
        parser.add_argument("--fertilizer", default=None,
                            help="speed_gro / deluxe_speed_gro / hyper_speed_gro / basic_fertilizer / quality_fertilizer / deluxe_fertilizer")
        parser.add_argument("--farming-level", type=int, default=None, help="耕种等级 0-14；给出时计入作物品质")
        return parser

    with_bonus_args(sub.add_parser("crop")).add_argument("name")
    year_help = "游戏年份；给出时过滤掉当年买不到种子的作物（如第 1 年的大蒜）"
    pl = sub.add_parser("list")
    pl.add_argument("--season")
    pl.add_argument("--year", type=int, default=None, help=year_help)
    with_bonus_args(sub.add_parser("artisan")).add_argument("name")
    pp = with_bonus_args(sub.add_parser("profit"))
    pp.add_argument("name")
    pp.add_argument("--plant-day", type=int, default=1)
    pp.add_argument("--year", type=int, default=None, help=year_help)
    ps = with_bonus_args(sub.add_parser("simulate"))
    ps.add_argument("name")
    ps.add_argument("--season", required=True)
    ps.add_argument("--plant-day", type=int, default=1)
    ps.add_argument("--tiles", type=int, default=0, help="格数；给出时计算总收入与种子钱")
    pq = with_bonus_args(sub.add_parser("quality"))
    pq.add_argument("name")
    pq.add_argument("--processing-share", type=float, default=1.0, help="机器能加工的收获比例 0-1")
    sub.add_parser("npc").add_argument("name")
    sub.add_parser("birthdays").add_argument("season")
    pcal = sub.add_parser("calendar")
    pcal.add_argument("season")
    pcal.add_argument("--year", type=int, default=None, help=year_help)
    pc = with_bonus_args(sub.add_parser("check-schedule"))
    pc.add_argument("file")
    pc.add_argument("--season", required=True)
    pc.add_argument("--year", type=int, default=None, help=year_help)
    pc.add_argument("--fix", action="store_true", help="打印修正后的 28 天 JSON")
    pc.add_argument("--gifts", action="store_true", help="同时核对送礼任务（data/npcs.json）")
    a = p.parse_args()
    if getattr(a, "year", None) is not None and a.year < 1:
        p.error("年份必须 ≥ 1")

    data = load_game_data(PROJECT_ROOT / "data")
    globals()[f"cmd_{a.cmd.replace('-', '_')}"](data, a)


if __name__ == "__main__":
    main()
