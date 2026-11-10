"""核心攻略生成：数据 → Prompt → 大模型 → 解析校验 → 合并日程 → Markdown。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from .analysis import SEASON_DAYS, build_data_context
from .bonuses import NO_BONUSES, Bonuses, fertilizer_availability_note, resolve_bonuses
from .data_loader import SEASON_ZH, GameData, GuideStore, load_game_data, normalize_season, search_guides
from .farm_state import (
    FarmState, OtherIncome, Planting, SeasonFinance, compute_finance, extract_farm_state,
    bus_warnings, following_season, late_planting_warnings, render_state_for_prompt, resolve_plantings,
)
from .json_utils import JSONParseError, ParsedJSON, parse_llm_json
from .llm_client import LLMClient, TruncatedOutput, get_client
from .prompts import REPAIR_PROMPT_TEMPLATE, REVISE_PROMPT_TEMPLATE, SYSTEM_PROMPT, build_user_prompt
from .renderer import render_markdown
from .schedule import ScheduleBlock, merge_daily_goals
from .season_rules import (
    check_gifts, check_plantings, check_plantings_match, enforce_season_end, next_season, withering_crops,
)

FALLBACK_GOAL = "自由安排"
TRUNCATED_ERROR = "输出太长被截断了：请精简 pillars、key_milestones 和各字段的文字，输出完整的 JSON"


class StrategyParseError(ValueError):
    pass


@dataclass
class StrategyResult:
    playstyle: str
    pacing: str
    season: str
    year: int
    macro_strategy: dict
    key_milestones: list[dict]
    daily_goals: dict[int, str]
    schedule: list[ScheduleBlock] = field(default_factory=list)
    season_warnings: list[str] = field(default_factory=list)
    gift_warnings: list[str] = field(default_factory=list)  # 送礼校验（data/npcs.json）
    bonuses: Bonuses = NO_BONUSES  # 职业与肥料加成
    markdown: str = ""
    raw_response: str = ""
    guide_excerpts: str = ""     # 检索到并放进 Prompt 的攻略片段
    attempts: int = 1            # 调用模型的次数（1 = 没有重试）
    json_parse_method: str = ""  # strict / cleaned / repaired
    processing_share: float = 1.0  # 机器能加工的收获比例（出售策略用）
    plantings: list[Planting] = field(default_factory=list)       # 种植清单（已剔除不合法条目）
    other_income: list[OtherIncome] = field(default_factory=list)  # 模型估算的其他收入
    finance: SeasonFinance | None = None   # 本季资金预期（程序计算）
    prior_state: FarmState | None = None   # 上季延续状态（第一季为 None）
    end_state: FarmState | None = None     # 本季结束时的农场状态
    problems: list[str] = field(default_factory=list)          # 最终版本里需要修正的问题（警告的子集）
    initial_problems: list[str] = field(default_factory=list)  # 自动修正之前的问题
    revisions: list[dict] = field(default_factory=list)  # 每轮自动修正：问题数前后、是否采用


# ---------------------------------------------------------------------------
# 解析与校验
# ---------------------------------------------------------------------------

def extract_json(text: str) -> ParsedJSON:
    """容错解析模型输出（严格解析 → 常见错误清理 → json_repair），失败时抛出 StrategyParseError。"""
    try:
        return parse_llm_json(text, expect=dict)
    except JSONParseError as e:
        raise StrategyParseError(str(e)) from e


@dataclass
class ParsedStrategy:
    macro: dict
    milestones: list[dict]
    goals: dict[int, str]
    plantings: list[dict] = field(default_factory=list)  # {"day", "crop", "tiles"}
    other_income: list[OtherIncome] = field(default_factory=list)


def _parse_plantings(raw, errors: list[str] | None) -> list[dict]:
    """校验 plantings。errors 为 None 时是兜底模式：只保留合法条目，不报错。"""
    if not isinstance(raw, list):
        if errors is not None:
            errors.append("plantings 缺失或不是数组（本季不种植请写 []）")
        return []
    items = []
    for i, item in enumerate(raw):
        try:
            day, tiles = int(item["day"]), int(item["tiles"])
            crop = str(item["crop"]).strip()
            if not (1 <= day <= SEASON_DAYS and tiles >= 1 and crop):
                raise ValueError
        except (TypeError, KeyError, ValueError):
            if errors is not None:
                errors.append(f"plantings[{i}] 需要 day（1-28）、crop（作物名）、tiles（正整数）: {item!r}")
            continue
        items.append({"day": day, "crop": crop, "tiles": tiles})
    return items


def _parse_other_income(raw) -> list[OtherIncome]:
    """模型估算的其他收入：可选字段，不合法的条目直接丢弃。"""
    out = []
    for item in raw if isinstance(raw, list) else []:
        try:
            gold, source = int(item["gold"]), str(item["source"]).strip()
        except (TypeError, KeyError, ValueError):
            continue
        if gold >= 0 and source:
            out.append(OtherIncome(source, gold, str(item.get("note") or "").strip()))
    return out


def validate_payload(payload: dict) -> ParsedStrategy:
    errors = []

    macro = payload.get("macro_strategy")
    if not isinstance(macro, dict) or not macro.get("pillars"):
        errors.append("macro_strategy 缺失或没有 pillars")

    milestones = payload.get("key_milestones")
    if not isinstance(milestones, list):
        errors.append("key_milestones 必须是数组")
        milestones = []

    raw_goals = payload.get("daily_goals")
    goals: dict[int, str] = {}
    if not isinstance(raw_goals, dict):
        errors.append("daily_goals 必须是对象")
    else:
        for k, v in raw_goals.items():
            try:
                day = int(k)
            except (TypeError, ValueError):
                errors.append(f"daily_goals 的键 {k!r} 不是数字")
                continue
            if 1 <= day <= SEASON_DAYS and isinstance(v, str) and v.strip():
                goals[day] = v.strip()
        missing = [d for d in range(1, SEASON_DAYS + 1) if d not in goals]
        if missing:
            errors.append(f"daily_goals 缺少或为空的天数: {missing}")

    plantings = _parse_plantings(payload.get("plantings"), errors)

    if errors:
        raise StrategyParseError("；".join(errors))

    return ParsedStrategy(macro, _clean_milestones(milestones), goals, plantings,
                          _parse_other_income(payload.get("other_income")))


def _clean_milestones(milestones) -> list[dict]:
    """只保留 day 能转成 1-28 整数的节点，并按日期排序（修复后的 JSON 里 day 可能是字符串）。"""
    cleaned = []
    for m in milestones or []:
        if not isinstance(m, dict):
            continue
        try:
            day = int(m.get("day"))
        except (TypeError, ValueError):
            continue
        if 1 <= day <= SEASON_DAYS:
            cleaned.append({**m, "day": day})
    return sorted(cleaned, key=lambda m: m["day"])


def _lenient_parse(payload: dict) -> ParsedStrategy:
    """重试仍失败时的兜底：尽量保留能用的部分，缺失的天数填充占位。"""
    macro = payload.get("macro_strategy") if isinstance(payload.get("macro_strategy"), dict) else {}
    raw = payload.get("daily_goals") if isinstance(payload.get("daily_goals"), dict) else {}
    goals = {}
    for d in range(1, SEASON_DAYS + 1):
        v = raw.get(str(d), raw.get(d))
        goals[d] = v.strip() if isinstance(v, str) and v.strip() else FALLBACK_GOAL
    return ParsedStrategy(macro, _clean_milestones(payload.get("key_milestones")), goals,
                          _parse_plantings(payload.get("plantings"), None),
                          _parse_other_income(payload.get("other_income")))


def _fallback_warnings(error: str | None, goals: dict[int, str], payload: dict, parsed: ParsedStrategy) -> list[str]:
    """走了兜底解析时明确告诉用户：哪些内容缺失，资金预期不可靠。"""
    if error is None:
        return []
    parts = []
    missing = [d for d in sorted(goals) if goals[d] == FALLBACK_GOAL]
    if missing:
        parts.append(f"第 {'、'.join(map(str, missing))} 天没有目标（显示为“{FALLBACK_GOAL}”）")
    raw = payload.get("plantings")
    if not isinstance(raw, list):
        parts.append("种植清单缺失")
    elif len(parsed.plantings) < len(raw):
        parts.append(f"种植清单有 {len(raw) - len(parsed.plantings)} 条无效")
    detail = "；".join(parts) + "；" if parts else ""
    return [f"⚠️ 模型重试后输出仍不合格，已用兜底结果：{detail}攻略可能不完整，资金预期不可靠（原因：{error[:150]}）"]


@dataclass
class _Draft:
    """一次生成（含格式重试）的结果。messages 是产生 raw 的对话，不含 raw 本身。"""
    raw: str
    payload: dict
    parse_method: str
    parsed: ParsedStrategy
    fallback_error: str | None
    calls: int
    messages: list


def _draft(client: LLMClient, messages: list, max_retries: int, allow_fallback: bool = True) -> _Draft:
    """调用模型并校验格式；不合格时把错误回传给模型重试。重试用完：能兜底就兜底，否则抛出 StrategyParseError。"""
    messages = list(messages)
    raw, parse_method, calls = "", "", 0
    payload: dict = {}
    for attempt in range(max_retries + 1):
        calls = attempt + 1
        truncated = False
        try:
            raw = client.complete(SYSTEM_PROMPT, messages)
        except TruncatedOutput as e:  # 截断的 JSON 可能被修复成“看起来完整”的结果，不能直接采信
            raw, truncated = e.text, True
        payload = {}
        try:
            parsed = extract_json(raw)  # 语法错误在本地修复，不消耗重试次数
            payload, parse_method = parsed.data, parsed.method
            strategy = validate_payload(payload)
            if truncated:
                raise StrategyParseError(TRUNCATED_ERROR)
            return _Draft(raw, payload, parse_method, strategy, None, calls, messages)
        except StrategyParseError as e:
            reason = TRUNCATED_ERROR if truncated else str(e)
            if attempt == max_retries:
                if not payload or not allow_fallback:
                    raise StrategyParseError(reason) from e
                return _Draft(raw, payload, parse_method, _lenient_parse(payload), reason, calls, messages)
            messages += [
                {"role": "assistant", "content": raw},
                {"role": "user", "content": REPAIR_PROMPT_TEMPLATE.format(error=reason)},
            ]
    raise AssertionError("unreachable")


@dataclass
class _Season:
    """评估一份草稿需要的本季信息。"""
    data: GameData
    season: str
    year: int
    bonuses: Bonuses
    prior_state: FarmState | None
    gold: int | None
    gold_estimated: int | None
    seed_stock: dict


@dataclass
class _Evaluation:
    goals: dict[int, str]
    plantings: list[Planting]
    finance: SeasonFinance
    end_state: FarmState
    warnings: list[str]
    gift_warnings: list[str]
    problems: list[str]  # 需要模型修正的问题：warnings 和 gift_warnings 中排除固定提醒、说明类的部分


def _evaluate(d: _Draft, s: _Season) -> _Evaluation:
    """程序校验一份草稿：种植清单 → 资金 → 季末状态 → 各项规则。"""
    strategy, data, season_key = d.parsed, s.data, s.season
    carry_in = s.prior_state.carry if s.prior_state else ()
    goals = dict(strategy.goals)

    plantings, planting_warnings = resolve_plantings(strategy.plantings, data, season_key, s.year, s.bonuses)
    finance = compute_finance(
        plantings, carry_in, start_gold=s.gold, start_gold_estimated=s.gold_estimated,
        other_income=strategy.other_income, bonuses=s.bonuses, seed_stock=s.seed_stock,
    )
    end_state = extract_farm_state(season_key, s.year, plantings, finance, carry_in,
                                   strategy.macro.get("season_goals"))

    carried = s.prior_state.carried_names if s.prior_state else ()
    goal_checks = check_plantings(goals, data, season_key, s.bonuses, s.year, carried)
    reported = {(w.day, w.crop) for w in goal_checks}
    problems = [f"⚠️ {w}" for w in goal_checks] + planting_warnings
    problems += late_planting_warnings(plantings, season_key, s.bonuses, reported)
    problems += bus_warnings(plantings, s.year, reported)
    if d.fallback_error is None:  # 兜底时清单本来就不可靠，下面的兜底警告已经说明
        problems += [f"⚠️ {w}" for w in check_plantings_match(goals, plantings, data, reported)]
    problems += finance.warnings
    note = fertilizer_availability_note(s.bonuses, season_key, s.year, plantings[0].day) if plantings else None
    if note and note.startswith("⚠️"):
        problems.append(note)

    warnings = _fallback_warnings(d.fallback_error, goals, d.payload, strategy) + list(problems)
    if note and not note.startswith("⚠️"):
        warnings.append(note)
    last_goal = goals.get(SEASON_DAYS, "")
    had_cleanup = "清理农场" in last_goal and "停止种植" in last_goal
    goals, inserted = enforce_season_end(goals, end_state.carried_names)
    nxt_zh = SEASON_ZH[next_season(season_key)]
    dying = "、".join(c.name for c in withering_crops(data, season_key, s.year))
    if dying:
        warnings.append(f"⚠️ 第 28 天是换季前一天：{dying}将在进入{nxt_zh}时枯死，请在此之前收获完毕。")
    if end_state.carry:
        kept = "；".join(c.describe(end_state.next_season) for c in end_state.carry)
        warnings.append(f"ℹ️ 跨入{nxt_zh}继续生长：{kept}。")
    if inserted and had_cleanup:
        warnings.append(f"ℹ️ 第 28 天的清理目标已注明保留{'、'.join(end_state.carried_names)}。")
    elif inserted:
        warnings.append(f"ℹ️ 已自动在第 28 天加入“{goals[SEASON_DAYS].split('；')[-1]}”。")

    gift_warnings = [f"⚠️ {w}" for w in check_gifts(goals, data, season_key)]
    return _Evaluation(goals, plantings, finance, end_state, warnings, gift_warnings, problems + gift_warnings)


def _revise_prompt(problems: list[str]) -> str:
    listed = "\n".join(f"{i}. {p.removeprefix('⚠️ ')}" for i, p in enumerate(problems, 1))
    return REVISE_PROMPT_TEMPLATE.format(problems=listed)


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

def generate_strategy(
    playstyle: str,
    pacing: str,
    season: str = "spring",
    year: int = 1,
    *,
    provider: str | None = None,
    model: str | None = None,
    client: LLMClient | None = None,
    data: GameData | None = None,
    guide_store: GuideStore | None = None,
    n_guides: int = 3,
    professions: Iterable[str] = (),
    fertilizer: str | None = None,
    max_retries: int = 1,
    start_gold: int | None = None,
    prior_state: FarmState | None = None,
    farming_level: int | None = None,
    processing_share: float = 1.0,
    revise_rounds: int = 1,
) -> StrategyResult:
    """生成某季节的完整攻略。

    Args:
        playstyle: 玩法风格，例如 "钓鱼赚钱"、"全收集"、"种田为主"。
        pacing: 节奏偏好，例如 "速通极限"、"休闲慢节奏"。
        season: spring/summer/fall/winter 或中文。
        provider: anthropic / mock（默认读环境变量 LLM_PROVIDER）。
        guide_store: 攻略向量库，默认使用 data/.chroma_db；不可用时退回关键词检索。
        n_guides: 检索的攻略片段数。
        professions: 职业，如 ["tiller", "artisan"] 或中文名；10 级职业会自动加入前置的农耕人。
        fertilizer: 加速肥料：speed_gro / deluxe_speed_gro / hyper_speed_gro（或中文名）。
        start_gold: 期初资金；不传时第 1 年春季用开局金币（data/farm.json），其他季节为未知。
        prior_state: 上一季结束时的农场状态（跨季连续规划时由 generate_plan 传入）。
        farming_level: 耕种等级（0-14）；给出或用了品质肥料时计入作物品质。
        processing_share: 机器能加工的收获比例（0-1），决定“高星直接卖、低星加工”的分界。
        revise_rounds: 自动修正的最多轮数（默认 1，0 = 关闭）。程序校验发现问题时，把问题清单反馈给模型
            重新生成；只有问题变少才采用新版本。会多调用模型。
    """
    season_key = normalize_season(season)
    if year < 1:
        raise ValueError(f"年份必须 ≥ 1，收到 {year}")
    if prior_state is not None and (prior_state.next_season, prior_state.next_year) != (season_key, year):
        expected = f"第 {prior_state.next_year} 年{SEASON_ZH[prior_state.next_season]}"
        raise ValueError(f"上季状态只能衔接{expected}，收到第 {year} 年{SEASON_ZH[season_key]}")
    if start_gold is not None and start_gold < 0:
        raise ValueError(f"期初资金必须 ≥ 0，收到 {start_gold}")
    bonuses = resolve_bonuses(professions, fertilizer, farming_level=farming_level)
    if not 0 <= processing_share <= 1:
        raise ValueError(f"加工比例必须在 0-1 之间，收到 {processing_share}")
    if revise_rounds < 0:
        raise ValueError(f"自动修正轮数必须 ≥ 0，收到 {revise_rounds}")
    season_zh = SEASON_ZH[season_key]
    data = data or load_game_data()
    client = client or get_client(provider, model)

    # 0) 资金起点：上季状态 > 显式传入 > 第 1 年春季的开局金币
    if prior_state is not None:
        gold, gold_estimated = prior_state.gold, prior_state.gold_estimated
    else:
        if start_gold is None and (season_key, year) == ("spring", 1):
            start_gold = data.starting_gold
        gold = gold_estimated = start_gold
    # 新存档（第 1 年春季、没有上季状态）有开局赠送的种子
    seed_stock = data.starting_seed_stock() if prior_state is None and (season_key, year) == ("spring", 1) else {}

    # 1) 检索攻略片段（RAG）+ 数据 → Prompt
    guide_query = f"{season_zh}，{playstyle}，{pacing}"
    guide_excerpts = search_guides(
        guide_query, n_results=n_guides, store=guide_store, fallback_chunks=data.guide_chunks
    )
    carryover = render_state_for_prompt(prior_state, start_gold, seed_stock)
    context = build_data_context(data, season_key, guide_excerpts, bonuses, year, carryover, processing_share)
    user_prompt = build_user_prompt(
        playstyle=playstyle, pacing=pacing, season_zh=season_zh, year=year, context=context
    )
    messages = [{"role": "user", "content": user_prompt}]

    # 2) 调用模型 + 校验格式（不合格时把错误回传给模型重试）
    season_info = _Season(data, season_key, year, bonuses, prior_state, gold, gold_estimated, seed_stock)
    draft = _draft(client, messages, max_retries)
    attempts = draft.calls
    evaluation = _evaluate(draft, season_info)
    initial_problems = list(evaluation.problems)

    # 3) 自动修正：把程序校验发现的问题反馈给模型，问题变少才采用新版本
    revisions: list[dict] = []
    for round_no in range(1, revise_rounds + 1):
        if draft.fallback_error is not None or not evaluation.problems:
            break
        conversation = draft.messages + [
            {"role": "assistant", "content": draft.raw},
            {"role": "user", "content": _revise_prompt(evaluation.problems)},
        ]
        record = {"round": round_no, "problems_before": len(evaluation.problems)}
        try:
            candidate = _draft(client, conversation, 0, allow_fallback=False)
        except StrategyParseError as e:
            attempts += 1
            revisions.append({**record, "problems_after": None, "accepted": False, "error": str(e)[:150]})
            break
        attempts += candidate.calls
        candidate_eval = _evaluate(candidate, season_info)
        accepted = len(candidate_eval.problems) < len(evaluation.problems)
        revisions.append({**record, "problems_after": len(candidate_eval.problems), "accepted": accepted})
        if not accepted:
            break
        draft, evaluation = candidate, candidate_eval

    warnings = list(evaluation.warnings)
    if revisions:
        last = revisions[-1]
        if any(r["accepted"] for r in revisions):
            warnings.append(f"ℹ️ 程序把校验发现的问题反馈给模型修正：问题从 {len(initial_problems)} 条减少到 "
                            f"{len(evaluation.problems)} 条。")
        elif last["problems_after"] is None:
            warnings.append("ℹ️ 程序把校验发现的问题反馈给模型修正，但修正版格式不合格，保留原版本。")
        else:
            warnings.append(f"ℹ️ 程序把校验发现的问题反馈给模型修正，但问题没有减少（{last['problems_before']} → "
                            f"{last['problems_after']} 条），保留原版本。")

    raw, parse_method, strategy = draft.raw, draft.parse_method, draft.parsed
    macro, milestones, goals = strategy.macro, strategy.milestones, evaluation.goals

    # 4) 合并日程 + 渲染
    result = StrategyResult(
        playstyle=playstyle,
        bonuses=bonuses,
        pacing=pacing,
        season=season_key,
        year=year,
        macro_strategy=macro,
        key_milestones=milestones,
        daily_goals=goals,
        schedule=merge_daily_goals(goals),
        season_warnings=warnings,
        gift_warnings=evaluation.gift_warnings,
        raw_response=raw,
        guide_excerpts=guide_excerpts,
        processing_share=processing_share,
        attempts=attempts,
        json_parse_method=parse_method,
        plantings=evaluation.plantings,
        other_income=strategy.other_income,
        finance=evaluation.finance,
        prior_state=prior_state,
        end_state=evaluation.end_state,
        problems=evaluation.problems,
        initial_problems=initial_problems,
        revisions=revisions,
    )
    result.markdown = render_markdown(result, data)
    return result


# ---------------------------------------------------------------------------
# 跨季连续规划
# ---------------------------------------------------------------------------

@dataclass
class PlanResult:
    results: list[StrategyResult]
    markdown: str = ""
    failed_season: tuple[str, int] | None = None  # 中途失败的季节（季节, 年份）；之后的季节没有生成
    error: str | None = None
    planned: list[tuple[str, int]] = field(default_factory=list)  # 计划生成的全部季节

    @property
    def complete(self) -> bool:
        return self.failed_season is None

    @property
    def states(self) -> list[FarmState]:
        return [r.end_state for r in self.results if r.end_state]


def parse_seasons(seasons: str | Iterable[str], year: int = 1) -> list[tuple[str, int]]:
    """把 "spring,summer" 解析成 [(季节, 年份)]。季节必须连续；冬季之后进入下一年的春季。"""
    if isinstance(seasons, str):
        seasons = [s for s in seasons.replace("，", ",").split(",") if s.strip()]
    keys = [normalize_season(s) for s in seasons]
    if not keys:
        raise ValueError("至少需要一个季节")
    plan = [(keys[0], year)]
    for key in keys[1:]:
        expected = following_season(*plan[-1])
        if key != expected[0]:
            raise ValueError(f"季节必须连续：{SEASON_ZH[plan[-1][0]]}之后应为{SEASON_ZH[expected[0]]}，"
                             f"收到{SEASON_ZH[key]}")
        plan.append(expected)
    return plan


def generate_plan(
    playstyle: str,
    pacing: str,
    seasons: str | Iterable[str] = ("spring", "summer"),
    year: int = 1,
    *,
    start_gold: int | None = None,
    data: GameData | None = None,
    client: LLMClient | None = None,
    provider: str | None = None,
    model: str | None = None,
    **kwargs,
) -> PlanResult:
    """跨季连续规划：逐季调用 generate_strategy，把上一季的季末状态注入下一季。"""
    from .renderer import render_plan  # 避免循环导入

    plan = parse_seasons(seasons, year)
    data = data or load_game_data()
    client = client or get_client(provider, model)
    results: list[StrategyResult] = []
    state: FarmState | None = None
    out = PlanResult(results, planned=plan)
    for season_key, season_year in plan:
        try:
            result = generate_strategy(
                playstyle, pacing, season_key, season_year, client=client, data=data,
                start_gold=start_gold if state is None else None, prior_state=state, **kwargs,
            )
        except Exception as e:
            if not results:  # 第一季就失败：没有可保留的结果，照常抛出
                raise
            # 之后的季节失败：保留已经生成（已付费）的季节，并说明从哪一季开始缺失
            out.failed_season, out.error = (season_key, season_year), f"{type(e).__name__}: {e}"[:300]
            break
        results.append(result)
        state = result.end_state
    out.markdown = render_plan(out, data)
    return out
