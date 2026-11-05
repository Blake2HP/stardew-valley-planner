"""命令行入口。

示例：
    python main.py --playstyle "钓鱼赚钱" --pacing "休闲慢节奏" --season spring --provider mock
    python main.py -p "种田为主，兼顾社区中心" -c "速通极限" -s summer --provider anthropic -o guide.md
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from stardew_planner import generate_strategy

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass


def main() -> None:
    parser = argparse.ArgumentParser(description="星露谷物语最佳攻略生成器")
    parser.add_argument("-p", "--playstyle", default="钓鱼赚钱，兼顾种田", help="玩法风格")
    parser.add_argument("-c", "--pacing", default="休闲慢节奏", help="节奏偏好")
    parser.add_argument("-s", "--season", default="spring", help="spring/summer/fall/winter 或 春季/夏季…")
    parser.add_argument("-y", "--year", type=int, default=1)
    parser.add_argument("--provider", default=None, help="anthropic / openai / mock（默认读 LLM_PROVIDER）")
    parser.add_argument("--model", default=None, help="覆盖默认模型名")
    parser.add_argument("-o", "--output", default=None, help="同时保存 Markdown 到文件")
    args = parser.parse_args()

    result = generate_strategy(
        args.playstyle, args.pacing, args.season, args.year,
        provider=args.provider, model=args.model,
    )

    for stream in (sys.stdout, sys.stderr):  # Windows 控制台中文/emoji
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    print(result.markdown)

    if args.output:
        Path(args.output).write_text(result.markdown, encoding="utf-8")
        print(f"\n已保存到 {args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()
