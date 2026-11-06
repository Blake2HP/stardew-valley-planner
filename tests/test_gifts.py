"""NPC 生日与送礼：data/npcs.json、Prompt 注入、送礼任务校验。数据来自 Stardew Valley Wiki（1.6）。"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from stardew_planner import generate_strategy
from stardew_planner.analysis import build_data_context
from stardew_planner.data_loader import DEFAULT_DATA_DIR, load_npcs
from stardew_planner.llm_client import MOCK_RESPONSE
from stardew_planner.prompts import SYSTEM_PROMPT
from stardew_planner.season_rules import check_gifts, gift_tasks

ROOT = Path(__file__).resolve().parent.parent
SV = str(ROOT / ".claude" / "skills" / "stardew-data-guard" / "scripts" / "sv.py")
# 春季其他天不送礼，只看被测的那天；生日当天的检查单独测试
SPRING_BIRTHDAYS_OK = {14: "送海莉黄水仙", 20: "送谢恩披萨"}


# ---------------------------------------------------------------------------
# 数据
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "name, season, day",
    [("海莉", "spring", 14), ("谢恩", "spring", 20), ("潘妮", "fall", 2),
     ("阿比盖尔", "fall", 13), ("塞巴斯蒂安", "winter", 10), ("莉亚", "winter", 23)],
)
def test_birthdays(data, name, season, day):
    n = data.npc_data.find(name)
    assert (n.birthday_season, n.birthday_day) == (season, day)


def test_npc_data_shape():
    nd = load_npcs(DEFAULT_DATA_DIR / "npcs.json")
    assert len(nd.npcs) == 6
    assert {i.name_en for i in nd.universal_loves} == {
        "Golden Pumpkin", "Magic Rock Candy", "Pearl", "Prismatic Shard", "Rabbit's Foot", "Stardrop Tea"}
    haley = nd.find("Haley")
    assert [i.name for i in haley.loves] == ["椰子", "水果沙拉", "粉红蛋糕", "向日葵"]
    # 海莉讨厌五彩碎片、潘妮讨厌兔子的脚：不算进她们的最爱
    assert "五彩碎片" not in {i.name for i in nd.loved_items(haley)}
    assert "兔子的脚" not in {i.name for i in nd.loved_items(nd.find("潘妮"))}


def test_missing_npc_file_is_empty(tmp_path):
    assert load_npcs(tmp_path / "npcs.json").npcs == ()


# ---------------------------------------------------------------------------
# Prompt 注入
# ---------------------------------------------------------------------------

def test_birthdays_injected_for_current_season(data):
    spring = build_data_context(data, "spring", year=1)["npc_birthdays"]
    assert "第 14 天【海莉生日】最爱：椰子、水果沙拉、粉红蛋糕、向日葵（夏季/秋季作物）" in spring
    assert "第 20 天【谢恩生日】" in spring and "潘妮" not in spring
    assert "80 × 8 = 640" in spring
    fall = build_data_context(data, "fall", year=1)["npc_birthdays"]
    assert "南瓜（本季可种）" in fall          # 阿比盖尔最爱的南瓜是秋季作物
    assert "讨厌兔子的脚" in fall               # 潘妮的通用最爱例外
    assert "本季没有收录的 NPC 过生日" in build_data_context(data, "summer")["npc_birthdays"]


def test_system_prompt_has_gift_rule():
    assert "送{NPC}{礼物}" in SYSTEM_PROMPT and "最爱或喜欢物品" in SYSTEM_PROMPT


class Recorder:
    def complete(self, system, messages):
        self.prompt = messages[0]["content"]
        return json.dumps(MOCK_RESPONSE, ensure_ascii=False)


def test_prompt_contains_birthday_section(data, indexed_store):
    client = Recorder()
    result = generate_strategy("社交", "休闲", "spring", client=client, data=data, guide_store=indexed_store)
    assert "### 本季 NPC 生日（data/npcs.json）" in client.prompt and "海莉生日" in client.prompt
    assert result.gift_warnings == []                  # mock 日程在生日当天送了喜欢的礼物
    assert "## 🎂 生日送礼" in result.markdown and "海莉生日(14日)" in result.markdown


# ---------------------------------------------------------------------------
# 送礼任务识别
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "goal, expected",
    [
        ("送海莉黄水仙", [("海莉", ["黄水仙"])]),
        ("给谢恩送披萨和防风草", [("谢恩", ["披萨", "防风草"])]),
        ("送阿比盖尔南瓜汤", [("阿比盖尔", ["南瓜汤"])]),      # 不会再拆出“南瓜”
        ("送潘妮虞美人", [("潘妮", ["虞美人"])]),               # crops.json 的叫法也能识别
        ("送莉亚果酒（小桶酿的）", [("莉亚", ["果酒"])]),       # 括号视为说明
        ("收获草莓，送海莉黄水仙", [("海莉", ["黄水仙"])]),
        ("不要送海莉防风草", []),                                # 否定
        ("别忘了送海莉黄水仙", [("海莉", ["黄水仙"])]),          # “别忘了”是提醒，不是否定
        ("给海莉准备礼物", []),                                  # 没有“送”，只是准备
        ("和海莉聊天", []),
    ],
)
def test_gift_tasks(data, goal, expected):
    assert [(n.name, items) for n, items in gift_tasks(goal, data)] == expected


# ---------------------------------------------------------------------------
# 送礼校验
# ---------------------------------------------------------------------------

def test_valid_gifts_pass(data):
    goals = {**SPRING_BIRTHDAYS_OK, 5: "送谢恩珍珠"}           # 珍珠是通用最爱
    assert check_gifts(goals, data, "spring") == []


def test_gift_not_in_preferences_warns(data):
    w = check_gifts({**SPRING_BIRTHDAYS_OK, 14: "送海莉防风草"}, data, "spring")
    assert len(w) == 1 and w[0].day == 14 and w[0].npc == "海莉"
    assert "防风草不在 npcs.json 记录的海莉最爱 / 喜欢中" in w[0].message
    assert "黄水仙" in w[0].detail                            # 给出可选的礼物


def test_universal_love_exception_warns(data):
    w = check_gifts({**SPRING_BIRTHDAYS_OK, 3: "送海莉五彩碎片"}, data, "spring")
    assert len(w) == 1 and "海莉讨厌五彩碎片" in w[0].message


def test_gift_without_item_warns(data):
    w = check_gifts({**SPRING_BIRTHDAYS_OK, 14: "送海莉生日礼物"}, data, "spring")
    assert len(w) == 1 and "没有写明礼物" in w[0].message


def test_missing_birthday_gift_warns(data):
    w = check_gifts({14: "送海莉黄水仙", 20: "下矿井"}, data, "spring")
    assert len(w) == 1 and w[0].day == 20 and "谢恩生日当天没有送礼任务" in w[0].message


def test_gift_on_wrong_day_still_misses_birthday(data):
    # 提前一天送不算生日礼物（生日 ×8 只在当天）
    w = check_gifts({13: "送海莉黄水仙", 20: "送谢恩披萨"}, data, "spring")
    assert [x.npc for x in w] == ["海莉"]


def test_no_birthdays_no_warnings(data):
    assert check_gifts({1: "种蓝莓"}, data, "summer") == []


# ---------------------------------------------------------------------------
# 命令行
# ---------------------------------------------------------------------------

def run(*args):
    return subprocess.run([sys.executable, SV, *args], cwd=ROOT, capture_output=True, text=True, encoding="utf-8")


def test_sv_npc_and_birthdays():
    r = run("npc", "海莉")
    assert r.returncode == 0 and "生日：春季第 14 天" in r.stdout and "讨厌 五彩碎片" in r.stdout
    assert "第 13 天 阿比盖尔" in run("birthdays", "fall").stdout
    assert run("npc", "刘易斯").returncode == 2


def test_sv_check_schedule_gifts(tmp_path):
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"14": "送海莉防风草", "20": "送谢恩披萨", "28": "清理农场/停止种植"},
                               ensure_ascii=False), encoding="utf-8")
    assert run("check-schedule", str(plan), "--season", "spring").returncode == 0        # 不加 --gifts 不查送礼
    r = run("check-schedule", str(plan), "--season", "spring", "--gifts")
    assert r.returncode == 3 and "防风草不在 npcs.json 记录的海莉最爱 / 喜欢中" in r.stdout
