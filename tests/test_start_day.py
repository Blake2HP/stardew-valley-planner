"""季中动态重规划（start_day）：收益重算、日程校验范围、资金模拟、跨季衔接、mock 端到端。"""

import json

import pytest

from stardew_planner import generate_plan, generate_strategy
from stardew_planner.achievements import (
    Progress, _season_share, friendship_season, load_achievement_rules, resolve_goals, season_targets,
)
from stardew_planner.analysis import build_data_context, rank_crops, season_profit, too_late_crops
from stardew_planner.bonuses import NO_BONUSES
from stardew_planner.generator import StrategyParseError, validate_payload
from stardew_planner.honey import simulate_honey
from stardew_planner.livestock import Herd, produce_days, simulate_livestock
from stardew_planner.llm_client import MOCK_RESPONSE, MockClient
from stardew_planner.season_rules import check_gifts


def crop(data, name):
    return next(c for c in data.crops if c.name == name)


class FixedClient:
    """原样返回一份 1-28 天的完整规划（不理会“今天是第几天”），用来测试程序的校验。"""

    def __init__(self, payload=MOCK_RESPONSE):
        self.text = json.dumps(payload, ensure_ascii=False)
        self.prompts = []

    def complete(self, system, messages):
        self.prompts.append(messages[0]["content"])
        return self.text


# ---------------------------------------------------------------------------
# 收益重算
# ---------------------------------------------------------------------------

def test_profit_from_start_day(data):
    cauliflower = crop(data, "花椰菜")       # 生长 12 天
    assert season_profit(cauliflower, 1)[0] == 2      # ⌊(28 − 1) / 12⌋ = 2
    assert season_profit(cauliflower, 15)[0] == 1     # 可用 28 − 15 + 1 = 14 天：⌊13 / 12⌋ = 1
    assert season_profit(cauliflower, 17)[0] == 0     # 17 + 12 = 29 > 28，来不及成熟


def test_rank_crops_skips_crops_too_late(data):
    names = {r.crop.name for r in rank_crops(data, "spring", top_n=50, year=1, start_day=17)}
    assert "花椰菜" not in names and "防风草" in names
    assert "花椰菜" in {c.name for c in too_late_crops(data, "spring", 17, year=1)}
    by_name = {r.crop.name: r for r in rank_crops(data, "spring", top_n=50, year=1, start_day=10)}
    assert by_name["草莓"].plant_day == 13              # max(第 10 天, 草莓种子开售日 13)
    assert by_name["防风草"].plant_day == 10


def test_cross_season_crops_stay_listed(data):
    corn = next(r for r in rank_crops(data, "summer", top_n=50, year=1, start_day=27) if r.crop.name == "玉米")
    assert corn.seasons_alive == 2 and corn.harvests > 0   # 玉米跨夏秋，第 27 天种也来得及


def test_prompt_context_for_mid_season(data):
    ctx = build_data_context(data, "spring", year=1, start_day=17)
    assert "今天是本季第 17 天" in ctx["today"]
    assert "已经来不及成熟、不要再种" in ctx["crop_table"] and "花椰菜（生长 12 天）" in ctx["crop_table"]
    assert "复活节" not in ctx["calendar_events"] and "花舞节" in ctx["calendar_events"]   # 第 13 天的节日已过
    assert "海莉生日" not in ctx["npc_birthdays"]                                           # 海莉生日是第 14 天
    assert build_data_context(data, "spring", year=1)["today"] == ""


# ---------------------------------------------------------------------------
# 日程校验范围
# ---------------------------------------------------------------------------

def test_daily_goals_only_need_remaining_days():
    payload = {**MOCK_RESPONSE, "daily_goals": {str(d): "钓鱼" for d in range(15, 29)}}
    parsed = validate_payload(payload, start_day=15)
    assert sorted(parsed.goals) == list(range(15, 29))
    with pytest.raises(StrategyParseError, match=r"缺少或为空的天数: \[20\]"):
        validate_payload({**payload, "daily_goals": {str(d): "钓鱼" for d in range(15, 29) if d != 20}},
                         start_day=15)
    with pytest.raises(StrategyParseError):
        validate_payload(payload)                     # 从第 1 天开始时仍然要 28 天


def test_earlier_days_are_dropped():
    parsed = validate_payload(MOCK_RESPONSE, start_day=15)
    assert min(parsed.goals) == 15
    assert all(m["day"] >= 15 for m in parsed.milestones)


def test_past_plantings_are_reported_and_not_counted(data):
    result = generate_strategy("种田", "稳健", "spring", client=FixedClient(), data=data, start_day=15,
                               start_gold=1000, revise_rounds=0)
    past = [p for p in result.problems if "早于今天（第 15 天）" in p]
    assert len(past) == 4                                       # 第 1、7、7、14 天的播种
    assert "种植清单第 1 天的防风草早于今天" in past[0]
    assert [p.day for p in result.plantings] == [22]            # 只剩第 22 天的土豆
    assert result.finance.seed_cost == 5 * 50


def test_birthdays_before_start_day_are_not_checked(data):
    no_gifts = {d: "钓鱼" for d in range(1, 29)}
    assert any("海莉生日" in str(w) for w in check_gifts(no_gifts, data, "spring"))
    later = {d: "钓鱼" for d in range(15, 29)}
    assert not any("海莉" in str(w) for w in check_gifts(later, data, "spring", 15))   # 第 14 天已过


def test_past_tool_upgrades_and_constructions(data):
    payload = {**MOCK_RESPONSE, "tool_upgrades": [{"tool": "喷壶", "day": 5, "level": "铜"}],
               "constructions": [{"building": "鸡舍", "day": 3}]}
    result = generate_strategy("种田", "稳健", "spring", client=FixedClient(payload), data=data, start_day=15,
                               start_gold=5000, revise_rounds=0)
    assert any("第 5 天交送喷壶早于今天" in p for p in result.problems)
    assert any("第 3 天找罗宾建鸡舍早于今天" in p for p in result.problems)
    assert result.finance.tool_cost == 0 and result.finance.building_cost == 0


# ---------------------------------------------------------------------------
# 资金模拟与成就
# ---------------------------------------------------------------------------

def test_livestock_from_start_day(data):
    chicken = next(a for a in data.animals if a.name == "鸡")
    herd = Herd(chicken, 2)
    assert produce_days(herd, "spring", 15)[0] == 15 + chicken.mature_nights + 1
    result = simulate_livestock([herd], "spring", data.hay_price, 15)
    assert result.cash_by_day[15] < 0 and min(result.cash_by_day) == 15    # 第 15 天买入，之前没有收支
    winter = simulate_livestock([Herd(chicken, 1, new=False)], "winter", 50, 21)
    assert winter.hay_cost == 8 * 50                                        # 只买第 21-28 天的干草


def test_honey_from_start_day(data):
    full = simulate_honey([], "spring", 1, data.honey, NO_BONUSES)
    late = simulate_honey([], "spring", 1, data.honey, NO_BONUSES, start_day=15)
    assert sum(full.jars.values()) == 7 and sum(late.jars.values()) == 4   # 第 16、20、24、28 天
    assert min(late.cash_by_day) == 16


def test_friendship_only_counts_remaining_days(data):
    rules = load_achievement_rules().friendship
    goals = {d: "和海莉聊天" for d in range(1, 29)}
    assert friendship_season(goals, data, rules, "spring", {}, 15)["海莉"] == 14 * rules.talk_daily


def test_targets_scale_with_remaining_days(data):
    assert _season_share(25000, 4) == 6250
    assert _season_share(25000, 4, 15) == 3572           # ⌈25000 × 14 / (3 × 28 + 14)⌉
    rules = load_achievement_rules()
    t = season_targets(resolve_goals("混合栽培,万人迷", rules), Progress(), data.crops, "spring", 1, start_day=20)
    assert "花椰菜" not in {c.crop.name for c in t.crops}
    assert "花椰菜（第 20 天起来不及成熟）" in t.later
    assert t.friend_gap.per_season == _season_share(25000, 4, 20)


# ---------------------------------------------------------------------------
# 端到端（mock）与跨季衔接
# ---------------------------------------------------------------------------

def test_mock_end_to_end(data):
    result = generate_strategy("钓鱼赚钱", "休闲慢节奏", "spring", client=MockClient(), data=data, start_day=15)
    assert result.start_day == 15 and min(result.daily_goals) == 15
    assert "第 1 年春季（第 15–28 天）攻略" in result.markdown and "季中重新规划" in result.markdown
    assert result.finance.start_gold is None                     # 季中开始：不默认开局 500g
    assert not result.finance.lines or "开局赠送" not in "\n".join(result.finance.lines)
    assert not any("早于今天" in p for p in result.problems)     # mock 按“今天”只排剩下的日子
    assert result.schedule[0].start == 15


def test_plan_crosses_into_next_season_from_day_one(data):
    plan = generate_plan("钓鱼赚钱", "休闲慢节奏", "spring,summer", client=MockClient(), data=data,
                         start_day=15, start_gold=1000)
    spring, summer = plan.results
    assert (spring.start_day, summer.start_day) == (15, 1)
    assert min(summer.daily_goals) == 1 and summer.prior_state is not None
    assert summer.finance.start_gold == spring.finance.end_gold
    assert "第 1 年春季（第 15–28 天） → 第 1 年夏季" in plan.markdown


@pytest.mark.parametrize("day", [0, 29, 1.5, True])
def test_bad_start_day(data, day):
    with pytest.raises(ValueError, match="起始日"):
        generate_strategy("种田", "稳健", "spring", client=MockClient(), data=data, start_day=day)
