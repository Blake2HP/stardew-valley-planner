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
