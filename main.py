"""命令行入口。

示例：
    python main.py --playstyle "钓鱼赚钱" --pacing "休闲慢节奏" --season spring --provider mock
    python main.py -p "种田为主，兼顾社区中心" -c "速通极限" -s summer --provider anthropic -o guide.md
    python main.py --professions artisan --fertilizer speed_gro --provider mock
    python main.py --season spring,summer,fall --provider mock    # 跨季连续规划
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from stardew_planner import generate_plan, generate_strategy
from stardew_planner.bonuses import resolve_bonuses
from stardew_planner.data_loader import SEASON_ZH
from stardew_planner.generator import parse_seasons

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass


def main() -> None:
    for stream in (sys.stdout, sys.stderr):  # Windows 控制台中文/emoji
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="星露谷物语最佳攻略生成器")
    parser.add_argument("-p", "--playstyle", default="钓鱼赚钱，兼顾种田", help="玩法风格")
    parser.add_argument("-c", "--pacing", default="休闲慢节奏", help="节奏偏好")
    parser.add_argument("-s", "--season", default="spring",
                        help="spring/summer/fall/winter 或 春季/夏季…；逗号分隔多个连续季节做跨季规划，如 spring,summer")
    parser.add_argument("-y", "--year", type=int, default=1, help="游戏年份；第 1 年不计入第 2 年起才出售种子的作物")
    parser.add_argument("--provider", default=None, help="anthropic / mock（默认读 LLM_PROVIDER）")
    parser.add_argument("--model", default=None, help="覆盖默认模型名")
    parser.add_argument("-o", "--output", default=None, help="同时保存 Markdown 到文件")
    parser.add_argument("--farm-type", default=None,
                        help="农场地图：standard / riverland / forest / hilltop / wilderness / four_corners / beach / "
                             "meadowlands（或中文名）。草原农场开局没有赠送的防风草种子")
    parser.add_argument("--revise-rounds", type=int, default=1,
                        help="自动修正轮数：程序校验发现问题时反馈给模型修改（默认 1，0 = 关闭；会多调用模型）")
    parser.add_argument("--start-gold", type=int, default=None,
                        help="期初资金；不传时第 1 年春季用开局金币（data/farm.json），其他季节为未知")
    parser.add_argument("--professions", default="",
                        help="职业，逗号分隔：tiller（农耕人）、artisan（工匠）、agriculturist（农业学家）；"
                             "选工匠或农业学家会自动加入农耕人")
    parser.add_argument("--farming-level", type=int, default=None,
                        help="耕种等级 0-14；给出（或用了品质肥料）时按品质概率计算收益期望值")
    parser.add_argument("--processing-share", type=float, default=1.0,
                        help="机器能加工的收获比例 0-1（默认 1 = 机器充足）；不足时自动算出“高星直接卖、低星加工”")
    parser.add_argument("--fertilizer", default=None,
                        help="肥料（每格只能一种）：加速 speed_gro / deluxe_speed_gro / hyper_speed_gro，品质 basic_fertilizer / quality_fertilizer / deluxe_fertilizer")
    args = parser.parse_args()
    if args.year < 1:
        parser.error("年份必须 ≥ 1")
    if not 0 <= args.processing_share <= 1:
        parser.error("加工比例必须在 0-1 之间")
    if args.revise_rounds < 0:
        parser.error("自动修正轮数必须 ≥ 0")
    if args.start_gold is not None and args.start_gold < 0:
        parser.error("期初资金必须 ≥ 0")
    try:
        seasons = parse_seasons(args.season, args.year)
    except ValueError as e:
        parser.error(str(e))

    try:
        bonuses = resolve_bonuses([args.professions], args.fertilizer, farming_level=args.farming_level)
    except ValueError as e:
        parser.error(str(e))

    common = dict(
        provider=args.provider, model=args.model, start_gold=args.start_gold,
        professions=[p.key for p in bonuses.professions],
        fertilizer=bonuses.fertilizer.key if bonuses.fertilizer else None,
        farming_level=args.farming_level, processing_share=args.processing_share,
        revise_rounds=args.revise_rounds, farm_type=args.farm_type,
    )
    if len(seasons) > 1:
        result = generate_plan(args.playstyle, args.pacing, [s for s, _ in seasons], args.year, **common)
        failed = result.failed_season
        attempts = "、".join(str(r.attempts) for r in result.results)
        methods = "、".join(r.json_parse_method for r in result.results)
    else:
        result = generate_strategy(args.playstyle, args.pacing, seasons[0][0], args.year, **common)
        failed = None
        attempts, methods = result.attempts, result.json_parse_method

    for note in bonuses.notes:
        print(f"[提示] {note}", file=sys.stderr)
    print(result.markdown)
    print(f"\n[调用模型 {attempts} 次，JSON 解析方式: {methods}]", file=sys.stderr)

    if args.output:
        Path(args.output).write_text(result.markdown, encoding="utf-8")
        print(f"\n已保存到 {args.output}", file=sys.stderr)
    if failed is not None:
        print(f"\n[规划中断] 第 {failed[1]} 年{SEASON_ZH[failed[0]]} 生成失败：{result.error}；已输出完成的部分。", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
