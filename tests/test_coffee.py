"""咖啡豆与“多个原料做一件”的加工规则。期望值来自 https://stardewvalleywiki.com/Coffee_Bean 与 /Coffee（核对日期 2026-11-10）。"""

import pytest

from stardew_planner.analysis import build_data_context, sale_plan
from stardew_planner.bonuses import NO_BONUSES, resolve_bonuses
from stardew_planner.processing import simulate_processing
from stardew_planner.season_rules import check_gifts


def test_coffee_bean_values(crops):
    c = crops["咖啡豆"]
    assert (c.seasons, c.sell_price, c.grow_days, c.regrow_days, c.category) == (("spring", "summer"), 15, 10, 2, "seed")
    assert c.harvest_yield == pytest.approx(4 + 0.02 / 0.98, abs=1e-4)
    assert [c.speed_grow_days[k] for k in ("10", "20", "25", "33", "35", "43")] == [8, 7, 7, 6, 6, 5]


def test_only_keg_makes_coffee(data, crops):
    (rule,) = data.artisan_rules_for(crops["咖啡豆"])           # 种子类不能放进罐头瓶
    assert (rule.machine, rule.product, rule.fixed_price, rule.input_count) == ("小桶", "咖啡", 150, 5)
    assert NO_BONUSES.artisan_unit_price(rule, crops["咖啡豆"]) == 30   # 150 ÷ 5


def test_tiller_does_not_apply(crops):
    assert resolve_bonuses(["tiller"]).crop_price(crops["咖啡豆"]) == 15


def test_sale_plan_per_bean(data, crops):
    plan = sale_plan(crops["咖啡豆"], data.artisan_rules_for(crops["咖啡豆"]))
    assert plan.options[0].gain == 15 and "咖啡" in plan.summary()


def test_processing_uses_five_beans_per_coffee(data, crops):
    # 10 格第 11 天收获 ≈ 40.2 颗；小桶 1 个，每天最多做 1 批（5 颗 → 1 杯），第 11-28 天共 18 批 = 90 颗，全部留得下
    beans = 10 * crops["咖啡豆"].harvest_yield
    r = simulate_processing([(11, crops["咖啡豆"], 10)], data.artisan_rules_for, {"小桶": 1}, NO_BONUSES)
    assert r.held_value == pytest.approx(beans * 15)
    assert r.completed["咖啡"] == pytest.approx(beans / 5)
    assert r.completed_value == pytest.approx(beans / 5 * 150)
    assert r.cash_by_day[12] == pytest.approx(150)                # 第 11 天装 5 颗，第 12 天出第一杯


def test_keg_prefers_better_rate(data, crops):
    # 咖啡每台每天多赚 (150 − 5 × 15) / 1 = 75g；同一天的草莓果酒 (360 − 120) / 7 ≈ 34g，所以先做咖啡：
    # 8.04 杯咖啡占第 11-19 天，剩下的机器时间才留给草莓（机器按流体近似：第 19 天起做果酒，第 26-27 天出 1 瓶）
    r = simulate_processing([(11, crops["咖啡豆"], 10), (11, crops["草莓"], 10)], data.artisan_rules_for, {"小桶": 1},
                            NO_BONUSES)
    beans = 10 * crops["咖啡豆"].harvest_yield
    assert r.completed["咖啡"] == pytest.approx(beans / 5)
    assert r.cash_by_day[12] == pytest.approx(150)                 # 第一天先做咖啡
    assert r.completed["果酒"] == pytest.approx(1.0) and r.cash_by_day[26] + r.cash_by_day[27] == pytest.approx(360)


def test_prompt_and_gift(data):
    assert "5 个原料做 1 件，折合每个原料 30g" in build_data_context(data, "spring")["artisan_rules"]
    (w,) = [x for x in check_gifts({3: "送海莉咖啡豆", 14: "送海莉黄水仙", 20: "送谢恩披萨", 27: "送艾米丽紫水晶"},
                                   data, "spring")]
    assert "海莉不喜欢咖啡豆" in w.message                        # All Seeds 是通用不喜欢
