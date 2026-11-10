"""花蜜：售价 = 100 + 花的基础售价 × 2。期望值来自 https://stardewvalleywiki.com/Honey 的售价表（核对日期 2026-11-10）。"""

import subprocess
import sys
from pathlib import Path

import pytest

from stardew_planner.analysis import build_data_context, crop_note

ROOT = Path(__file__).resolve().parent.parent
SV = str(ROOT / ".claude" / "skills" / "stardew-data-guard" / "scripts" / "sv.py")


@pytest.mark.parametrize("flower, price", [
    ("郁金香", 160), ("蓝爵", 200), ("夏季亮片", 280), ("虞美人", 380), ("向日葵", 260), ("玫瑰仙子", 680),
])
def test_wiki_honey_table(data, crops, flower, price):
    assert data.honey.price_for(crops[flower]) == price


def test_only_flowers(data, crops):
    assert data.honey.price_for(crops["草莓"]) is None
    assert "蜂蜜" not in crop_note(crops["草莓"], data.honey)


def test_note_in_prompt(data):
    notes = build_data_context(data, "summer")["crop_table"]
    assert "向日葵蜂蜜，售价 = 100 + 80 × 2 = 260g" in notes


def test_sv_artisan_for_flower():
    out = subprocess.run([sys.executable, SV, "artisan", "玫瑰仙子", "--professions", "artisan"], cwd=ROOT,
                         capture_output=True, text=True, encoding="utf-8", env={"PYTHONIOENCODING": "utf-8",
                                                                                  "SYSTEMROOT": __import__("os").environ.get("SYSTEMROOT", "")})
    assert out.returncode == 0
    assert "100 + 290 × 2 = 680g" in out.stdout and "= 952g" in out.stdout   # 工匠 +40%（wiki 表：952g）
