"""核心攻略生成：数据 → Prompt → 大模型 → 解析校验 → 合并日程 → Markdown。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from .analysis import SEASON_DAYS, build_data_context
from .data_loader import SEASON_ZH, GameData, load_game_data, normalize_season
from .llm_client import LLMClient, get_client
from .prompts import REPAIR_PROMPT_TEMPLATE, SYSTEM_PROMPT, build_user_prompt
from .renderer import render_markdown
from .schedule import ScheduleBlock, merge_daily_goals
from .season_rules import check_plantings, enforce_season_end, next_season, withering_crops

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
    markdown: str = ""
    raw_response: str = ""


# ---------------------------------------------------------------------------
# 解析与校验
# ---------------------------------------------------------------------------

def extract_json(text: str) -> dict:
    """容错地从模型输出中取出 JSON：去掉 ```json 代码块，截取第一个 { 到最后一个 }。"""
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.MULTILINE)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise StrategyParseError("输出中找不到 JSON 对象")
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError as e:
        raise StrategyParseError(f"JSON 语法错误: {e}") from e


def validate_payload(payload: dict) -> tuple[dict, list[dict], dict[int, str]]:
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

    if errors:
        raise StrategyParseError("；".join(errors))

    milestones = sorted(
        (m for m in milestones if isinstance(m, dict) and "day" in m),
        key=lambda m: int(m["day"]),
    )
    return macro, milestones, goals


def _lenient_parse(payload: dict) -> tuple[dict, list[dict], dict[int, str]]:
    """重试仍失败时的兜底：尽量保留能用的部分，缺失的天数填充占位。"""
    macro = payload.get("macro_strategy") if isinstance(payload.get("macro_strategy"), dict) else {}
    milestones = [m for m in payload.get("key_milestones") or [] if isinstance(m, dict) and "day" in m]
    raw = payload.get("daily_goals") if isinstance(payload.get("daily_goals"), dict) else {}
    goals = {}
    for d in range(1, SEASON_DAYS + 1):
        v = raw.get(str(d), raw.get(d))
        goals[d] = v.strip() if isinstance(v, str) and v.strip() else FALLBACK_GOAL
    return macro, sorted(milestones, key=lambda m: int(m["day"])), goals


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
    max_retries: int = 1,
) -> StrategyResult:
    """生成某季节的完整攻略。

    Args:
        playstyle: 玩法风格，例如 "钓鱼赚钱"、"全收集"、"种田为主"。
        pacing: 节奏偏好，例如 "速通极限"、"休闲慢节奏"。
        season: spring/summer/fall/winter 或中文。
        provider: anthropic / openai / mock（默认读环境变量 LLM_PROVIDER）。
    """
    season_key = normalize_season(season)
    season_zh = SEASON_ZH[season_key]
    data = data or load_game_data()
    client = client or get_client(provider, model)

    # 1) 数据 → Prompt
    context = build_data_context(data, season_key, query=f"{playstyle} {pacing} {season_zh}")
    user_prompt = build_user_prompt(
        playstyle=playstyle, pacing=pacing, season_zh=season_zh, year=year, context=context
    )
    messages = [{"role": "user", "content": user_prompt}]

    # 2) 调用模型 + 校验，失败时把错误回传给模型自我修正
    raw = ""
    payload: dict = {}
    for attempt in range(max_retries + 1):
        raw = client.complete(SYSTEM_PROMPT, messages)
        payload = {}
        try:
            payload = extract_json(raw)
            macro, milestones, goals = validate_payload(payload)
            break
        except StrategyParseError as e:
            if attempt == max_retries:
                if not payload:
                    raise
                macro, milestones, goals = _lenient_parse(payload)
                break
            messages += [
                {"role": "assistant", "content": raw},
                {"role": "user", "content": REPAIR_PROMPT_TEMPLATE.format(error=e)},
            ]

    # 3) 跨季规则校验：检查来不及成熟的种植，并强制第 28 天写入清理目标
    warnings = [f"⚠️ {w}" for w in check_plantings(goals, data, season_key)]
    goals, inserted = enforce_season_end(goals)
    dying = "、".join(c.name for c in withering_crops(data, season_key))
    if dying:
        warnings.append(f"⚠️ 第 28 天是换季前一天：{dying}将在进入{SEASON_ZH[next_season(season_key)]}时枯死，请在此之前收获完毕。")
    if inserted:
        warnings.append("ℹ️ 已自动在第 28 天加入“清理农场/停止种植”。")

    # 4) 合并日程 + 渲染
    result = StrategyResult(
        playstyle=playstyle,
        pacing=pacing,
        season=season_key,
        year=year,
        macro_strategy=macro,
        key_milestones=milestones,
        daily_goals=goals,
        schedule=merge_daily_goals(goals),
        season_warnings=warnings,
        raw_response=raw,
    )
    result.markdown = render_markdown(result, data)
    return result
