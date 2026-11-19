"""联机模式（--players / --roles）：参数、分工解析、按玩家的日程、联机规则与校验。"""

import subprocess
import sys
from pathlib import Path

import pytest

from stardew_planner import generate_strategy
from stardew_planner.coop import check_players, parse_roles
from stardew_planner.llm_client import MockClient

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
