"""预设玩法、节奏和给新手看的优劣势说明（data/playstyles.json），以及网页 / 命令行里的展示。"""

import json
import subprocess
import sys
from pathlib import Path

from stardew_planner.analysis import BIRTHDAY_MULTIPLIER, LOVED_POINTS
from stardew_planner.playstyles import describe_presets, load_presets

ROOT = Path(__file__).resolve().parent.parent


def test_every_preset_has_pros_cons_and_audience():
    presets = load_presets()
    assert [p.name for p in presets.playstyles] == ["种田极限赚钱", "钓鱼赚钱，兼顾种田", "酿酒加工为主", "社交送礼优先",
                                                    "挖矿与全收集"]
    assert [p.name for p in presets.pacings] == ["休闲慢节奏", "稳健", "速通极限"]
    for p in presets.playstyles + presets.pacings:
        assert p.summary and p.pros and p.cons and p.suited_for


def test_numbers_match_project_data(data):
    """说明里的数字必须和数据文件一致（数据改了这里要跟着改）。"""
    text = json.dumps(json.loads((ROOT / "data" / "playstyles.json").read_text(encoding="utf-8")), ensure_ascii=False)
    wine = next(r for r in data.artisan_rules if r.product == "果酒")
    assert f"果酒 = 水果基础售价 × {wine.multiplier:g}" in text and f"果酒约 {wine.processing_days} 天" in text
    assert f"80 × 8 = {LOVED_POINTS * BIRTHDAY_MULTIPLIER}" in text and (LOVED_POINTS, BIRTHDAY_MULTIPLIER) == (80, 8)
    friendship = json.loads((ROOT / "data" / "npcs.json").read_text(encoding="utf-8"))["_friendship"]
    assert "每周最多送 2 次礼物" in text and "每周最多送 2 次礼物" in friendship


def test_missing_file_is_empty(tmp_path):
    presets = load_presets(tmp_path)
    assert presets.playstyles == () and presets.pacings == ()


def test_cli_lists_presets():
    r = subprocess.run([sys.executable, "main.py", "--list-playstyles"], cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8")
    assert r.returncode == 0
    assert "【酿酒加工为主】" in r.stdout and "＋ " in r.stdout and "－ " in r.stdout and "适合：" in r.stdout
    assert "【速通极限】" in r.stdout


def test_describe():
    text = describe_presets(load_presets())
    assert text.startswith("玩法风格") and "节奏偏好" in text


def test_web_options_include_presets(indexed_store):
    from fastapi.testclient import TestClient

    from stardew_planner.llm_client import MockClient
    from web.app import Settings, create_app

    settings = Settings()
    settings.provider, settings.rate_limit = "mock", 0
    app = create_app(settings, client=MockClient(), guide_store=indexed_store, warm_up=False)
    with TestClient(app) as c:
        o = c.get("/api/options").json()
    first = o["playstyles"][0]
    assert first["name"] == "种田极限赚钱" and first["pros"] and first["cons"] and first["suited_for"]
    assert [p["name"] for p in o["pacings"]] == ["休闲慢节奏", "稳健", "速通极限"]
