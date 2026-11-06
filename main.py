"""命令行入口。

示例：
    python main.py --playstyle "钓鱼赚钱" --pacing "休闲慢节奏" --season spring --provider mock
    python main.py -p "种田为主，兼顾社区中心" -c "速通极限" -s summer --provider anthropic -o guide.md
    python main.py --professions artisan --fertilizer speed_gro --provider mock
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from stardew_planner import generate_strategy
from stardew_planner.bonuses import resolve_bonuses

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
    parser.add_argument("-s", "--season", default="spring", help="spring/summer/fall/winter 或 春季/夏季…")
    parser.add_argument("-y", "--year", type=int, default=1)
    parser.add_argument("--provider", default=None, help="anthropic / openai / mock（默认读 LLM_PROVIDER）")
    parser.add_argument("--model", default=None, help="覆盖默认模型名")
    parser.add_argument("-o", "--output", default=None, help="同时保存 Markdown 到文件")
    parser.add_argument("--professions", default="",
                        help="职业，逗号分隔：tiller（农耕人）、artisan（工匠）、agriculturist（农业学家）；"
                             "选工匠或农业学家会自动加入农耕人")
    parser.add_argument("--fertilizer", default=None,
                        help="加速肥料：speed_gro（生长激素）、deluxe_speed_gro（高级）、hyper_speed_gro（顶级）")
    args = parser.parse_args()

    try:
        bonuses = resolve_bonuses([args.professions], args.fertilizer)
    except ValueError as e:
        parser.error(str(e))

    result = generate_strategy(
        args.playstyle, args.pacing, args.season, args.year,
        provider=args.provider, model=args.model,
        professions=[p.key for p in bonuses.professions],
        fertilizer=bonuses.fertilizer.key if bonuses.fertilizer else None,
    )

    for note in bonuses.notes:
        print(f"[提示] {note}", file=sys.stderr)
    print(result.markdown)
    print(f"\n[调用模型 {result.attempts} 次，JSON 解析方式: {result.json_parse_method}]", file=sys.stderr)

    if args.output:
        Path(args.output).write_text(result.markdown, encoding="utf-8")
        print(f"\n已保存到 {args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()
