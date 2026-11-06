"""作物品质（银星 / 金星 / 铱星）、品质肥料（普通 / 高级 / 顶级），以及“高星直接卖、低星加工”的出售策略。

期望值来自 Stardew Valley Wiki（1.6）：Farming 页面的品质公式与概率表、各作物信息框的四档售价。
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from stardew_planner import generate_strategy
from stardew_planner.analysis import build_data_context, rank_crops, sale_plan, season_profit
from stardew_planner.bonuses import NO_BONUSES, QUALITIES, gold_chance, load_bonus_rules, quality_odds, resolve_bonuses
from stardew_planner.data_loader import ArtisanRule
from stardew_planner.farm_state import compute_finance, resolve_plantings
from stardew_planner.llm_client import MOCK_RESPONSE

ROOT = Path(__file__).resolve().parent.parent
SV = str(ROOT / ".claude" / "skills" / "stardew-data-guard" / "scripts" / "sv.py")

DELUXE_L10 = {"normal": 0.0, "silver": 0.11305, "gold": 0.48195, "iridium": 0.405}


def odds_list(o):
    return [o[q] for q in QUALITIES]


# ---------------------------------------------------------------------------
# 数据与概率公式
# ---------------------------------------------------------------------------

def test_quality_data():
    rules = load_bonus_rules()
    assert rules.quality_multipliers == {"normal": 1.0, "silver": 1.25, "gold": 1.5, "iridium": 2.0}
    f = rules.fertilizers
    assert [f[k].quality_level for k in ("basic_fertilizer", "quality_fertilizer", "deluxe_fertilizer")] == [1, 2, 3]
    assert [f[k].price for k in ("basic_fertilizer", "quality_fertilizer", "deluxe_fertilizer")] == [100, 150, None]
    assert all(f[k].growth_speed_percent == 0 for k in ("basic_fertilizer", "quality_fertilizer", "deluxe_fertilizer"))
    assert all(f[k].quality_level == 0 for k in ("speed_gro", "deluxe_speed_gro", "hyper_speed_gro"))


def test_gold_chance_formula():
    # 0.2 × (10 / 10) + 0.2 × 3 × (12 / 12) + 0.01 = 0.81
    assert gold_chance(10, 3) == pytest.approx(0.81)
    assert gold_chance(0, 0) == pytest.approx(0.01)


@pytest.mark.parametrize(
    "fert, level, wiki",
    [  # wiki 表格（百分比，已四舍五入）：无肥料 / 普通 / 高级为 普通-银-金；顶级为 银-金-铱
        (0, 0, (97, 2, 1)), (0, 10, (46, 33, 21)), (0, 14, (30, 41, 29)),
        (1, 5, (42, 35, 23)), (1, 10, (15, 44, 41)), (1, 14, (11, 33, 56)),
        (2, 0, (78, 14, 8)), (2, 6, (15, 45, 40)), (2, 10, (10, 29, 61)),
        (3, 0, (84, 10, 6)), (3, 10, (11, 48, 41)), (3, 13, (0, 49, 51)), (3, 14, (0, 46, 55)),
    ],
)
def test_quality_odds_match_wiki_tables(fert, level, wiki):
    o = quality_odds(level, fert)
    assert sum(o.values()) == pytest.approx(1)
    got = (o["silver"], o["gold"], o["iridium"]) if fert == 3 else (o["normal"], o["silver"], o["gold"])
    for g, w in zip(got, wiki):
        assert abs(g * 100 - w) < 1, (got, wiki)


def test_deluxe_fertilizer_has_no_normal_quality_and_caps_at_one():
    assert quality_odds(0, 3)["normal"] == 0
    o = quality_odds(14, 3)          # g = 1.09 > 1：金星掷骰必中，银星为 0
    assert o["silver"] == pytest.approx(0) and o["iridium"] == pytest.approx(0.545)


def test_only_deluxe_fertilizer_gives_iridium():
    for fert in (0, 1, 2):
        assert quality_odds(14, fert)["iridium"] == 0


# ---------------------------------------------------------------------------
# 品质售价：⌊基础 × 品质倍率⌋，农耕人再 ⌊× 1.1⌋
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "crop, base, tiller",
    [  # wiki 信息框：普通 / 银星 / 金星 / 铱星
        ("防风草", [35, 43, 52, 70], [38, 47, 57, 77]),
        ("杨桃", [750, 937, 1125, 1500], [825, 1030, 1237, 1650]),
        ("大黄", [220, 275, 330, 440], [242, 302, 363, 484]),
        ("小麦", [25, 31, 37, 50], [27, 34, 40, 55]),
    ],
)
def test_quality_prices_match_wiki(crops, crop, base, tiller):
    t = resolve_bonuses(["tiller"])
    assert [NO_BONUSES.crop_price(crops[crop], q) for q in QUALITIES] == base
    assert [t.crop_price(crops[crop], q) for q in QUALITIES] == tiller


# ---------------------------------------------------------------------------
# 顶级肥料的收益期望值
# ---------------------------------------------------------------------------

def test_deluxe_fertilizer_expected_profit_cauliflower(crops):
    b = resolve_bonuses([], "deluxe_fertilizer", farming_level=10)
    assert odds_list(b.quality_odds()) == pytest.approx(odds_list(DELUXE_L10))
    # 售价：银星 ⌊175 × 1.25⌋ = 218，金星 ⌊175 × 1.5⌋ = 262，铱星 175 × 2 = 350
    expected = 0.11305 * 218 + 0.48195 * 262 + 0.405 * 350   # = 292.6658
    assert b.expected_crop_value(crops["花椰菜"]) == pytest.approx(expected)
    # ⌊27 / 12⌋ = 2 次；2 × 292.6658 − 2 × 80 − 肥料 0（顶级肥料只能自制） = 425.33
    assert season_profit(crops["花椰菜"], 1, b) == (2, int(2 * expected - 160))
    assert season_profit(crops["花椰菜"], 1, b) == (2, 425)
    assert season_profit(crops["花椰菜"]) == (2, 190)   # 不计品质时不变


def test_deluxe_fertilizer_with_tiller_and_extra_harvest(crops):
    # 草莓：农耕人售价 132 / 165 / 198 / 264；单次产量 1.0204，额外的 0.0204 个为普通品质
    b = resolve_bonuses(["tiller"], "deluxe_fertilizer", farming_level=10)
    expected = 0.11305 * 165 + 0.48195 * 198 + 0.405 * 264 + 0.0204 * 132
    assert b.expected_crop_value(crops["草莓"]) == pytest.approx(expected)
    # 第 13 天种：收获 2 次（第 21、25 天），种子 1 份
    assert season_profit(crops["草莓"], 13, b) == (2, int(2 * expected - 100))


def test_extra_crops_are_regular_quality(crops):
    # 蓝莓每次 3.0204 个，只有第一个吃到肥料的品质
    b = resolve_bonuses([], "deluxe_fertilizer", farming_level=10)
    first = 0.11305 * 62 + 0.48195 * 75 + 0.405 * 100
    assert b.expected_crop_value(crops["蓝莓"]) == pytest.approx(first + 2.0204 * 50)


def test_quality_fertilizer_cost_and_growth(crops):
    b = resolve_bonuses([], "quality_fertilizer", farming_level=0)
    assert b.grow_days(crops["花椰菜"]) == 12        # 品质肥料不加速
    assert b.fertilizer_cost == 150
    value = 0.78 * 175 + 0.14 * 218 + 0.08 * 262       # 约等于 wiki 表格 0 级（四舍五入后的概率）
    assert b.expected_crop_value(crops["花椰菜"]) == pytest.approx(value, rel=0.01)


def test_quality_switches(crops):
    assert not NO_BONUSES.quality_active
    assert not resolve_bonuses(["tiller"], "speed_gro").quality_active      # 旧行为不变
    assert resolve_bonuses([], "basic_fertilizer").effective_farming_level == 0
    assert resolve_bonuses(["artisan"], "basic_fertilizer").effective_farming_level == 10  # 由职业推出
    assert resolve_bonuses([], farming_level=3).quality_active


@pytest.mark.parametrize("level, message", [(15, "0-14"), (-1, "0-14")])
def test_farming_level_range(level, message):
    with pytest.raises(ValueError, match=message):
        resolve_bonuses([], farming_level=level)


def test_profession_needs_farming_level():
    with pytest.raises(ValueError, match="耕种等级 6 还不能选择工匠（10 级）"):
        resolve_bonuses(["artisan"], farming_level=6)
    with pytest.raises(ValueError, match="农耕人（5 级）"):
        resolve_bonuses(["tiller"], farming_level=4)


def test_fertilizer_aliases():
    assert resolve_bonuses([], "顶级肥料").fertilizer.key == "deluxe_fertilizer"
    assert resolve_bonuses([], "Basic Fertilizer").fertilizer.key == "basic_fertilizer"


# ---------------------------------------------------------------------------
# 出售策略：高星直接卖，低星加工
# ---------------------------------------------------------------------------

def plan_for(data, crops, name, bonuses, share=1.0, rules=None):
    return sale_plan(crops[name], rules if rules is not None else data.artisan_rules_for(crops[name]), bonuses, share)


def test_processing_gain_shrinks_with_quality(data, crops):
    p = plan_for(data, crops, "花椰菜", resolve_bonuses([], "deluxe_fertilizer", farming_level=10))
    gains = {o.quality: o.gain for o in p.options}
    # 腌菜 400g（加工品不继承品质）：银星 +182、金星 +138、铱星 +50
    assert gains == {"normal": 225, "silver": 182, "gold": 138, "iridium": 50}


def test_unlimited_machines_process_everything(data, crops):
    p = plan_for(data, crops, "花椰菜", resolve_bonuses([], "deluxe_fertilizer", farming_level=10))
    assert all(o.processed_share == 1 for o in p.options if o.units)
    assert p.expected_best == pytest.approx(400)
    assert p.summary().startswith("全部加工成腌菜")


def test_limited_machines_sell_high_stars(data, crops):
    # 机器只够加工一半：银星（0.113）全部加工，金星加工 0.387 个，铱星全部直接卖
    p = plan_for(data, crops, "花椰菜", resolve_bonuses([], "deluxe_fertilizer", farming_level=10), share=0.5)
    shares = {o.quality: o.processed_share for o in p.options}
    assert shares["silver"] == 1 and shares["iridium"] == 0
    assert shares["gold"] == pytest.approx((0.5 - 0.11305) / 0.48195)
    expected = 0.11305 * 400 + (0.5 - 0.11305) * 400 + (0.48195 - (0.5 - 0.11305)) * 262 + 0.405 * 350
    assert p.expected_best == pytest.approx(expected)
    assert p.summary().startswith("金星（部分）、铱星直接卖；银星、金星（部分）加工成腌菜")


def test_no_machines_sell_everything(data, crops):
    b = resolve_bonuses([], "deluxe_fertilizer", farming_level=10)
    p = plan_for(data, crops, "花椰菜", b, share=0)
    assert p.expected_best == pytest.approx(b.expected_crop_value(crops["花椰菜"]))
    assert p.summary().startswith("全部直接卖")


def test_high_star_sold_when_direct_beats_processing(crops):
    # 假设某加工品固定 300g：铱星 350g 直接卖更划算，即使机器充足也不加工
    rule = ArtisanRule("测试机", "测试品", "vegetable", 0, 0, 300, 1600, 1.0)
    p = sale_plan(crops["花椰菜"], [rule], resolve_bonuses([], "deluxe_fertilizer", farming_level=10))
    shares = {o.quality: o.processed_share for o in p.options if o.units}
    assert shares == {"silver": 1, "gold": 1, "iridium": 0}
    assert p.summary().startswith("铱星直接卖；银星、金星加工成测试品")


def test_extra_crops_processed_first(data, crops):
    # 蓝莓的额外产出是普通品质，加工多赚最多，机器不够时最先加工
    p = plan_for(data, crops, "蓝莓", resolve_bonuses([], "deluxe_fertilizer", farming_level=10), share=0.5)
    normal = next(o for o in p.options if o.quality == "normal")
    assert normal.units == pytest.approx(2.0204) and normal.processed_share == pytest.approx(0.5 * 3.0204 / 2.0204)


def test_flowers_have_no_plan(data, crops):
    assert plan_for(data, crops, "郁金香", NO_BONUSES).summary() == "直接卖（无加工规则）"


def test_invalid_processing_share(data, crops):
    with pytest.raises(ValueError, match="0-1"):
        plan_for(data, crops, "花椰菜", NO_BONUSES, share=1.5)


# ---------------------------------------------------------------------------
# 排行、Prompt、资金、生成流程、命令行
# ---------------------------------------------------------------------------

def test_ranking_and_prompt_include_quality(data):
    b = resolve_bonuses([], "deluxe_fertilizer", farming_level=10)
    by_name = {r.crop.name: r for r in rank_crops(data, "spring", top_n=20, bonuses=b, year=1)}
    assert by_name["花椰菜"].profit_per_tile == 425
    assert by_name["花椰菜"].profit_with_plan == 2 * 400 - 160      # 全部加工成腌菜
    ctx = build_data_context(data, "spring", bonuses=b, year=1, processing_share=0.5)
    assert "耕种等级 10" in ctx["bonuses"] and "加工品不继承品质" in ctx["bonuses"]
    assert "铱星 40.5%" in ctx["bonuses"]
    assert "按品质出售策略" in ctx["crop_table"] and "铱星直接卖" in ctx["crop_table"]


def test_finance_uses_quality_expectation(data):
    b = resolve_bonuses([], "deluxe_fertilizer", farming_level=10)
    plantings, _ = resolve_plantings([{"day": 1, "crop": "花椰菜", "tiles": 10}], data, "spring", 1, b)
    f = compute_finance(plantings, bonuses=b)
    assert f.crop_revenue == int(10 * (0.11305 * 218 + 0.48195 * 262 + 0.405 * 350))
    assert "品质期望收入 292.67g" in f.lines[0]


class Recorder:
    def complete(self, system, messages):
        self.prompt = messages[0]["content"]
        return json.dumps(MOCK_RESPONSE, ensure_ascii=False)


def test_generate_strategy_with_quality(data, indexed_store):
    client = Recorder()
    result = generate_strategy("种田", "速通", "spring", client=client, data=data, guide_store=indexed_store,
                               fertilizer="deluxe_fertilizer", farming_level=10, processing_share=0.5)
    assert "耕种等级 10" in client.prompt
    assert "按品质出售策略" in result.markdown and "净利按品质分布" in result.markdown
    assert "机器只够加工 50%" in result.markdown


def run(*args, script="main.py"):
    return subprocess.run([sys.executable, script, *args], cwd=ROOT, capture_output=True, text=True, encoding="utf-8")


@pytest.mark.parametrize("args, message", [(["--farming-level", "15"], "0-14"),
                                           (["--processing-share", "2"], "加工比例")])
def test_cli_rejects_bad_quality_args(args, message):
    r = run("--provider", "mock", *args)
    assert r.returncode == 2 and message in r.stderr


def test_sv_quality_command():
    r = run("quality", "花椰菜", "--fertilizer", "deluxe_fertilizer", "--farming-level", "10",
            "--processing-share", "0.5", script=SV)
    assert r.returncode == 0, r.stdout
    assert "g = 0.2 × (10 / 10) + 0.2 × 肥料等级 3 × ((10 + 2) / 12) + 0.01 = 0.8100" in r.stdout
    assert "铱星 350g" in r.stdout and "加工多赚 +50g" in r.stdout
    assert "按策略 366.64g" in r.stdout


def test_sv_profit_with_deluxe_fertilizer():
    r = run("profit", "花椰菜", "--fertilizer", "deluxe_fertilizer", "--farming-level", "10", script=SV)
    assert r.returncode == 0
    assert "品质期望收入 = 0.1130 × 218 + 0.4819 × 262 + 0.4050 × 350 = 292.67g" in r.stdout
    assert "= 425.332g" in r.stdout
