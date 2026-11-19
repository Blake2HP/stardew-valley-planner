"""Steam 成就目标（--achievements / --achievement-progress）：数据、缺口、Prompt、输出字段与成就校验。"""

import subprocess
import sys
from pathlib import Path

import pytest

from stardew_planner import generate_strategy
from stardew_planner.achievements import Progress, load_achievement_rules, parse_progress, resolve_goals
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
