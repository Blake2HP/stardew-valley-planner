"""核心攻略生成：数据 → Prompt → 大模型 → 解析校验 → 合并日程 → Markdown。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from .analysis import SEASON_DAYS, build_data_context
from .bonuses import NO_BONUSES, Bonuses, fertilizer_availability_note, resolve_bonuses
from .data_loader import SEASON_ZH, GameData, GuideStore, load_game_data, normalize_season, search_guides
from .farm_state import (
    FarmState, OtherIncome, Planting, SeasonFinance, compute_finance, extract_farm_state,
    following_season, late_planting_warnings, render_state_for_prompt, resolve_plantings,
)
from .json_utils import JSONParseError, ParsedJSON, parse_llm_json
from .llm_client import LLMClient, get_client
from .prompts import REPAIR_PROMPT_TEMPLATE, SYSTEM_PROMPT, build_user_prompt
from .renderer import render_markdown
from .schedule import ScheduleBlock, merge_daily_goals
from .season_rules import check_gifts, check_plantings, enforce_season_end, next_season, withering_crops

FALLBACK_GOAL = "自由安排"


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
) -> StrategyResult:
    """生成某季节的完整攻略。

    Args:
        playstyle: 玩法风格，例如 "钓鱼赚钱"、"全收集"、"种田为主"。
        pacing: 节奏偏好，例如 "速通极限"、"休闲慢节奏"。
        season: spring/summer/fall/winter 或中文。
        provider: anthropic / openai / mock（默认读环境变量 LLM_PROVIDER）。
        guide_store: 攻略向量库，默认使用 data/.chroma_db；不可用时退回关键词检索。
        n_guides: 检索的攻略片段数。
        professions: 职业，如 ["tiller", "artisan"] 或中文名；10 级职业会自动加入前置的农耕人。
        fertilizer: 加速肥料：speed_gro / deluxe_speed_gro / hyper_speed_gro（或中文名）。
        start_gold: 期初资金；不传时第 1 年春季用开局金币（data/farm.json），其他季节为未知。
        prior_state: 上一季结束时的农场状态（跨季连续规划时由 generate_plan 传入）。
        farming_level: 耕种等级（0-14）；给出或用了品质肥料时计入作物品质。
        processing_share: 机器能加工的收获比例（0-1），决定“高星直接卖、低星加工”的分界。
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
    carry_in = prior_state.carry if prior_state else ()

    # 1) 检索攻略片段（RAG）+ 数据 → Prompt
    guide_query = f"{season_zh}，{playstyle}，{pacing}"
    guide_excerpts = search_guides(
        guide_query, n_results=n_guides, store=guide_store, fallback_chunks=data.guide_chunks
    )
    carryover = render_state_for_prompt(prior_state, start_gold)
    context = build_data_context(data, season_key, guide_excerpts, bonuses, year, carryover, processing_share)
    user_prompt = build_user_prompt(
        playstyle=playstyle, pacing=pacing, season_zh=season_zh, year=year, context=context
    )
    messages = [{"role": "user", "content": user_prompt}]

    # 2) 调用模型 + 校验，失败时把错误回传给模型自我修正
    raw, parse_method, attempts = "", "", 0
    payload: dict = {}
    for attempt in range(max_retries + 1):
        attempts = attempt + 1
        raw = client.complete(SYSTEM_PROMPT, messages)
        payload = {}
        try:
            parsed = extract_json(raw)  # 语法错误在本地修复，不消耗重试次数
            payload, parse_method = parsed.data, parsed.method
            parsed_strategy = validate_payload(payload)
            break
        except StrategyParseError as e:
            if attempt == max_retries:
                if not payload:
                    raise
                parsed_strategy = _lenient_parse(payload)
                break
            messages += [
                {"role": "assistant", "content": raw},
                {"role": "user", "content": REPAIR_PROMPT_TEMPLATE.format(error=e)},
            ]

    macro, milestones, goals = parsed_strategy.macro, parsed_strategy.milestones, parsed_strategy.goals

    # 3) 种植清单 → 资金（程序计算）→ 季末状态
    plantings, planting_warnings = resolve_plantings(parsed_strategy.plantings, data, season_key, year, bonuses)
    finance = compute_finance(
        plantings, carry_in, start_gold=gold, start_gold_estimated=gold_estimated,
        other_income=parsed_strategy.other_income, bonuses=bonuses,
    )
    end_state = extract_farm_state(season_key, year, plantings, finance, carry_in, macro.get("season_goals"))

    # 4) 跨季规则校验：检查来不及成熟的种植，并强制第 28 天写入清理目标（保留跨季作物）
    carried = prior_state.carried_names if prior_state else ()
    goal_checks = check_plantings(goals, data, season_key, bonuses, year, carried)
    warnings = [f"⚠️ {w}" for w in goal_checks]
    warnings += planting_warnings
    warnings += late_planting_warnings(plantings, season_key, bonuses, {(w.day, w.crop) for w in goal_checks})
    warnings += finance.warnings
    if plantings and (note := fertilizer_availability_note(bonuses, season_key, year, plantings[0].day)):
        warnings.append(note)
    last_goal = goals.get(SEASON_DAYS, "")
    had_cleanup = "清理农场" in last_goal and "停止种植" in last_goal
    goals, inserted = enforce_season_end(goals, end_state.carried_names)
    nxt_zh = SEASON_ZH[next_season(season_key)]
    dying = "、".join(c.name for c in withering_crops(data, season_key, year))
    if dying:
        warnings.append(f"⚠️ 第 28 天是换季前一天：{dying}将在进入{nxt_zh}时枯死，请在此之前收获完毕。")
    if end_state.carry:
        kept = "；".join(c.describe(end_state.next_season) for c in end_state.carry)
        warnings.append(f"ℹ️ 跨入{nxt_zh}继续生长：{kept}。")
    if inserted and had_cleanup:
        warnings.append(f"ℹ️ 第 28 天的清理目标已注明保留{'、'.join(end_state.carried_names)}。")
    elif inserted:
        warnings.append(f"ℹ️ 已自动在第 28 天加入“{goals[SEASON_DAYS].split('；')[-1]}”。")

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
        gift_warnings=[f"⚠️ {w}" for w in check_gifts(goals, data, season_key)],
        raw_response=raw,
        guide_excerpts=guide_excerpts,
        processing_share=processing_share,
        attempts=attempts,
        json_parse_method=parse_method,
        plantings=plantings,
        other_income=parsed_strategy.other_income,
        finance=finance,
        prior_state=prior_state,
        end_state=end_state,
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
    for season_key, season_year in plan:
        result = generate_strategy(
            playstyle, pacing, season_key, season_year, client=client, data=data,
            start_gold=start_gold if state is None else None, prior_state=state, **kwargs,
        )
        results.append(result)
        state = result.end_state
    out = PlanResult(results)
    out.markdown = render_plan(out, data)
    return out
