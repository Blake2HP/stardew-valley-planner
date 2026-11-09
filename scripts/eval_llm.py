"""用真实大模型评估攻略生成的稳定性。

对一组预设用例（不同季节、玩法、加成）循环调用 generate_strategy，统计：
- 成功率（没有抛异常）、一次生成成功率（attempts == 1）、兜底解析次数；
- attempts（调用模型次数，> 1 说明第一次输出没通过校验）与 JSON 解析方式；
- season_warnings 的触发频率与具体内容（按类别归类）；送礼警告单独统计；
- finance.end_gold（期末资金，只算种植收支）。
结束后在终端打印 Markdown 统计表；可选把每次运行的原始结果存成 JSON。

不改动任何业务代码，只调用公开接口。

示例：
    python scripts/eval_llm.py                          # 默认 anthropic，每个用例跑 1 次
    python scripts/eval_llm.py --runs 3 --json eval.json
    python scripts/eval_llm.py --only spring_rush fall_wine --model claude-opus-5-5
    python scripts/eval_llm.py --provider mock          # 离线检查脚本本身（不消耗额度）
    python scripts/eval_llm.py --cases my_cases.json    # 自定义用例（格式同 DEFAULT_CASES）

注意：真实 provider 每次运行至少调用一次模型（失败时会重试），会产生 API 费用。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import sys
import time
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from stardew_planner import generate_strategy  # noqa: E402
from stardew_planner.data_loader import SEASON_ZH, load_game_data  # noqa: E402
from stardew_planner.generator import FALLBACK_GOAL  # noqa: E402

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except ImportError:
    pass


# ---------------------------------------------------------------------------
# 用例
# ---------------------------------------------------------------------------

# 每个用例：id、说明、generate_strategy 的参数。非第 1 年春季的用例给出 start_gold，期末资金才有绝对值可比。
DEFAULT_CASES: list[dict] = [
    {"id": "spring_rush", "label": "春季极限赚钱", "difficulty": "高",
     "playstyle": "种田极限赚钱，每一格地都要产出", "pacing": "速通极限", "season": "spring", "year": 1},
    {"id": "spring_casual_fish", "label": "春季休闲钓鱼", "difficulty": "低",
     "playstyle": "钓鱼为主，少量种田", "pacing": "休闲慢节奏", "season": "spring", "year": 1},
    {"id": "summer_corn_carry", "label": "夏季种玉米跨秋", "difficulty": "中",
     "playstyle": "种田为主，重视跨季作物", "pacing": "稳健", "season": "summer", "year": 1, "start_gold": 3000},
    {"id": "fall_wine", "label": "秋季酿酒", "difficulty": "中",
     "playstyle": "酿酒和加工为主，种蔓越莓和葡萄", "pacing": "稳健", "season": "fall", "year": 1,
     "start_gold": 8000, "professions": ["artisan"]},
    {"id": "winter_mine_collect", "label": "冬季挖矿全收集", "difficulty": "中",
     "playstyle": "挖矿与博物馆全收集，兼顾社区中心", "pacing": "速通极限", "season": "winter", "year": 1,
     "start_gold": 10000},
    {"id": "y2_spring_social", "label": "第 2 年春季社交送礼", "difficulty": "高",
     "playstyle": "社交优先，生日一定送最爱的礼物，顺便种大蒜", "pacing": "休闲慢节奏", "season": "spring", "year": 2,
     "start_gold": 20000},
    {"id": "fall_deluxe_quality", "label": "秋季顶级肥料品质农业", "difficulty": "高",
     "playstyle": "追求铱星作物，高星直接卖、低星加工", "pacing": "速通极限", "season": "fall", "year": 2,
     "start_gold": 30000, "professions": ["tiller"], "fertilizer": "deluxe_fertilizer", "farming_level": 10,
     "processing_share": 0.5},
]

CASE_PARAMS = ("playstyle", "pacing", "season", "year", "start_gold", "professions", "fertilizer",
               "farming_level", "processing_share")


def load_cases(path: str | None) -> list[dict]:
    if not path:
        return DEFAULT_CASES
    cases = json.loads(Path(path).read_text(encoding="utf-8"))
    for i, c in enumerate(cases):
        missing = [k for k in ("id", "playstyle", "pacing", "season") if k not in c]
        if missing:
            raise SystemExit(f"用例 {i} 缺少字段：{', '.join(missing)}")
    return cases


# ---------------------------------------------------------------------------
# 警告分类
# ---------------------------------------------------------------------------

# (类别, 正则, 是否算“问题”)。“固定提示”每季都会出现（如换季枯死提醒），不计入问题率。
WARNING_CATEGORIES: list[tuple[str, str, bool]] = [
    ("模型输出不合格（已用兜底结果）", r"已用兜底结果", True),
    ("来不及成熟", r"来不及成熟", True),
    ("种了非本季作物", r"不是.{1,4}作物", True),
    ("种子尚未开售（日期 / 年份）", r"才能买到|才开售|年起才出售", True),
    ("重复种植跨季作物", r"已从上季延续", True),
    ("第 1 年种了需要巴士的作物", r"修好巴士", True),
    ("种植清单条目无效", r"种植清单", True),
    ("现金不足", r"现金为", True),
    ("缺少第 28 天清理（已自动补上）", r"已自动在第 28 天加入", True),
    ("清理目标没写保留跨季作物（已补上）", r"已注明保留", True),
    ("肥料可自制（提示）", r"才在商店出售.*可以自制", False),
    ("肥料当时买不到", r"才在商店出售", True),
    ("换季枯死提醒（固定提示）", r"换季前一天", False),
    ("跨季作物提示（固定提示）", r"继续生长", False),
]
GIFT_CATEGORIES: list[tuple[str, str]] = [
    ("礼物不在喜好列表", r"不在 npcs\.json"),
    ("送了讨厌的通用最爱", r"讨厌"),
    ("送礼没写明礼物", r"没有写明礼物"),
    ("生日当天没送礼", r"生日当天没有送礼任务"),
]


def categorize(text: str) -> tuple[str, bool]:
    for name, pattern, is_problem in WARNING_CATEGORIES:
        if re.search(pattern, text):
            return name, is_problem
    return "其他", True


def categorize_gift(text: str) -> str:
    return next((name for name, pattern in GIFT_CATEGORIES if re.search(pattern, text)), "其他送礼问题")


# ---------------------------------------------------------------------------
# 运行
# ---------------------------------------------------------------------------

@dataclass
class RunRecord:
    case_id: str
    label: str
    run: int
    ok: bool
    seconds: float
    error: str = ""
    attempts: int | None = None
    json_parse_method: str = ""
    fallback_days: int = 0                      # 兜底解析填充的天数（> 0 说明重试后仍不合格）
    season_warnings: list[str] = field(default_factory=list)
    gift_warnings: list[str] = field(default_factory=list)
    end_gold: int | None = None
    end_gold_estimated: int | None = None
    plantings: int = 0

    @property
    def problem_warnings(self) -> list[str]:
        return [w for w in self.season_warnings if categorize(w)[1]]


def run_case(case: dict, run: int, args, data) -> RunRecord:
    kwargs = {k: case[k] for k in CASE_PARAMS if k in case}
    started = time.perf_counter()
    try:
        result = generate_strategy(
            provider=args.provider, model=args.model, data=data, max_retries=args.max_retries, **kwargs,
        )
    except Exception as e:  # 评估要记录所有失败，而不是中断
        return RunRecord(case["id"], case.get("label", case["id"]), run, False,
                         time.perf_counter() - started, error=f"{type(e).__name__}: {e}"[:300])
    finance = result.finance
    return RunRecord(
        case_id=case["id"], label=case.get("label", case["id"]), run=run, ok=True,
        seconds=time.perf_counter() - started,
        attempts=result.attempts, json_parse_method=result.json_parse_method,
        fallback_days=sum(1 for g in result.daily_goals.values() if g == FALLBACK_GOAL),
        season_warnings=list(result.season_warnings), gift_warnings=list(result.gift_warnings),
        end_gold=finance.end_gold if finance else None,
        end_gold_estimated=finance.end_gold_estimated if finance else None,
        plantings=len(result.plantings),
    )


# ---------------------------------------------------------------------------
# 报告
# ---------------------------------------------------------------------------

def pct(n: int, d: int) -> str:
    return f"{n / d:.0%}" if d else "-"


def fmt_gold(values: list[int]) -> str:
    if not values:
        return "-"
    if len(values) == 1:
        return f"{values[0]}g"
    return f"{min(values)} / {round(statistics.mean(values))} / {max(values)}g"


def cell(text: str, limit: int = 80) -> str:
    text = str(text).replace("|", "\\|").replace("\n", " ")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def build_report(records: list[RunRecord], cases: list[dict], args) -> str:
    total = len(records)
    ok = [r for r in records if r.ok]
    out = [f"# 模型稳定性评估（provider: {args.provider}"
           + (f"，model: {args.model}" if args.model else "") + f"，每个用例 {args.runs} 次）", ""]

    # ---- 总览 ----
    first_try = [r for r in ok if r.attempts == 1]
    fallback = [r for r in ok if r.fallback_days]
    with_problem = [r for r in ok if r.problem_warnings]
    with_gift = [r for r in ok if r.gift_warnings]
    methods = Counter(r.json_parse_method for r in ok)
    out += [
        "## 总览", "",
        "| 指标 | 数值 |", "|---|---|",
        f"| 运行次数 | {total}（{len(cases)} 个用例 × {args.runs} 次） |",
        f"| 成功率（没有抛异常） | {pct(len(ok), total)}（{len(ok)}/{total}） |",
        f"| 一次生成成功率（attempts = 1） | {pct(len(first_try), total)}（{len(first_try)}/{total}） |",
        f"| 平均 attempts | {statistics.mean(r.attempts for r in ok):.2f} |" if ok else "| 平均 attempts | - |",
        f"| 重试后仍不合格、走兜底解析 | {len(fallback)} 次 |",
        "| JSON 解析方式 | " + ("、".join(f"{m} {n}" for m, n in methods.most_common()) or "-") + " |",
        f"| 触发问题类 season_warnings 的运行 | {pct(len(with_problem), len(ok))}（{len(with_problem)}/{len(ok)}） |",
        f"| 触发送礼警告的运行 | {pct(len(with_gift), len(ok))}（{len(with_gift)}/{len(ok)}） |",
        f"| 平均耗时 | {statistics.mean(r.seconds for r in records):.1f} 秒 |" if records else "| 平均耗时 | - |",
        "",
    ]

    # ---- 每个用例 ----
    out += ["## 各用例", "",
            "| 用例 | 季节 | 难度 | 成功 | attempts | 问题警告（平均条数） | 送礼警告 | 期末资金 min/avg/max | 种植条目 | 平均耗时 |",
            "|---|---|:---:|:---:|:---:|:---:|:---:|---:|:---:|---:|"]
    for c in cases:
        rs = [r for r in records if r.case_id == c["id"]]
        if not rs:
            continue
        good = [r for r in rs if r.ok]
        season = f"第 {c.get('year', 1)} 年{SEASON_ZH.get(c['season'], c['season'])}"
        attempts = "、".join(str(r.attempts) for r in good) or "-"
        problems = f"{statistics.mean(len(r.problem_warnings) for r in good):.1f}" if good else "-"
        gifts = f"{statistics.mean(len(r.gift_warnings) for r in good):.1f}" if good else "-"
        golds = [r.end_gold for r in good if r.end_gold is not None]
        plant = f"{statistics.mean(r.plantings for r in good):.1f}" if good else "-"
        out.append(f"| {c.get('label', c['id'])} | {season} | {c.get('difficulty', '-')} | {len(good)}/{len(rs)} | "
                   f"{attempts} | {problems} | {gifts} | {fmt_gold(golds)} | {plant} | "
                   f"{statistics.mean(r.seconds for r in rs):.1f}s |")
    out.append("")

    # ---- 警告分类 ----
    counts: Counter = Counter()
    runs_hit: dict[str, set] = {}
    examples: dict[str, str] = {}
    for r in ok:
        for w in r.season_warnings:
            name, _ = categorize(w)
            counts[name] += 1
            runs_hit.setdefault(name, set()).add((r.case_id, r.run))
            examples.setdefault(name, f"[{r.label}] {w}")
        for w in r.gift_warnings:
            name = "送礼：" + categorize_gift(w)
            counts[name] += 1
            runs_hit.setdefault(name, set()).add((r.case_id, r.run))
            examples.setdefault(name, f"[{r.label}] {w}")
    out += ["## 警告分类（season_warnings 与送礼警告）", ""]
    if counts:
        out += ["| 类别 | 条数 | 涉及运行占比 | 示例 |", "|---|:---:|:---:|---|"]
        for name, n in counts.most_common():
            out.append(f"| {name} | {n} | {pct(len(runs_hit[name]), len(ok))} | {cell(examples[name], 120)} |")
    else:
        out.append("（没有任何警告）")
    out.append("")

    # ---- 具体问题警告 ----
    detail = [(r, w) for r in ok for w in r.problem_warnings + r.gift_warnings]
    if detail:
        out += ["## 问题警告明细", "", "| 用例 | 第几次 | 警告 |", "|---|:---:|---|"]
        out += [f"| {r.label} | {r.run} | {cell(w, 160)} |" for r, w in detail]
        out.append("")

    # ---- 失败 ----
    failed = [r for r in records if not r.ok]
    if failed:
        out += ["## 失败", "", "| 用例 | 第几次 | 错误 |", "|---|:---:|---|"]
        out += [f"| {r.label} | {r.run} | {cell(r.error, 160)} |" for r in failed]
        out.append("")

    out.append("> 期末资金只算种植收支（程序计算），非第 1 年春季的用例使用各自的 start_gold；"
               "“固定提示”类警告每季都会出现，不计入问题率。")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="评估真实大模型生成攻略的稳定性")
    parser.add_argument("--provider", default="anthropic", help="anthropic / openai / mock（默认 anthropic）")
    parser.add_argument("--model", default=None, help="覆盖默认模型名")
    parser.add_argument("--runs", type=int, default=1, help="每个用例重复运行的次数（默认 1）")
    parser.add_argument("--only", nargs="+", default=None, metavar="ID", help="只跑这些用例 id")
    parser.add_argument("--cases", default=None, help="自定义用例 JSON 文件（列表，字段同 DEFAULT_CASES）")
    parser.add_argument("--max-retries", type=int, default=1, help="校验失败后让模型重试的次数（默认 1）")
    parser.add_argument("--json", default=None, help="把每次运行的原始结果保存为 JSON")
    parser.add_argument("--list", action="store_true", help="只列出用例，不运行")
    args = parser.parse_args(argv)

    cases = load_cases(args.cases)
    if args.only:
        unknown = set(args.only) - {c["id"] for c in cases}
        if unknown:
            parser.error(f"未知用例：{', '.join(sorted(unknown))}；可选：{', '.join(c['id'] for c in cases)}")
        cases = [c for c in cases if c["id"] in args.only]
    if args.list:
        for c in cases:
            print(f"{c['id']}\t{c.get('label', '')}\t{c['season']} 第 {c.get('year', 1)} 年\t{c['playstyle']} / {c['pacing']}")
        return 0
    if args.runs < 1:
        parser.error("--runs 必须 ≥ 1")
    key_env = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY"}.get(args.provider.lower())
    if key_env and not os.getenv(key_env):
        parser.error(f"没有找到环境变量 {key_env}（可以写在项目根目录的 .env 里），或者用 --provider mock 离线检查脚本")

    data = load_game_data()
    records: list[RunRecord] = []
    total = len(cases) * args.runs
    print(f"开始评估：{len(cases)} 个用例 × {args.runs} 次 = {total} 次运行（provider: {args.provider}）", file=sys.stderr)
    try:
        for run in range(1, args.runs + 1):
            for c in cases:
                rec = run_case(c, run, args, data)
                records.append(rec)
                status = (f"attempts={rec.attempts}，问题警告 {len(rec.problem_warnings)} 条，期末 {rec.end_gold}g"
                          if rec.ok else f"失败：{rec.error}")
                print(f"[{len(records)}/{total}] {rec.label} 第 {run} 次（{rec.seconds:.1f}s）：{status}", file=sys.stderr)
    except KeyboardInterrupt:
        print("\n已中断，下面是已完成部分的统计。", file=sys.stderr)

    if not records:
        return 1
    print(build_report(records, cases, args))
    if args.json:
        Path(args.json).write_text(
            json.dumps([{**asdict(r), "problem_warnings": r.problem_warnings} for r in records],
                       ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"\n原始结果已保存到 {args.json}", file=sys.stderr)
    return 0 if all(r.ok for r in records) else 1


if __name__ == "__main__":
    sys.exit(main())
