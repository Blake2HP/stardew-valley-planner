"""命令行入口。

示例：
    python main.py --playstyle "钓鱼赚钱" --pacing "休闲慢节奏" --season spring --provider mock
    python main.py -p "种田为主，兼顾社区中心" -c "速通极限" -s summer --provider anthropic -o guide.md
    python main.py --professions artisan --fertilizer speed_gro --provider mock
    python main.py --season spring,summer,fall --provider mock    # 跨季连续规划
    python main.py --save "%APPDATA%/StardewValley/Saves/Name_123456789/Name_123456789" --provider mock  # 导入存档
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from stardew_planner import generate_plan, generate_strategy
from stardew_planner.bonuses import resolve_bonuses
from stardew_planner.data_loader import SEASON_ZH
from stardew_planner.generator import parse_seasons
from stardew_planner.playstyles import describe_presets, load_presets

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass


# 存档能填的参数：argparse 的 dest → SaveState.generator_kwargs() 里的键
SAVE_FIELDS = {
    "season": "season", "year": "year", "start_day": "start_day", "start_gold": "start_gold",
    "farm_type": "farm_type", "farming_level": "farming_level", "professions": "professions",
    "greenhouse": "greenhouse", "route": "route", "kegs": "kegs", "jars": "jars", "bee_houses": "bee_houses",
    "owned_animals": "owned_animals", "achievement_progress": "achievement_progress", "in_ground": "in_ground",
}


def explicit_dests(parser: argparse.ArgumentParser, argv: list[str]) -> set[str]:
    """命令行里显式写了的参数（dest）。支持 --opt value、--opt=value、-s value、-svalue。"""
    out = set()
    for action in parser._actions:
        for opt in action.option_strings:
            if any(a == opt or a.startswith(opt + "=") or (len(opt) == 2 and a.startswith(opt) and not a.startswith("--"))
                   for a in argv):
                out.add(action.dest)
    return out


def apply_save(args: argparse.Namespace, state, explicit: set[str]) -> list[str]:
    """用存档的值填参数；命令行显式写了的以命令行为准。返回说明（哪些被命令行覆盖）。"""
    values = state.generator_kwargs()
    notes = []
    for dest, key in SAVE_FIELDS.items():
        if key not in values:
            continue
        if dest in explicit:
            notes.append(f"--{dest.replace('_', '-')} 以命令行为准（存档里是 {_show(values[key])}）")
            continue
        value = values[key]
        if dest == "professions":
            value = ",".join(value)
        setattr(args, dest, value)
    # 季节或年份换了：存档里的日期和地里的作物都不适用（起始日没写时从第 1 天开始）
    if {"season", "year"} & explicit:
        first = args.season.replace("，", ",").split(",")[0].strip()
        try:
            from stardew_planner.data_loader import normalize_season
            same = normalize_season(first) == state.season and args.year == state.year
        except ValueError:
            same = False
        if not same:
            if "start_day" not in explicit and "start_day" in values:
                args.start_day = 1
                notes.append("季节或年份和存档不同，从第 1 天开始规划")
            if getattr(args, "in_ground", None):
                args.in_ground = None
                notes.append("季节或年份和存档不同，存档里地里的作物不导入")
    return notes


def _show(value) -> str:
    if isinstance(value, (list, tuple)):
        return ",".join(map(str, value))
    if isinstance(value, dict):
        return ",".join(f"{k}:{v}" for k, v in value.items())
    if hasattr(value, "shipped"):
        return "存档里的出货和好感度"
    return str(value)


def main(argv: list[str] | None = None) -> None:
    for stream in (sys.stdout, sys.stderr):  # Windows 控制台中文/emoji
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")

    parser = build_parser()
    argv = sys.argv[1:] if argv is None else argv
    args = parser.parse_args(argv)
    run(parser, args, argv)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="星露谷物语最佳攻略生成器")
    parser.add_argument("--save", default=None,
                        help="导入《星露谷物语》1.6 主存档（存档文件夹里和文件夹同名的文件，不是 SaveGameInfo）："
                             "自动填入季节、日期、年份、现金、农场地图、耕种等级、职业、温室、路线、机器、动物和成就进度；"
                             "命令行另外写了的参数以命令行为准")
    parser.add_argument("-p", "--playstyle", default="钓鱼赚钱，兼顾种田",
                        help="玩法风格，可以自己写；用 --list-playstyles 查看预设玩法和各自的优劣势")
    parser.add_argument("-c", "--pacing", default="休闲慢节奏", help="节奏偏好，可以自己写；预设见 --list-playstyles")
    parser.add_argument("--list-playstyles", action="store_true",
                        help="列出预设的玩法风格和节奏偏好，以及各自的优劣势，然后退出")
    parser.add_argument("--list-achievements", action="store_true",
                        help="按类别列出全部 49 个 Steam 成就，注明哪些由程序算缺口，然后退出")
    parser.add_argument("-s", "--season", default="spring",
                        help="spring/summer/fall/winter 或 春季/夏季…；逗号分隔多个连续季节做跨季规划，如 spring,summer")
    parser.add_argument("-y", "--year", type=int, default=1, help="游戏年份；第 1 年不计入第 2 年起才出售种子的作物")
    parser.add_argument("--start-day", type=int, default=1,
                        help="季中重新规划：今天是本季第几天（1-28，默认 1），只规划这天到第 28 天；"
                             "跨季规划时只有第一个季节从这天开始。第 1 年春季从第 2 天起不再默认开局金币和赠送的种子，"
                             "期初资金请用 --start-gold 给出")
    parser.add_argument("--provider", default=None, help="anthropic / mock（默认读 LLM_PROVIDER）")
    parser.add_argument("--model", default=None, help="覆盖默认模型名")
    parser.add_argument("-o", "--output", default=None, help="同时保存 Markdown 到文件")
    parser.add_argument("--kegs", type=int, default=0, help="小桶数量；给出时按机器数量和加工时间模拟加工，计入资金")
    parser.add_argument("--jars", type=int, default=0, help="罐头瓶数量")
    parser.add_argument("--players", type=int, default=1, choices=range(1, 5), metavar="1-4",
                        help="联机人数 1-4（默认 1 = 单人）；多人时日程按玩家 P1-P4 分开写，P1 为房主")
    parser.add_argument("--roles", default=None,
                        help="联机分工，如 \"P1:种田, P2:下矿, P3:钓鱼\"；编号不能超过 --players")
    parser.add_argument("--achievements", default=None,
                        help="想完成的 Steam 成就，逗号分隔，如 \"混合栽培,万人迷\"（data/achievements.json）；"
                             "用 --list-achievements 查看全部；赚钱、友谊、出货类由程序算缺口并校验，其他只写进 Prompt")
    parser.add_argument("--achievement-progress", default=None,
                        help="已有的成就进度，如 \"防风草=15,万人迷=12,千万富翁=50000\"："
                             "作物 = 已运送个数，万人迷 = 已 5 心的人数，富翁 = 已累计赚取的金币；不给时从零算")
    parser.add_argument("--location", default="pelican_town", choices=["pelican_town", "ginger_island"],
                        help="种植地点：ginger_island = 姜岛农场，任何作物任何季节都能种、换季不枯死")
    parser.add_argument("--route", default=None, choices=["community_center"],
                        help="游戏路线：community_center = 社区中心献祭，收集包要用的作物先留下、不卖，没留下时报献祭校验")
    parser.add_argument("--animals", default=None,
                        help="本季第 1 天新买的动物，如 \"鸡:2,牛:1\"（鸡、鸭、兔子、牛、山羊、绵羊、猪）；产出和成本计入资金")
    parser.add_argument("--owned-animals", default=None,
                        help="已经养着的成年动物，写法同 --animals；不计购买费，从第 1 天起就有产出（导入存档时自动填）")
    parser.add_argument("--bee-houses", type=int, default=0,
                        help="室外蜂房数量；每 4 天一罐，按室外开着的最值钱的花算花蜜，计入资金（冬季不产）")
    parser.add_argument("--greenhouse", action="store_true",
                        help="温室已修好：可以把作物种在温室（不受季节限制，可再生作物不会枯死，最多 120 格）")
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
    return parser


def run(parser: argparse.ArgumentParser, args: argparse.Namespace, argv: list[str]) -> None:
    if args.save:
        from stardew_planner.data_loader import load_game_data
        from stardew_planner.save_parser import SaveParseError, parse_save
        try:
            state = parse_save(args.save, load_game_data())
        except SaveParseError as e:
            parser.error(f"存档导入失败：{e}")
        args.in_ground = None
        notes = apply_save(args, state, explicit_dests(parser, argv))
        print("[存档解析摘要]", file=sys.stderr)
        for line in state.summary_lines(load_game_data()):
            print(f"  - {line}", file=sys.stderr)
        for line in notes:
            print(f"  - {line}", file=sys.stderr)
        for w in state.warnings:
            print(f"  ⚠️ {w}", file=sys.stderr)
    if args.list_playstyles:
        print(describe_presets(load_presets()), end="")
        return
    if args.list_achievements:
        from stardew_planner.achievements import list_achievements, load_achievement_rules
        print(list_achievements(load_achievement_rules()), end="")
        return
    if args.year < 1:
        parser.error("年份必须 ≥ 1")
    if not 1 <= args.start_day <= 28:
        parser.error("起始日必须在 1-28 之间")
    if not 0 <= args.processing_share <= 1:
        parser.error("加工比例必须在 0-1 之间")
    if args.kegs < 0 or args.jars < 0 or args.bee_houses < 0:
        parser.error("机器数量不能为负数")
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

    try:  # 成就名和进度写错时直接报参数错误
        from stardew_planner.achievements import load_achievement_rules, parse_progress, resolve_goals
        from stardew_planner.data_loader import DEFAULT_DATA_DIR, load_crops
        rules = load_achievement_rules()
        resolve_goals(args.achievements, rules)
        if isinstance(args.achievement_progress, str):  # 存档导入的是已经解析好的进度
            parse_progress(args.achievement_progress, rules, load_crops(DEFAULT_DATA_DIR / "crops.json"))
    except ValueError as e:
        parser.error(str(e))

    common = dict(
        provider=args.provider, model=args.model, start_gold=args.start_gold,
        professions=[p.key for p in bonuses.professions],
        fertilizer=bonuses.fertilizer.key if bonuses.fertilizer else None,
        farming_level=args.farming_level, processing_share=args.processing_share,
        revise_rounds=args.revise_rounds, farm_type=args.farm_type, kegs=args.kegs, jars=args.jars,
        greenhouse=args.greenhouse, bee_houses=args.bee_houses, animals=args.animals,
        route=args.route, location=args.location, players=args.players, roles=args.roles,
        achievements=args.achievements, achievement_progress=args.achievement_progress,
        start_day=args.start_day, owned_animals=args.owned_animals, in_ground=getattr(args, "in_ground", None),
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
