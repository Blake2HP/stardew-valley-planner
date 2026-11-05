"""generate_strategy 端到端：用假的模型客户端验证解析、重试和渲染。"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from stardew_planner import generate_strategy
from stardew_planner.generator import StrategyParseError
from stardew_planner.llm_client import MOCK_RESPONSE
from stardew_planner.season_rules import CLEANUP_GOAL

ROOT = Path(__file__).resolve().parent.parent
SV = ROOT / ".claude" / "skills" / "stardew-data-guard" / "scripts" / "sv.py"
GOOD = json.dumps(MOCK_RESPONSE, ensure_ascii=False)


class ScriptedClient:
    """按顺序返回预设回复，并记录调用次数。"""

    def __init__(self, *responses: str):
        self.responses = list(responses)
        self.calls = 0

    def complete(self, system, messages):
        self.calls += 1
        return self.responses[min(self.calls, len(self.responses)) - 1]


def run(client, data, **kw):
    return generate_strategy("钓鱼赚钱", "休闲慢节奏", "spring", client=client, data=data, **kw)


def test_valid_response_single_call(data):
    client = ScriptedClient(GOOD)
    result = run(client, data)
    assert client.calls == 1
    assert result.attempts == 1 and result.json_parse_method == "strict"
    assert len(result.daily_goals) == 28


@pytest.mark.parametrize(
    "broken",
    [
        GOOD.replace(", ", ",, ", 3),                                 # 多逗号
        GOOD[:-2],                                                    # 漏掉结尾两个括号
        "```json\n" + GOOD.replace("}}", "},}", 1) + "\n```",         # 代码块 + 末尾逗号
    ],
    ids=["double-commas", "truncated", "fenced-trailing-comma"],
)
def test_broken_json_is_repaired_without_retry(data, broken):
    pytest.importorskip("json_repair")
    client = ScriptedClient(broken)
    result = run(client, data)
    assert client.calls == 1, "语法错误应在本地修复，不应触发重试"
    assert result.json_parse_method in {"cleaned", "repaired"}
    assert result.daily_goals[1] == MOCK_RESPONSE["daily_goals"]["1"]


def test_schema_error_triggers_one_retry(data):
    incomplete = json.loads(GOOD)
    del incomplete["daily_goals"]["5"]
    client = ScriptedClient(json.dumps(incomplete, ensure_ascii=False), GOOD)
    result = run(client, data)
    assert client.calls == 2 and result.attempts == 2


def test_lenient_fallback_fills_missing_days(data):
    incomplete = json.loads(GOOD)
    del incomplete["daily_goals"]["5"]
    client = ScriptedClient(json.dumps(incomplete, ensure_ascii=False))
    result = run(client, data, max_retries=1)
    assert client.calls == 2
    assert result.daily_goals[5] == "自由安排"


def test_unparseable_response_raises(data):
    with pytest.raises(StrategyParseError):
        run(ScriptedClient("抱歉，我无法回答。"), data, max_retries=0)


def test_rendered_markdown_contains_all_sections(data):
    md = run(ScriptedClient(GOOD), data).markdown
    for heading in ["## 🧭 宏观策略指导", "## 📌 关键时间节点", "## ⚠️ 换季校验", "## 📅 每日核心目标"]:
        assert heading in md
    assert "**第 3-4 天**：海滩钓鱼攒钱" in md
    assert CLEANUP_GOAL in md


# ---------------------------------------------------------------------------
# skill 的 sv.py 命令行：退出码约定
# ---------------------------------------------------------------------------

def sv(*args):
    return subprocess.run([sys.executable, str(SV), *args], cwd=ROOT, capture_output=True,
                          text=True, encoding="utf-8")


def test_sv_known_crop():
    r = sv("artisan", "草莓")
    assert r.returncode == 0 and "120 × 3.0 = 360g" in r.stdout


def test_sv_profit_waits_for_seed_availability():
    r = sv("profit", "草莓")
    assert r.returncode == 0
    assert "实际可用生长天数 = 28 − 种植日 13 + 1 = 16" in r.stdout
    assert "= 2（种子 1 份）" in r.stdout


def test_sv_missing_data_exit_2():
    assert sv("crop", "阿比盖尔").returncode == 2


def test_sv_check_schedule_exit_codes(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"20": "种花椰菜", "28": "收完作物"}, ensure_ascii=False), encoding="utf-8")
    good = tmp_path / "good.json"
    good.write_text(json.dumps({"20": "种土豆", "28": f"出售；{CLEANUP_GOAL}"}, ensure_ascii=False), encoding="utf-8")
    assert sv("check-schedule", str(bad), "--season", "spring").returncode == 3
    assert sv("check-schedule", str(good), "--season", "spring").returncode == 0
