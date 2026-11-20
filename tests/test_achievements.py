"""Steam 成就目标（--achievements / --achievement-progress）：数据、缺口、Prompt、输出字段与成就校验。"""

import subprocess
import sys
from pathlib import Path

import pytest

from stardew_planner import generate_strategy
from stardew_planner.achievements import (
    Progress, load_achievement_rules, parse_progress, resolve_goals, season_targets,
)
from stardew_planner.llm_client import MockClient

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def rules():
    return load_achievement_rules()


# ---------------------------------------------------------------------------
# 任务 1：数据与参数
# ---------------------------------------------------------------------------

def test_data_file(rules, data):
    by_key = {a.key: a for a in rules.achievements}
    assert by_key["legend"].target == 10_000_000 and by_key["legend"].name == "千万富翁"
    assert by_key["millionaire"].target == 1_000_000 and by_key["millionaire"].name == "百万富翁"
    poly = by_key["polyculture"]
    assert poly.target == 15 and len(poly.crops) == 28 and "多作种植" in poly.aliases
    known = {c.name_en for c in data.crops}
    assert set(poly.crops) <= known  # 28 种作物都在 crops.json 里
    popular = by_key["popular"]
    assert (popular.target, popular.hearts, popular.points_per_heart) == (20, 5, 250)
    assert data.achievement_rules is not None


def test_resolve_goals(rules):
    goals = resolve_goals("多作种植, 万人迷，Legend", rules)
    assert [(g.achievement.key, g.label) for g in goals] == [
        ("polyculture", "多作种植"), ("popular", "万人迷"), ("legend", "Legend")]
    assert resolve_goals(None, rules) == [] and resolve_goals("", rules) == []
    assert len(resolve_goals("混合栽培,多作种植", rules)) == 1  # 同一个成就只算一次
    with pytest.raises(ValueError, match="未知的成就：钓鱼大师"):
        resolve_goals("钓鱼大师", rules)
    with pytest.raises(ValueError, match="没有成就数据"):
        resolve_goals("万人迷", None)


def test_parse_progress(rules, data):
    p = parse_progress("防风草=15, Potato:7，万人迷=12；千万富翁=50000", rules, data.crops)
    assert p.shipped == {"防风草": 15, "土豆": 7} and p.friends == 12 and p.earned == 50000
    assert parse_progress(None, rules, data.crops) == Progress()
    assert parse_progress({"万人迷": 3}, rules, data.crops).friends == 3


@pytest.mark.parametrize("spec, message", [
    ("防风草", "写法应为"),
    ("钓鱼大师=3", "既不是作物"),
    ("混合栽培=3", "既不是作物"),
])
def test_bad_progress(rules, data, spec, message):
    with pytest.raises(ValueError, match=message):
        parse_progress(spec, rules, data.crops)


def test_cli_rejects_unknown_achievement():
    r = subprocess.run([sys.executable, "main.py", "--provider", "mock", "--achievements", "钓鱼大师"], cwd=ROOT,
                       capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 2 and "未知的成就：钓鱼大师" in r.stderr


def test_strategy_keeps_achievements(data, indexed_store):
    result = generate_strategy("种田", "稳健", "spring", client=MockClient(), data=data, guide_store=indexed_store,
                               achievements="多作种植", achievement_progress="防风草=3")
    assert [g.achievement.key for g in result.achievements] == ["polyculture"]
    assert result.achievement_progress.shipped == {"防风草": 3}
    solo = generate_strategy("种田", "稳健", "spring", client=MockClient(), data=data, guide_store=indexed_store)
    assert solo.achievements == [] and solo.achievement_progress == Progress()


# ---------------------------------------------------------------------------
# 任务 2：本季缺口与 Prompt 注入
# ---------------------------------------------------------------------------

class RecordingClient(MockClient):
    def complete(self, system, messages):
        self.system, self.prompt = system, messages[0]["content"]
        return super().complete(system, messages)


def test_spring_targets(rules, data):
    goals = resolve_goals("多作种植,万人迷,千万富翁", rules)
    t = season_targets(goals, parse_progress("防风草=15,土豆=6", rules, data.crops), data.crops, "spring", 1)
    assert {c.crop.name: c.need for c in t.crops} == {"花椰菜": 15, "青豆": 15, "甘蓝": 15, "土豆": 9, "草莓": 15}
    assert {c.crop.name for c in t.optional} == {"咖啡豆", "大黄"}   # 旅行货车、沙漠绿洲：可选
    assert t.later == ["大蒜（第 2 年起）"] and t.shipped_done == ["防风草"]
    assert (t.friends_left, t.seasons_left, t.friend_target) == (20, 4, 5)
    assert [(g.achievement.key, gap) for g, gap in t.earnings] == [("legend", 10_000_000)]


def test_year_two_and_fall_targets(rules, data):
    goals = resolve_goals("混合栽培", rules)
    spring2 = season_targets(goals, Progress(), data.crops, "spring", 2)
    assert "大蒜" in {c.crop.name for c in spring2.crops} and spring2.later == []
    fall = season_targets(goals, Progress(shipped={"玉米": 15}), data.crops, "fall", 1)
    names = {c.crop.name for c in fall.crops}
    assert "小麦" in names and "玉米" not in names and "玉米" in fall.shipped_done  # 夏秋作物秋季没运够还要种
    assert "甜菜" in {c.crop.name for c in fall.optional}                          # 甜菜种子在沙漠绿洲
    assert "洋蓟（第 2 年起）" in fall.later


def test_friend_target_spreads_over_remaining_seasons(rules, data):
    goals = resolve_goals("万人迷", rules)
    p = Progress(friends=4, befriended=("海莉", "艾米丽"), seasons_done=1)
    t = season_targets(goals, p, data.crops, "summer", 1)
    assert (t.friends_left, t.seasons_left, t.friend_target) == (14, 3, 5)  # ⌈(20 − 4 − 2) / 3⌉ = 5


def test_prompt_block(data, indexed_store):
    client = RecordingClient()
    generate_strategy("种田", "稳健", "spring", client=client, data=data, guide_store=indexed_store,
                      achievements="多作种植,万人迷", achievement_progress="防风草=15")
    block = client.prompt.split("### 【🏆 成就规划目标】")[1].split("### 相关攻略片段")[0]
    assert "花椰菜 15 个" in block and "已经运够：防风草" in block and "可选" in block
    assert "多作种植（混合栽培：" in block and "万人迷（与 20 个人" in block
    assert "本季至少要给 5 位新的村民送礼" in block and "5 颗心 = 5 × 250 = 1250 点" in block


def test_no_achievements_prompt_unchanged(data, indexed_store):
    client = RecordingClient()
    generate_strategy("种田", "稳健", "spring", client=client, data=data, guide_store=indexed_store)
    assert "成就" not in client.prompt and "\n\n### 相关攻略片段" in client.prompt
    assert "\n\n\n### 相关攻略片段" not in client.prompt


def test_progress_carries_across_seasons(data, indexed_store):
    from stardew_planner import generate_plan
    out = generate_plan("种田", "稳健", ["spring", "summer"], client=MockClient(), data=data, guide_store=indexed_store,
                        achievements="多作种植,万人迷,千万富翁")
    spring, summer = out.results
    end = spring.achievement_progress_end
    assert summer.achievement_progress == end and end.seasons_done == 1
    assert end.shipped["花椰菜"] > 0 and end.earned > 0 and end.befriended
    assert summer.achievement_targets.seasons_left == 3
    assert summer.achievement_progress_end.seasons_done == 2


# ---------------------------------------------------------------------------
# 任务 3：achievement_milestones 与系统 Prompt
# ---------------------------------------------------------------------------

def test_system_prompt_rules():
    from stardew_planner.prompts import SYSTEM_PROMPT, build_system_prompt
    assert build_system_prompt() == SYSTEM_PROMPT                     # 不选成就：系统 Prompt 不变
    rules_text = build_system_prompt(achievements=True)
    assert "## 成就规划" in rules_text and "achievement_plan" in rules_text and "achievement_milestones" in rules_text
    assert "## 联机模式" in build_system_prompt(2, achievements=True)  # 和联机规则可以同时出现


def test_milestones_are_parsed_leniently():
    from stardew_planner.generator import validate_payload
    from stardew_planner.llm_client import MOCK_RESPONSE
    payload = {**MOCK_RESPONSE, "achievement_milestones": [
        {"day": 9, "achievement": "万人迷", "goal": "送海莉黄水仙"},
        {"day": "3", "goal": "种 15 颗土豆"},
        {"day": 40, "goal": "超出范围"}, {"goal": "没有日期"}, "随便写的",
    ]}
    parsed = validate_payload(payload)
    assert parsed.achievement_milestones == [
        {"day": 3, "achievement": "", "goal": "种 15 颗土豆"},
        {"day": 9, "achievement": "万人迷", "goal": "送海莉黄水仙"},
    ]
    assert validate_payload(MOCK_RESPONSE).achievement_milestones == []  # 字段可选，不影响普通生成


def test_strategy_renders_achievement_section(data, indexed_store):
    client = RecordingClient()
    result = generate_strategy("种田", "稳健", "spring", client=client, data=data, guide_store=indexed_store,
                               achievements="多作种植,万人迷")
    assert "## 成就规划" in client.system
    assert result.macro_strategy["achievement_plan"] and len(result.achievement_milestones) == 2
    md = result.markdown
    assert "## 🏆 成就规划" in md and "**实现思路**" in md and "| 第 1 天 | 成就目标 |" in md
    assert md.index("## 🏆 成就规划") < md.index("## 📅 每日核心目标")


def test_plain_strategy_has_no_achievement_section(data, indexed_store):
    client = RecordingClient()
    result = generate_strategy("种田", "稳健", "spring", client=client, data=data, guide_store=indexed_store)
    assert "成就" not in result.markdown and "## 成就规划" not in client.system
    assert result.achievement_targets is None and result.achievement_milestones == []


# ---------------------------------------------------------------------------
# 任务 4：成就校验与自动修正
# ---------------------------------------------------------------------------

def _targets(rules, data, spec="多作种植,万人迷", progress=None):
    return season_targets(resolve_goals(spec, rules), progress or Progress(), data.crops, "spring", 1)


FULL = {"achievement_plan": "思路"}
MILESTONES = [{"day": 1, "achievement": "混合栽培", "goal": "种土豆"}]


def test_polyculture_check(rules, data):
    from stardew_planner.achievements import check_achievements
    t = _targets(rules, data, "多作种植", parse_progress("花椰菜=15,青豆=15,甘蓝=15,草莓=15", rules, data.crops))
    units = {"防风草": 15.0, "土豆": 12.5}
    problems = check_achievements(t, units, {"防风草", "土豆"}, {}, FULL, MILESTONES)
    assert problems == ["⚠️ 成就校验：目标包含“多作种植”，但本季安排种植的土豆预计只能直接出货 12.5 个，不足 15 个。请修改种植清单！"]
    problems = check_achievements(t, {"土豆": 20}, {"土豆"}, {}, FULL, MILESTONES)
    assert problems == ["⚠️ 成就校验：目标包含“多作种植”，但本季没有安排种植防风草（要收获并出货 15 个）。请修改种植清单！"]
    assert check_achievements(t, {"防风草": 15, "土豆": 18.75}, {"防风草", "土豆"}, {}, FULL, MILESTONES) == []


def test_popular_check(rules, data):
    from stardew_planner.achievements import check_achievements
    t = _targets(rules, data, "万人迷", Progress(befriended=("海莉",)))
    gifts = {2: ["海莉"], 9: ["阿比盖尔"], 16: ["潘妮"]}
    problems = check_achievements(t, {}, set(), gifts, FULL, MILESTONES)
    assert problems == [
        "⚠️ 成就校验：目标包含“万人迷”，但第 22-28 天没有安排送礼。请每周至少安排一天“送{村民}{礼物}”！",
        "⚠️ 成就校验：目标包含“万人迷”，但本季只给 2 位新的村民安排了送礼（潘妮、阿比盖尔），至少需要 5 位。请增加送礼对象！",
    ]
    gifts = {2: ["阿比盖尔", "潘妮"], 9: ["莉亚"], 16: ["玛鲁"], 23: ["海莉", "艾米丽"]}
    assert check_achievements(t, {}, set(), gifts, FULL, MILESTONES) == []
    done = _targets(rules, data, "万人迷", Progress(friends=20))  # 已经达成：不再检查送礼
    assert check_achievements(done, {}, set(), {}, FULL, MILESTONES) == []


def test_plan_and_milestones_required(rules, data):
    from stardew_planner.achievements import check_achievements
    t = _targets(rules, data, "千万富翁")
    assert check_achievements(t, {}, set(), {}, FULL, MILESTONES) == []  # 赚钱类只提示，不校验金额
    problems = check_achievements(t, {}, set(), {}, {}, [])
    assert [p.split("：")[1].split("，")[0] for p in problems] == [
        "macro_strategy 里没有 achievement_plan", "没有给出 achievement_milestones"]


def test_checks_reach_the_revise_loop(data, indexed_store):
    import json

    from stardew_planner.llm_client import MOCK_RESPONSE

    class Revising(MockClient):
        calls = []

        def complete(self, system, messages):
            self.calls.append(messages[-1]["content"])
            payload = json.loads(super().complete(system, messages))
            if len(self.calls) > 1:  # 修正版：第 1 周给阿比盖尔送紫水晶
                payload["daily_goals"] = {**payload["daily_goals"], "3": "领取鱼竿，海滩钓鱼，送阿比盖尔紫水晶"}
            return json.dumps(payload, ensure_ascii=False)

    original = MOCK_RESPONSE["daily_goals"]["3"]
    client = Revising()
    result = generate_strategy("种田", "稳健", "spring", client=client, data=data, guide_store=indexed_store,
                               achievements="多作种植,万人迷", revise_rounds=1)
    assert any(p.startswith("⚠️ 成就校验：目标包含“多作种植”") for p in result.initial_problems)
    assert "成就校验：目标包含“万人迷”，但第 1-7 天没有安排送礼" in client.calls[1]  # 反馈给模型
    assert result.revisions[0]["accepted"]
    assert not any("第 1-7 天没有安排送礼" in p for p in result.problems)
    assert "第 1-7 天没有安排送礼" not in result.markdown and "成就校验" in result.markdown
    assert MOCK_RESPONSE["daily_goals"]["3"] == original  # 没有改到共享的 mock 数据


def test_plain_generation_has_no_achievement_checks(data, indexed_store):
    result = generate_strategy("种田", "稳健", "spring", client=MockClient(), data=data, guide_store=indexed_store)
    assert not any("成就校验" in p for p in result.problems + result.season_warnings)


# ---------------------------------------------------------------------------
# 混合栽培只算直接出货：加工前先留够，留给收集包的不算
# ---------------------------------------------------------------------------

def test_processing_keeps_shipping_amount(data):
    from stardew_planner.bonuses import NO_BONUSES
    from stardew_planner.processing import simulate_processing
    potato = next(c for c in data.crops if c.name == "土豆")
    harvests = [(7, potato, 20), (14, potato, 20)]  # 每次 20 格 × 1.25 = 25 个
    free = simulate_processing(harvests, data.artisan_rules_for, {"小桶": 100}, NO_BONUSES)
    kept = simulate_processing(harvests, data.artisan_rules_for, {"小桶": 100}, NO_BONUSES, keep={"土豆": 15})
    assert free.held_units["土豆"] == pytest.approx(50)
    assert kept.held_units["土豆"] == pytest.approx(35)   # 先留 15 个直接卖，其余照常加工
    assert kept.net < free.net                            # 少加工 15 个，加工净增变少


def test_shipping_counts_exclude_processing(data, indexed_store):
    goal = dict(client=MockClient(), data=data, guide_store=indexed_store, kegs=200, revise_rounds=0)
    plain = generate_strategy("种田", "稳健", "spring", **goal)
    assert plain.finance.processed_units.get("防风草", 0) > 0          # 不选成就：小桶照常吃掉防风草
    result = generate_strategy("种田", "稳健", "spring", achievements="多作种植", **goal)
    f = result.finance
    assert f.processed_units.get("防风草", 0) == pytest.approx(0)       # 15 格防风草全部留着直接出货
    assert result.achievement_progress_end.shipped["防风草"] == 15
    assert not any("防风草" in p for p in result.problems if p.startswith("⚠️ 成就校验"))


def test_bundle_units_are_not_shipped(data, indexed_store):
    result = generate_strategy("种田", "稳健", "spring", client=MockClient(), data=data, guide_store=indexed_store,
                               achievements="多作种植", route="community_center", revise_rounds=0)
    assert result.finance.bundle_units.get("防风草") == 1
    assert result.achievement_progress_end.shipped["防风草"] == 14
    assert ("⚠️ 成就校验：目标包含“多作种植”，但本季安排种植的防风草预计只能直接出货 14 个"
            "（预计收获 15 个，其中 1 个留给收集包），不足 15 个。请修改种植清单！") in result.problems
