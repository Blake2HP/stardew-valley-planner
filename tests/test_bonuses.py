"""职业加成（农耕人 / 工匠 / 农业学家）与加速肥料。期望值来自 Stardew Valley Wiki（1.6）。"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from stardew_planner import generate_strategy
from stardew_planner.analysis import build_data_context, rank_crops, season_profit
from stardew_planner.bonuses import NO_BONUSES, floor_mul, load_bonus_rules, resolve_bonuses
from stardew_planner.data_loader import DEFAULT_DATA_DIR
from stardew_planner.llm_client import MOCK_RESPONSE
from stardew_planner.season_rules import check_plantings, last_safe_plant_day

ROOT = Path(__file__).resolve().parent.parent
RULES = load_bonus_rules()


def rule(data, product):
    return next(r for r in data.artisan_rules if r.product == product)


# ---------------------------------------------------------------------------
# 规则数据
# ---------------------------------------------------------------------------

def test_profession_and_fertilizer_values():
    p, f = RULES.professions, RULES.fertilizers
    assert p["tiller"].crop_price_multiplier == 1.1
    assert p["artisan"].artisan_price_multiplier == 1.4
    assert p["agriculturist"].growth_speed_percent == 10
    assert [f[k].growth_speed_percent for k in ("speed_gro", "deluxe_speed_gro", "hyper_speed_gro")] == [10, 25, 33]
    assert [f[k].price for k in ("speed_gro", "deluxe_speed_gro", "hyper_speed_gro")] == [100, 150, None]


def test_every_crop_has_full_speed_table():
    raw = json.loads((DEFAULT_DATA_DIR / "crops.json").read_text(encoding="utf-8"))["crops"]
    for c in raw:
        table = c["speed_grow_days"]
        assert list(table) == ["10", "20", "25", "33", "35", "43"], c["name"]
        days = list(table.values())
        assert all(1 <= d < c["grow_days"] for d in days), c["name"]
        # 加速越多越快（10% 与农业学家单独时相同，20% 与 25% 可能相同）
        for (k1, d1), (k2, d2) in zip(table.items(), list(table.items())[1:]):
            if int(k2) > int(k1):
                assert d2 <= d1, (c["name"], k1, k2)


# ---------------------------------------------------------------------------
# 价格：逐步向下取整
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value, mult, expected",
                         [(175, 1.1, 192), (35, 1.1, 38), (393, 1.4, 550), (400, 1.4, 560), (300, 1.4, 420)])
def test_floor_mul_is_exact(value, mult, expected):
    assert floor_mul(value, mult) == expected


def test_tiller_raises_crop_price(crops):
    tiller = resolve_bonuses(["tiller"])
    assert tiller.crop_price(crops["花椰菜"]) == 192   # wiki：175g → 192g
    assert tiller.crop_price(crops["草莓"]) == 132     # 水果同样适用
    assert NO_BONUSES.crop_price(crops["花椰菜"]) == 175


@pytest.mark.parametrize(
    "crop, product, base, with_artisan",
    [("花椰菜", "果汁", 393, 550), ("花椰菜", "腌菜", 400, 560), ("草莓", "果酒", 360, 504), ("啤酒花", "淡啤酒", 300, 420)],
)
def test_artisan_profession(data, crops, crop, product, base, with_artisan):
    r = rule(data, product)
    assert NO_BONUSES.artisan_price(r, crops[crop]) == base
    assert resolve_bonuses(["artisan"]).artisan_price(r, crops[crop]) == with_artisan


def test_tiller_does_not_affect_artisan_goods(data, crops):
    assert resolve_bonuses(["tiller"]).artisan_price(rule(data, "腌菜"), crops["花椰菜"]) == 400


# ---------------------------------------------------------------------------
# 生长天数：查 wiki 日历表
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "professions, fertilizer, days",
    [
        ([], None, 12),
        (["agriculturist"], None, 10),
        ([], "speed_gro", 10),
        (["agriculturist"], "speed_gro", 9),
        ([], "deluxe_speed_gro", 9),
        (["agriculturist"], "deluxe_speed_gro", 7),
        ([], "hyper_speed_gro", 8),
        (["agriculturist"], "hyper_speed_gro", 6),
    ],
)
def test_cauliflower_grow_days(crops, professions, fertilizer, days):
    assert resolve_bonuses(professions, fertilizer).grow_days(crops["花椰菜"]) == days


def test_yam_twenty_percent_is_not_simple_rounding(crops):
    # 10 天 × 20% 按百分比应该是 8 天，但游戏（和 wiki 日历）是 7 天：所以用查表
    assert resolve_bonuses(["agriculturist"], "speed_gro").grow_days(crops["山药"]) == 7


def test_speed_does_not_shorten_regrow(crops):
    # 草莓第 13 天种，顶级生长激素：生长 5 天，再生仍是 4 天 → 1 + ⌊(16 − 1 − 5) / 4⌋ = 3 次
    assert season_profit(crops["草莓"], 13, resolve_bonuses([], "hyper_speed_gro"))[0] == 3


# ---------------------------------------------------------------------------
# 收益与校验
# ---------------------------------------------------------------------------

def test_profit_with_tiller_agriculturist_and_speed_gro(crops):
    # 生长 4 天 → 6 次；6 × 88 × 1.25 − 6 × 50 − 肥料 100 = 260
    b = resolve_bonuses(["agriculturist"], "speed_gro")
    assert season_profit(crops["土豆"], 1, b) == (6, 260)


def test_fertilizer_cost_is_charged_once_per_tile(crops):
    with_fert = season_profit(crops["花椰菜"], 1, resolve_bonuses([], "speed_gro"))
    # 生长 10 天 → 2 次；2 × 175 − 2 × 80 − 100 = 90
    assert with_fert == (2, 90)
    assert resolve_bonuses([], "hyper_speed_gro").fertilizer_cost == 0  # 无法购买，按 0 计


def test_fertilizer_extends_last_plant_day(data, crops):
    deluxe = resolve_bonuses([], "deluxe_speed_gro")
    assert last_safe_plant_day(crops["花椰菜"]) == 16
    assert last_safe_plant_day(crops["花椰菜"], deluxe) == 19
    assert check_plantings({18: "种花椰菜"}, data, "spring") != []
    assert check_plantings({18: "种花椰菜"}, data, "spring", deluxe) == []
    late = check_plantings({20: "种花椰菜"}, data, "spring", deluxe)
    assert "生长 9 天（已计入 高级生长激素 25%）" in late[0].formula


def test_ranking_uses_bonuses(data):
    plain = {r.crop.name: r for r in rank_crops(data, "spring", top_n=10)}
    boosted = {r.crop.name: r for r in rank_crops(data, "spring", top_n=10,
                                                 bonuses=resolve_bonuses(["artisan"], "speed_gro"))}
    assert boosted["花椰菜"].sell_price == 192 and boosted["花椰菜"].grow_days == 10
    assert boosted["花椰菜"].artisan_unit_price == 560
    assert plain["花椰菜"].artisan_unit_price == 400


def test_data_context_describes_bonuses(data):
    ctx = build_data_context(data, "spring", bonuses=resolve_bonuses(["artisan"], "speed_gro"))
    assert ctx["bonuses"].startswith("职业：农耕人、工匠；肥料：生长激素")
    assert "| 花椰菜 | 80g | 192g | 10/- |" in ctx["crop_table"]
    assert build_data_context(data, "spring")["bonuses"].startswith("无")


# ---------------------------------------------------------------------------
# 名称解析与职业规则
# ---------------------------------------------------------------------------

def test_level_10_profession_adds_tiller():
    b = resolve_bonuses(["artisan"])
    assert [p.key for p in b.professions] == ["tiller", "artisan"]
    assert "农耕人" in b.notes[0]


@pytest.mark.parametrize("names", [["artisan", "agriculturist"], ["工匠,农业学家"]])
def test_artisan_and_agriculturist_conflict(names):
    with pytest.raises(ValueError, match="职业冲突"):
        resolve_bonuses(names)


@pytest.mark.parametrize("fertilizer", ["speed_gro", "Speed-Gro", "生长激素", "SPEED_GRO"])
def test_fertilizer_aliases(fertilizer):
    assert resolve_bonuses([], fertilizer).fertilizer.key == "speed_gro"


@pytest.mark.parametrize("names", [["农耕人", "tiller", " Tiller "], ["农耕人, tiller，Tiller"]])
def test_profession_aliases_and_dedupe(names):
    assert [p.key for p in resolve_bonuses(names).professions] == ["tiller"]


@pytest.mark.parametrize("kwargs", [{"professions": ["rancher"]}, {"fertilizer": "basic_fertilizer"}])
def test_unknown_names_rejected(kwargs):
    with pytest.raises(ValueError, match="未知"):
        resolve_bonuses(**kwargs)


# ---------------------------------------------------------------------------
# 集成：生成流程、命令行
# ---------------------------------------------------------------------------

class RecordingClient:
    def complete(self, system, messages):
        self.prompt = messages[0]["content"]
        return json.dumps(MOCK_RESPONSE, ensure_ascii=False)


def test_generate_strategy_applies_bonuses(data, indexed_store):
    client = RecordingClient()
    result = generate_strategy("种田", "速通", "spring", client=client, data=data, guide_store=indexed_store,
                               professions=["artisan"], fertilizer="speed_gro")
    assert [p.key for p in result.bonuses.professions] == ["tiller", "artisan"]
    assert "职业与肥料：职业：农耕人、工匠；肥料：生长激素" in client.prompt
    assert "**职业：农耕人、工匠；肥料：生长激素**" in result.markdown
    assert "| 花椰菜 | 80g | 192g | 10 天 |" in result.markdown


def run(*args, script="main.py"):
    return subprocess.run([sys.executable, script, *args], cwd=ROOT, capture_output=True, text=True, encoding="utf-8")


def test_cli_rejects_conflicting_professions():
    r = run("--provider", "mock", "--professions", "artisan,agriculturist")
    assert r.returncode == 2
    assert "职业冲突" in r.stderr


SV = str(ROOT / ".claude" / "skills" / "stardew-data-guard" / "scripts" / "sv.py")


def test_sv_profit_with_bonuses():
    r = run("profit", "土豆", "--professions", "agriculturist", "--fertilizer", "speed_gro", script=SV)
    assert r.returncode == 0
    assert "⌊80 × 农耕人 1.1⌋ = 88" in r.stdout
    assert "speed_grow_days[20%]" in r.stdout
    assert "= 260g" in r.stdout


def test_sv_artisan_with_profession():
    r = run("artisan", "花椰菜", "--professions", "artisan", script=SV)
    assert "⌊393 × 工匠 1.4⌋ = 550g" in r.stdout
    assert "⌊400 × 工匠 1.4⌋ = 560g" in r.stdout
