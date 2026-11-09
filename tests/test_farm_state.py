"""跨季连续规划：种植清单、跨季收获模拟、资金计算、季末状态与注入下一季的 Prompt。"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from stardew_planner import generate_plan, generate_strategy
from stardew_planner.bonuses import resolve_bonuses
from stardew_planner.farm_state import (
    alive_seasons, compute_finance, extract_farm_state, following_season, harvest_days,
    render_state_for_prompt, resolve_plantings, season_label,
)
from stardew_planner.generator import StrategyParseError, parse_seasons, validate_payload
from stardew_planner.llm_client import MOCK_RESPONSE, MOCK_SUMMER, MockClient
from stardew_planner.season_rules import CLEANUP_GOAL, check_plantings, enforce_season_end

ROOT = Path(__file__).resolve().parent.parent
SV = str(ROOT / ".claude" / "skills" / "stardew-data-guard" / "scripts" / "sv.py")


def plant(data, season, year, *items, bonuses=None):
    raw = [{"day": d, "crop": c, "tiles": t} for d, c, t in items]
    plantings, warnings = resolve_plantings(raw, data, season, year, bonuses or resolve_bonuses([]))
    assert warnings == []
    return plantings


# ---------------------------------------------------------------------------
# 季节推进与跨季收获
# ---------------------------------------------------------------------------

def test_starting_gold_from_wiki(data):
    assert data.starting_gold == 500


@pytest.mark.parametrize("season, year, expected", [("spring", 1, ("summer", 1)), ("winter", 1, ("spring", 2))])
def test_following_season(season, year, expected):
    assert following_season(season, year) == expected


def test_parse_seasons():
    assert parse_seasons("spring,summer") == [("spring", 1), ("summer", 1)]
    assert parse_seasons("秋季，冬季,春季", 1) == [("fall", 1), ("winter", 1), ("spring", 2)]
    with pytest.raises(ValueError, match="季节必须连续"):
        parse_seasons("spring,fall")
    with pytest.raises(ValueError, match="至少"):
        parse_seasons("")


def test_alive_seasons(crops):
    assert alive_seasons(crops["玉米"], "summer") == 2
    assert alive_seasons(crops["玉米"], "fall") == 1
    assert alive_seasons(crops["向日葵"], "summer") == 2
    assert alive_seasons(crops["土豆"], "spring") == 1


@pytest.mark.parametrize(
    "crop, season, day, expected",
    [
        ("玉米", "summer", 1, list(range(15, 57, 4))),   # 1 + 14 = 15，之后每 4 天，直到秋季末（第 56 天）
        ("玉米", "fall", 1, [15, 19, 23, 27]),
        ("土豆", "spring", 1, [7]),
        ("土豆", "spring", 23, []),                       # 23 + 6 = 29 > 28
        ("草莓", "spring", 13, [21, 25]),
        ("向日葵", "summer", 25, [33]),                   # 跨到秋季第 5 天
    ],
)
def test_harvest_days(crops, crop, season, day, expected):
    assert harvest_days(crops[crop], day, season) == expected


def test_harvest_days_with_speed_gro(crops):
    assert harvest_days(crops["花椰菜"], 1, "spring", resolve_bonuses([], "speed_gro")) == [11]


def test_season_label():
    assert season_label("summer", 33) == "秋季第 5 天"
    assert season_label("winter", 29) == "春季第 1 天"


# ---------------------------------------------------------------------------
# 种植清单
# ---------------------------------------------------------------------------

def test_resolve_plantings_rejects_invalid_entries(data):
    raw = [
        {"day": 1, "crop": "龙果", "tiles": 5},
        {"day": 1, "crop": "蓝莓", "tiles": 5},
        {"day": 1, "crop": "大蒜", "tiles": 5},
        {"day": 5, "crop": "草莓", "tiles": 5},
        {"day": 1, "crop": "Potato", "tiles": 5},
    ]
    plantings, warnings = resolve_plantings(raw, data, "spring", 1)
    assert [p.crop.name for p in plantings] == ["土豆"]   # 英文名也认
    assert len(warnings) == 4
    assert all("未计入资金" in w for w in warnings)
    assert "第 2 年起" in warnings[2] and "第 13 天" in warnings[3]


# ---------------------------------------------------------------------------
# 资金
# ---------------------------------------------------------------------------

def test_mock_spring_finance(data):
    items = [(p["day"], p["crop"], p["tiles"]) for p in MOCK_RESPONSE["plantings"]]
    f = compute_finance(plant(data, "spring", 1, *items), start_gold=500, start_gold_estimated=500)
    # 防风草 525 + 花椰菜 875 + 土豆 2 × 500 + 草莓 2 × 5 × 120 × 1.0204 = 3624
    assert (f.crop_revenue, f.seed_cost, f.crop_net, f.end_gold) == (3624, 1700, 1924, 2424)
    assert f.warnings == []


def test_other_income_is_kept_separate(data):
    from stardew_planner.farm_state import OtherIncome

    f = compute_finance(plant(data, "spring", 1, (1, "防风草", 10)), start_gold=500, start_gold_estimated=500,
                        other_income=[OtherIncome("钓鱼", 2000)])
    assert f.end_gold == 500 + 10 * 35 - 10 * 20
    assert f.end_gold_estimated == f.end_gold + 2000


def test_fertilizer_charged_by_peak_tiles(data):
    # 生长激素下土豆 5 天：第 1 天种 10 格、第 6 天收获；第 7 天在同样 10 格补种 → 只买 10 格肥料
    b = resolve_bonuses([], "speed_gro")
    f = compute_finance(plant(data, "spring", 1, (1, "土豆", 10), (7, "土豆", 10), bonuses=b), bonuses=b)
    assert f.fertilizer_cost == 10 * 100
    assert "同时占用的最多格数 10" in f.lines[-1]


def test_cash_shortfall_warns(data):
    f = compute_finance(plant(data, "spring", 1, (1, "花椰菜", 10)), start_gold=100)
    assert len(f.warnings) == 1 and "第 1 天" in f.warnings[0] and "-700g" in f.warnings[0]


def test_cash_check_includes_fertilizer(data):
    # 种子 10 × 20 = 200g 买得起，肥料 10 × 100 = 1000g 买不起：第 1 天现金 250 − 1200 = −950
    b = resolve_bonuses([], "speed_gro")
    f = compute_finance(plant(data, "spring", 1, (1, "防风草", 10), bonuses=b), start_gold=250, bonuses=b)
    assert len(f.warnings) == 1 and "第 1 天" in f.warnings[0] and "-950g" in f.warnings[0]
    enough = compute_finance(plant(data, "spring", 1, (1, "防风草", 10), bonuses=b), start_gold=1200, bonuses=b)
    assert enough.warnings == []


def test_fertilizer_bought_only_when_new_tiles_are_used(data):
    # 第 7 天在收获后的同样 10 格补种，不用再买肥料；第 8 天多种 5 格，才买 5 份
    b = resolve_bonuses([], "speed_gro")
    plantings = plant(data, "spring", 1, (1, "土豆", 10), (7, "土豆", 10), (8, "土豆", 5), bonuses=b)
    f = compute_finance(plantings, bonuses=b)
    assert f.fertilizer_cost == 15 * 100
    # 现金变化：第 1 天 −500 −1000；第 6 天收获 +1000；第 7 天种子 −500；第 8 天种子 −250、肥料 −500
    # 最低点在第 8 天：期初 − 1750，所以期初 1750g 刚好够
    assert compute_finance(plantings, start_gold=1750, bonuses=b).warnings == []
    short = compute_finance(plantings, start_gold=1749, bonuses=b)
    assert "第 8 天" in short.warnings[0] and "-1g" in short.warnings[0]


def test_unpriced_fertilizer_costs_no_cash(data):
    b = resolve_bonuses([], "deluxe_fertilizer", farming_level=10)
    f = compute_finance(plant(data, "spring", 1, (1, "防风草", 10), bonuses=b), start_gold=200, bonuses=b)
    assert f.fertilizer_cost == 0 and f.warnings == []


def test_unknown_start_gold(data):
    f = compute_finance(plant(data, "spring", 1, (1, "花椰菜", 10)))
    assert f.end_gold is None and f.end_gold_estimated is None and f.warnings == []


# ---------------------------------------------------------------------------
# 季末状态
# ---------------------------------------------------------------------------

def test_corn_carries_into_fall(data):
    summer = plant(data, "summer", 1, (1, "蓝莓", 15), (1, "玉米", 8))
    f = compute_finance(summer, start_gold=2424, start_gold_estimated=2424)
    state = extract_farm_state("summer", 1, summer, f, planned_goals=["蓝莓收获 4 次"])
    assert (state.next_season, state.next_year) == ("fall", 1)
    assert state.carried_names == ("玉米",) and state.cleared == ("蓝莓",)
    assert state.carry[0].harvest_days == (3, 7, 11, 15, 19, 23, 27)
    # 夏季只计夏季内的 4 次玉米收获：蓝莓 4 × 15 × 3.0204 × 50 + 玉米 4 × 8 × 50
    assert f.crop_revenue == int(4 * 15 * 3.0204 * 50 + 4 * 8 * 50)

    fall = compute_finance([], state.carry, start_gold=state.gold)
    assert fall.crop_revenue == 7 * 8 * 50 and fall.seed_cost == 0   # 种子钱已在夏季付过
    assert extract_farm_state("fall", 1, [], fall, state.carry).carry == ()


def test_state_prompt_text(data):
    summer = plant(data, "summer", 1, (1, "玉米", 8))
    state = extract_farm_state("summer", 1, summer, compute_finance(summer, start_gold=2000, start_gold_estimated=2500))
    text = render_state_for_prompt(state)
    assert "不要重复种植" in text
    assert "玉米 ×8 格（夏季第 1 天种下）：秋季第 3、7、11、15、19、23、27 天可收获" in text
    assert f"期初资金（只算种植收支，程序计算）：{state.gold}g" in text
    assert "仅供参考" in text  # 含估算的金额单独标注
    assert "500g" in render_state_for_prompt(None, 500)
    assert "未知" in render_state_for_prompt(None, None)


# ---------------------------------------------------------------------------
# 校验规则
# ---------------------------------------------------------------------------

def test_replanting_carried_crop_warns(data):
    w = check_plantings({1: "种玉米"}, data, "fall", carried=["玉米"])
    assert len(w) == 1 and "已从上季延续" in w[0].message
    assert check_plantings({3: "收获玉米"}, data, "fall", carried=["玉米"]) == []


def test_cleanup_goal_keeps_carried_crops():
    goals, inserted = enforce_season_end({28: "收获蓝莓"}, keep=["玉米"])
    assert inserted and goals[28] == f"收获蓝莓；{CLEANUP_GOAL}（保留玉米）"
    assert enforce_season_end(goals, keep=["玉米"]) == (goals, False)


# ---------------------------------------------------------------------------
# 输出协议
# ---------------------------------------------------------------------------

def test_plantings_required():
    payload = {k: v for k, v in MOCK_RESPONSE.items() if k != "plantings"}
    with pytest.raises(StrategyParseError, match="plantings"):
        validate_payload(payload)
    assert validate_payload({**payload, "plantings": []}).plantings == []
    with pytest.raises(StrategyParseError, match=r"plantings\[0\]"):
        validate_payload({**payload, "plantings": [{"day": 40, "crop": "土豆", "tiles": 5}]})


def test_other_income_invalid_entries_dropped():
    parsed = validate_payload({**MOCK_RESPONSE, "other_income": [
        {"source": "钓鱼", "gold": "1200"}, {"source": "挖矿", "gold": -5}, "坏数据", {"gold": 10},
    ]})
    assert [(i.source, i.gold) for i in parsed.other_income] == [("钓鱼", 1200)]


class ScriptedClient:
    def __init__(self, *responses):
        self.responses, self.calls = list(responses), 0

    def complete(self, system, messages):
        self.calls += 1
        return self.responses[min(self.calls, len(self.responses)) - 1]


def test_missing_plantings_triggers_retry(data, indexed_store):
    missing = json.dumps({k: v for k, v in MOCK_RESPONSE.items() if k != "plantings"}, ensure_ascii=False)
    client = ScriptedClient(missing, json.dumps(MOCK_RESPONSE, ensure_ascii=False))
    result = generate_strategy("种田", "速通", "spring", client=client, data=data, guide_store=indexed_store)
    assert client.calls == 2 and len(result.plantings) == 5


# ---------------------------------------------------------------------------
# 生成流程
# ---------------------------------------------------------------------------

class RecordingMock(MockClient):
    def __init__(self):
        super().__init__()
        self.prompts = []

    def complete(self, system, messages):
        self.prompts.append(messages[0]["content"])
        return super().complete(system, messages)


def test_single_season_finance(data, indexed_store):
    client = RecordingMock()
    result = generate_strategy("种田", "速通", "spring", client=client, data=data, guide_store=indexed_store)
    assert "期初资金：500g（开局金币" in client.prompts[0]
    assert result.finance.end_gold == 2424 and result.end_state.gold == 2424
    assert "## 🌱 种植清单" in result.markdown and "## 💰 资金预期" in result.markdown
    assert "其他收入（模型估算，仅供参考）" in result.markdown


def test_start_gold_unknown_outside_first_spring(data, indexed_store):
    client = RecordingMock()
    result = generate_strategy("种田", "速通", "summer", client=client, data=data, guide_store=indexed_store)
    assert "期初资金：未知" in client.prompts[0] and result.finance.end_gold is None
    result = generate_strategy("种田", "速通", "summer", client=client, data=data, guide_store=indexed_store,
                               start_gold=3000)
    assert result.finance.start_gold == 3000


def test_prior_state_must_match_season(data, indexed_store):
    spring = generate_strategy("种田", "速通", "spring", client=RecordingMock(), data=data, guide_store=indexed_store)
    with pytest.raises(ValueError, match="只能衔接第 1 年夏季"):
        generate_strategy("种田", "速通", "fall", client=RecordingMock(), data=data, guide_store=indexed_store,
                          prior_state=spring.end_state)
    with pytest.raises(ValueError, match="期初资金"):
        generate_strategy("种田", "速通", "spring", client=RecordingMock(), data=data, start_gold=-1)


def test_generate_plan_injects_previous_state(data, indexed_store):
    client = RecordingMock()
    plan = generate_plan("种田", "速通", "spring,summer,fall", client=client, data=data, guide_store=indexed_store)
    assert [(r.season, r.year) for r in plan.results] == [("spring", 1), ("summer", 1), ("fall", 1)]
    spring, summer, fall = plan.results

    # 夏季 Prompt：春季的期末资金
    assert "### 上季延续状态（程序计算）" in client.prompts[1]
    assert f"期初资金（只算种植收支，程序计算）：{spring.end_state.gold}g" in client.prompts[1]
    assert summer.finance.start_gold == spring.finance.end_gold == 2424
    assert summer.finance.start_gold_estimated == spring.finance.end_gold_estimated

    # 秋季 Prompt：夏季留下的玉米
    assert "玉米 ×8 格（夏季第 1 天种下）" in client.prompts[2]
    assert fall.finance.start_gold == summer.finance.end_gold
    assert any(line.startswith("玉米（上季延续）：7 次") for line in fall.finance.lines)

    # 夏季第 28 天保留玉米；Markdown 有总览表
    assert "保留玉米" in summer.daily_goals[28]
    assert "## 📈 各季衔接总览" in plan.markdown
    assert "| 第 1 年夏季 | 2424g |" in plan.markdown
    assert plan.markdown.count("## 🌸 星露谷物语") == 1   # 各季标题降了一级
    assert len(plan.states) == 3


def test_generate_plan_crosses_year(data, indexed_store):
    plan = generate_plan("种田", "速通", ["winter", "spring"], client=RecordingMock(), data=data,
                         guide_store=indexed_store, start_gold=5000)
    assert [(r.season, r.year) for r in plan.results] == [("winter", 1), ("spring", 2)]
    assert plan.results[1].finance.start_gold == 5000   # 冬季不种植，资金不变


# ---------------------------------------------------------------------------
# 命令行
# ---------------------------------------------------------------------------

def run(*args, script="main.py"):
    return subprocess.run([sys.executable, script, *args], cwd=ROOT, capture_output=True, text=True, encoding="utf-8")


def test_cli_multi_season():
    r = run("--provider", "mock", "--season", "summer,fall", "--start-gold", "3000")
    assert r.returncode == 0, r.stderr
    assert "跨季连续规划（第 1 年夏季 → 第 1 年秋季）" in r.stdout
    assert "| 第 1 年夏季 | 3000g |" in r.stdout


@pytest.mark.parametrize("args, message", [(["--season", "spring,fall"], "季节必须连续"),
                                           (["--start-gold", "-1"], "期初资金")])
def test_cli_rejects_bad_input(args, message):
    r = run("--provider", "mock", *args)
    assert r.returncode == 2 and message in r.stderr


def test_sv_simulate_corn():
    r = run("simulate", "玉米", "--season", "summer", "--tiles", "8", script=SV)
    assert r.returncode == 0
    assert "共 11 次" in r.stdout and "秋季第 27 天" in r.stdout
    assert "= 4400g" in r.stdout


def test_sv_simulate_out_of_season():
    assert run("simulate", "玉米", "--season", "spring", script=SV).returncode == 3


def test_mock_summer_plantings_fit_budget(data):
    items = [(p["day"], p["crop"], p["tiles"]) for p in MOCK_SUMMER["plantings"]]
    assert compute_finance(plant(data, "summer", 1, *items), start_gold=2424).warnings == []
