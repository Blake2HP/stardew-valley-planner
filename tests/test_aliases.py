"""作物别名：官方中文名与本项目叫法不同时，各处都要认得。"""

import subprocess
import sys
from pathlib import Path

from stardew_planner.bonuses import resolve_bonuses
from stardew_planner.farm_state import resolve_plantings
from stardew_planner.season_rules import _planted_crops, check_gifts, check_plantings, check_plantings_match

ROOT = Path(__file__).resolve().parent.parent
SV = str(ROOT / ".claude" / "skills" / "stardew-data-guard" / "scripts" / "sv.py")


def test_official_names_recorded(crops):
    aliases = {c.name: c.aliases for c in crops.values() if c.aliases}
    assert aliases == {"甘蓝": ("甘蓝菜",), "大蒜": ("蒜",), "虞美人": ("虞美人花",), "番茄": ("西红柿",)}
    assert crops["虞美人"].names == ("虞美人花", "虞美人")      # 长的在前


def test_plantings_list_accepts_official_names(data):
    raw = [{"day": 1, "crop": "虞美人花", "tiles": 1}, {"day": 1, "crop": "西红柿", "tiles": 1}]
    plantings, warnings = resolve_plantings(raw, data, "summer", 1)
    assert warnings == [] and [p.crop.name for p in plantings] == ["虞美人", "番茄"]


def test_goal_detection_with_aliases(data):
    assert [c.name for c in _planted_crops("种西红柿和虞美人花", data.crops)] == ["虞美人", "番茄"]
    assert [c.name for c in _planted_crops("补种甘蓝菜", data.crops)] == ["甘蓝"]
    (w,) = check_plantings({3: "种蒜"}, data, "spring", year=1)          # 第 1 年买不到大蒜
    assert w.crop == "大蒜" and "第 2 年起" in w.message


def test_goal_and_list_match_across_names(data):
    plantings, _ = resolve_plantings([{"day": 1, "crop": "番茄", "tiles": 1}], data, "summer", 1)
    assert check_plantings_match({1: "种西红柿"}, plantings, data) == []
    plantings, _ = resolve_plantings([{"day": 1, "crop": "西红柿", "tiles": 1}], data, "summer", 1)
    assert check_plantings_match({1: "种番茄"}, plantings, data) == []


def test_gift_vocabulary_includes_aliases(data):
    # 潘妮最爱虞美人花；送“西红柿”能被识别出来（海莉不喜欢蔬菜，所以报警）
    ok = {2: "送潘妮虞美人花", 5: "送艾利欧特龙虾", 13: "送阿比盖尔紫水晶"}
    assert check_gifts(ok, data, "fall") == []
    (w,) = check_gifts({**ok, 20: "送海莉西红柿"}, data, "fall")
    assert "海莉不喜欢西红柿" in w.message


def test_sv_finds_crop_by_alias():
    r = subprocess.run([sys.executable, SV, "crop", "西红柿"], cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0 and "【番茄 / Tomato】" in r.stdout
