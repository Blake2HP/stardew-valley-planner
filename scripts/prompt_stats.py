"""测量发给模型的 Prompt 有多长：按场景、按【数据参考】的章节统计，找出最占地方的部分。

用 mock 跑 generate_strategy，记录第一次调用时的系统 Prompt 和用户 Prompt（不消耗 API 额度）。
加 --count-tokens 时再用 Anthropic 的 count_tokens 接口数一遍 token（需要 API 凭证，count_tokens 本身免费）；
不加时只报字符数。

还会报告两个和缓存有关的数字：
- 同一次生成里的重试 / 自动修正：系统 Prompt 和第一条用户消息原样重发，都能命中缓存；
- 不同请求之间：用户 Prompt 和第一个场景共同前缀有多长（前缀越长，换个玩法也能复用的越多）。

示例：
    python scripts/prompt_stats.py
    python scripts/prompt_stats.py --count-tokens --model claude-opus-5-5
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from stardew_planner import generate_strategy  # noqa: E402
from stardew_planner.data_loader import load_game_data  # noqa: E402
from stardew_planner.llm_client import MockClient  # noqa: E402

SCENARIOS = {
    "基础（春季）": dict(season="spring"),
    "换玩法（春季）": dict(season="spring", playstyle="全收集，社区中心优先", pacing="速通极限"),
    "夏季 + 职业肥料": dict(season="summer", professions=["artisan"], farming_level=10, fertilizer="deluxe_fertilizer"),
    "全功能（秋季）": dict(season="fall", kegs=10, jars=5, bee_houses=3, greenhouse=True, animals="鸡:2,牛:1",
                       route="community_center", players=3, roles="P1:种田,P2:下矿,P3:钓鱼",
                       achievements="混合栽培,万人迷,千万富翁"),
    "季中第 15 天": dict(season="spring", start_day=15, start_gold=1000),
}


class Recorder(MockClient):
    def __init__(self):
        super().__init__()
        self.calls = []

    def complete(self, system, messages):
        self.calls.append((system, [dict(m) for m in messages]))
        return super().complete(system, messages)


def sections(prompt: str) -> list[tuple[str, int]]:
    """按“## / ### 标题”切开用户 Prompt，返回 (标题, 字符数)。"""
    parts = re.split(r"(?m)^(#{2,3} .+)$", prompt)
    out = [("（开头）", len(parts[0]))]
    for i in range(1, len(parts), 2):
        out.append((parts[i].lstrip("# ").strip(), len(parts[i]) + len(parts[i + 1])))
    return out


def common_prefix(a: str, b: str) -> int:
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    return n


def count_tokens(client, model: str, system: str, messages: list) -> int:
    return client.messages.count_tokens(model=model, system=system, messages=messages).input_tokens


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--count-tokens", action="store_true", help="用 count_tokens 接口数 token（需要 API 凭证）")
    parser.add_argument("--model", default=os.getenv("ANTHROPIC_MODEL", "claude-opus-5-5"))
    args = parser.parse_args()

    api = None
    if args.count_tokens:
        import anthropic
        api = anthropic.Anthropic()

    data = load_game_data()
    captured = {}
    for name, kw in SCENARIOS.items():
        kw = dict(kw)
        rec = Recorder()
        generate_strategy(kw.pop("playstyle", "钓鱼赚钱，兼顾种田"), kw.pop("pacing", "休闲慢节奏"), client=rec,
                          data=data, revise_rounds=1, **kw)
        system, messages = rec.calls[0]
        captured[name] = (system, messages[0]["content"], len(rec.calls))

    base_user = captured["基础（春季）"][1]
    print("| 场景 | 系统 Prompt（字） | 用户 Prompt（字） | 合计（字） | 和“基础”共同前缀（字） | 调用次数 |"
          + (" token |" if api else ""))
    print("|---|---:|---:|---:|---:|---:|" + ("---:|" if api else ""))
    for name, (system, user, calls) in captured.items():
        row = (f"| {name} | {len(system):,} | {len(user):,} | {len(system) + len(user):,} | "
               f"{common_prefix(base_user, user):,} | {calls} |")
        if api:
            row += f" {count_tokens(api, args.model, system, [{'role': 'user', 'content': user}]):,} |"
        print(row)

    name = "全功能（秋季）"
    print(f"\n### “{name}”的用户 Prompt 各章节（字符数，从大到小）\n")
    print("| 章节 | 字 | 占比 |")
    print("|---|---:|---:|")
    user = captured[name][1]
    for title, n in sorted(sections(user), key=lambda x: -x[1]):
        print(f"| {title} | {n:,} | {n / len(user):.0%} |")


if __name__ == "__main__":
    main()
