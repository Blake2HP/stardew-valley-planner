"""联机模式（--players / --roles）：参数、分工解析、按玩家的日程、联机规则与校验。"""

import subprocess
import sys
from pathlib import Path

import pytest

from stardew_planner import generate_plan, generate_strategy
from stardew_planner.coop import (
    apply_season_end, check_division, check_players, merge_day, parse_player_goals, parse_roles,
)
from stardew_planner.generator import StrategyParseError, validate_payload
from stardew_planner.json_utils import parse_llm_json
from stardew_planner.llm_client import MOCK_RESPONSE, MockClient
from stardew_planner.prompts import SYSTEM_PROMPT, build_system_prompt
from stardew_planner.schedule import merge_player_goals

ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# 任务 1：参数与透传
# ---------------------------------------------------------------------------

def test_parse_roles():
    assert parse_roles("P1:种田, p2：下矿；P3:钓鱼", 3) == {"P1": "种田", "P2": "下矿", "P3": "钓鱼"}
    assert parse_roles({"P2": "钓鱼"}, 2) == {"P2": "钓鱼"}
    assert parse_roles(None, 4) == {} and parse_roles("", 1) == {}


@pytest.mark.parametrize("spec, players, message", [
    ("P3:钓鱼", 2, "超出了玩家人数"), ("P2:钓鱼", 1, "超出了玩家人数"), ("P1 种田", 2, "写法应为"),
    ("P1:种田,P1:钓鱼", 2, "写了两次"),
])
def test_bad_roles(spec, players, message):
    with pytest.raises(ValueError, match=message):
        parse_roles(spec, players)


@pytest.mark.parametrize("value", [0, 5, "两"])
def test_bad_players(value):
    with pytest.raises(ValueError, match="玩家人数"):
        check_players(value)


def test_cli_rejects_five_players():
    r = subprocess.run([sys.executable, "main.py", "--provider", "mock", "--players", "5"], cwd=ROOT,
                       capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 2 and "--players" in r.stderr


def test_strategy_keeps_players_and_roles(data, indexed_store):
    result = generate_strategy("种田", "稳健", "spring", client=MockClient(), data=data, guide_store=indexed_store,
                               players=2, roles="P1:种田, P2:钓鱼")
    assert result.players == 2 and result.roles == {"P1": "种田", "P2": "钓鱼"}
    solo = generate_strategy("种田", "稳健", "spring", client=MockClient(), data=data, guide_store=indexed_store)
    assert solo.players == 1 and solo.roles == {}
    with pytest.raises(ValueError, match="超出了玩家人数"):
        generate_strategy("种田", "稳健", "spring", client=MockClient(), data=data, roles="P2:钓鱼")


# ---------------------------------------------------------------------------
# 任务 2 / 5：JSON 结构、系统 Prompt、解析、合并日程、渲染
# ---------------------------------------------------------------------------

def coop_payload(players=2, **override):
    goals = {str(d): {"P1": MOCK_RESPONSE["daily_goals"][str(d)], "P2": "海滩钓鱼"} for d in range(1, 29)}
    if players == 3:
        for g in goals.values():
            g["P3"] = "下矿井推进"
    goals.update(override)
    return {**MOCK_RESPONSE, "daily_goals": goals}


def test_validate_coop_goals():
    parsed = validate_payload(coop_payload(), players=2)
    assert parsed.player_goals[1] == {"P1": "种下防风草并清理农场", "P2": "海滩钓鱼"}
    assert parsed.goals[1] == "P1：种下防风草并清理农场；P2：海滩钓鱼"
    # 任务列表也接受，按“，”拼起来
    parsed = validate_payload(coop_payload(**{"2": {"P1": ["领取鱼竿", "海滩钓鱼"], "P2": ["种地"]}}), players=2)
    assert parsed.player_goals[2] == {"P1": "领取鱼竿，海滩钓鱼", "P2": "种地"}


@pytest.mark.parametrize("override, message", [
    ({"3": "海滩钓鱼"}, "要写成按玩家的对象"),
    ({"3": {"P1": "钓鱼"}}, "缺少 P2"),
    ({"3": {"P1": "钓鱼", "P2": "下矿", "P3": "采集"}}, "多余的玩家"),
])
def test_bad_coop_goals(override, message):
    with pytest.raises(StrategyParseError, match=message):
        validate_payload(coop_payload(**override), players=2)


def test_single_player_unchanged():
    parsed = validate_payload(MOCK_RESPONSE)
    assert parsed.player_goals == {} and parsed.goals[1] == "种下防风草并清理农场"
    with pytest.raises(StrategyParseError, match="daily_goals"):
        validate_payload(coop_payload())                     # 单人模式不接受按玩家的对象


def test_json_repair_keeps_nested_goals():
    text = "{'daily_goals': {'1': {'P1': '种防风草', 'P2': '钓鱼',}},}"
    assert parse_llm_json(text, expect=dict).data["daily_goals"]["1"] == {"P1": "种防风草", "P2": "钓鱼"}


def test_merge_by_player():
    goals = {1: {"P1": "钓鱼", "P2": "下矿"}, 2: {"P1": "钓鱼。", "P2": "下矿"}, 3: {"P1": "钓鱼", "P2": "种地"}}
    blocks = merge_player_goals(goals)
    assert [(b.start, b.end) for b in blocks] == [(1, 2), (3, 3)]
    assert blocks[0].players == (("P1", "钓鱼"), ("P2", "下矿")) and blocks[0].goal == "P1：钓鱼；P2：下矿"


def test_merge_day_normalizes():
    assert merge_day({"P2": "钓鱼。", "P1": " 种地 "}) == "P1：种地；P2：钓鱼"


def test_system_prompt():
    assert build_system_prompt(1) == SYSTEM_PROMPT
    coop = build_system_prompt(3)
    assert coop.startswith(SYSTEM_PROMPT) and "## 联机模式（3 人：P1、P2、P3）" in coop
    assert "时间不会暂停" in coop and "钓鱼的单日期望收益要保守估计" in coop and '"player"' not in SYSTEM_PROMPT
    assert "tool_upgrades 每条都要写 player" in coop


def test_season_end_goes_to_player():
    day = {"P1": "收完作物", "P2": "钓鱼"}
    old = merge_day(day)
    assert apply_season_end(day, old, old + "；清理农场/停止种植")["P1"] == "收完作物；清理农场/停止种植"
    day = {"P1": "钓鱼", "P2": "清理农场/停止种植"}
    old = merge_day(day)
    assert apply_season_end(day, old, old + "（保留玉米）")["P2"] == "清理农场/停止种植（保留玉米）"


def test_division_check():
    same = {d: {"P1": "钓鱼", "P2": "钓鱼"} for d in range(1, 29)}
    (w,) = check_division(same, {})
    assert w.startswith("⚠️ 联机校验：有 28 天所有玩家的目标完全相同")
    split = {d: {"P1": "种防风草", "P2": "海滩钓鱼"} for d in range(1, 29)}
    assert check_division(split, {"P1": "种田", "P2": "钓鱼"}) == []
    (w,) = check_division(split, {"P2": "下矿"})
    assert "P2 的分工是“下矿”" in w


def test_strategy_end_to_end(data, indexed_store):
    result = generate_strategy("全收集", "稳健", "spring", client=MockClient(), data=data, guide_store=indexed_store,
                               players=3, roles="P1:种田, P2:下矿, P3:钓鱼")
    assert result.attempts == 1 and result.json_parse_method == "strict"      # 一次就符合联机格式
    assert not any(p.startswith("⚠️ 联机校验") for p in result.problems)
    assert result.player_goals[28]["P1"].endswith("清理农场/停止种植")
    md = result.markdown
    assert ("- **第 1 天**：\n  - **P1**: 种下防风草并清理农场\n  - **P2**: 下矿井推进\n"
            "  - **P3**: 海滩钓鱼攒钱") in md


def test_prompt_mentions_coop(data, indexed_store):
    class Recorder(MockClient):
        def complete(self, system, messages):
            self.system, self.user = system, messages[0]["content"]
            return super().complete(system, messages)

    client = Recorder()
    generate_strategy("全收集", "稳健", "spring", client=client, data=data, guide_store=indexed_store, players=2,
                      roles="P2:钓鱼")
    assert "- 联机：2 人联机（P1 为房主；分工：P2：钓鱼）" in client.user and "## 联机模式（2 人" in client.system
    solo = Recorder()
    generate_strategy("全收集", "稳健", "spring", client=solo, data=data, guide_store=indexed_store)
    assert "- 联机：单人" in solo.user and "联机模式" not in solo.system


def test_fallback_when_model_ignores_coop_format(data, indexed_store):
    class Solo:
        def complete(self, system, messages):
            import json
            return json.dumps(MOCK_RESPONSE, ensure_ascii=False)

    result = generate_strategy("种田", "稳健", "spring", client=Solo(), data=data, guide_store=indexed_store,
                               players=2, revise_rounds=0)
    assert result.player_goals[1] == {"P1": "种下防风草并清理农场", "P2": "（无安排）"}
    assert any("兜底" in w for w in result.season_warnings)


def test_plan_two_seasons(data, indexed_store):
    plan = generate_plan("全收集", "稳健", "spring,summer", client=MockClient(), data=data,
                         guide_store=indexed_store, players=2)
    assert [r.players for r in plan.results] == [2, 2] and plan.results[1].player_goals[1]["P2"] == "下矿井推进"


# ---------------------------------------------------------------------------
# 任务 3：多人体力
# ---------------------------------------------------------------------------

def test_coop_energy_threshold(data):
    from stardew_planner.energy import check_energy, describe_energy, heavy_days, watered_tiles
    from stardew_planner.farm_state import resolve_plantings

    plantings, _ = resolve_plantings([{"day": 1, "crop": "防风草", "tiles": 80}], data, "spring", 1)
    tiles = watered_tiles(plantings)                     # 第 1-4 天 80 格 × 2 = 160 点
    assert heavy_days(tiles, data.energy_rules) and not heavy_days(tiles, data.energy_rules, players=2)
    assert heavy_days(watered_tiles(resolve_plantings([{"day": 1, "crop": "防风草", "tiles": 120}], data, "spring", 1)[0]),
                      data.energy_rules, players=2)      # 120 × 2 = 240 > 200
    goals = {d: "P1：种地；P2：钓鱼" for d in range(1, 29)}
    big, _ = resolve_plantings([{"day": 1, "crop": "防风草", "tiles": 120}], data, "spring", 1)
    (w,) = check_energy(goals, big, (), data.energy_rules, players=2)
    assert w.startswith("⚠️ 联机校验：体力预警：第 1-4 天 2 人合计每天要手浇最多 120 格") and "约 200 点" in w
    goals[2] = "P1：种地；P2：泡温泉"                       # 任何一个玩家补体力都算
    goals[4] = "P1：去沙龙买沙拉；P2：钓鱼"
    assert check_energy(goals, big, (), data.energy_rules, players=2) == []
    assert "每人开局最大体力 270 点（3 人联机，各自一条）" in describe_energy(data.energy_rules, players=3)
    assert "约 150 格" in describe_energy(data.energy_rules, players=3)


def test_coop_prompt_energy_scaled(data, indexed_store):
    class Recorder(MockClient):
        def complete(self, system, messages):
            self.user = messages[0]["content"]
            return super().complete(system, messages)

    client = Recorder()
    generate_strategy("种田", "稳健", "spring", client=client, data=data, guide_store=indexed_store, players=2)
    assert "浇水超过 200 点（约 100 格）" in client.user
