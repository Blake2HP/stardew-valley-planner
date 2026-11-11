"""畜牧：data/animals.json 的数值、--animals 解析、产出与成本、照料动物校验、跨季带动物。

数值来自 Stardew Valley Wiki（1.6）各动物页面与 Marnie's Ranch（核对日期 2026-11-11）。
"""

import copy
import json

import pytest

from stardew_planner import generate_plan, generate_strategy
from stardew_planner.analysis import build_data_context
from stardew_planner.farm_state import compute_finance, describe_finance
from stardew_planner.livestock import Herd, parse_animals, produce_days, simulate_livestock
from stardew_planner.llm_client import MOCK_RESPONSE, MOCK_SUMMER, MockClient
from stardew_planner.season_rules import check_animal_care


def animal(data, name):
    return next(a for a in data.animals if a.name == name)


@pytest.mark.parametrize(
    "name, building, price, nights, every, product, product_price",
    [
        ("鸡", "鸡舍", 800, 3, 1, "蛋", 50),
        ("鸭", "大鸡舍", 1200, 5, 2, "鸭蛋", 95),
        ("兔子", "高级鸡舍", 8000, 6, 4, "动物毛", 340),
        ("牛", "畜棚", 1500, 5, 1, "牛奶", 125),
        ("山羊", "大畜棚", 4000, 5, 2, "羊奶", 225),
        ("绵羊", "高级畜棚", 8000, 4, 3, "动物毛", 340),
        ("猪", "高级畜棚", 16000, 10, 1, "松露", 625),
    ],
)
def test_wiki_values(data, name, building, price, nights, every, product, product_price):
    a = animal(data, name)
    assert (a.building, a.buy_price, a.mature_nights, a.produce_every_days, a.product, a.product_price) == \
        (building, price, nights, every, product, product_price)
    assert data.hay_price == 50


# ---------------------------------------------------------------------------
# 解析
# ---------------------------------------------------------------------------

def test_parse_animals(data):
    herd = parse_animals("鸡:2，Cow：1, 鸡:1", data.animals)
    assert {a.name: n for a, n in herd.items()} == {"鸡": 3, "牛": 1}
    assert {a.name: n for a, n in parse_animals({"山羊": 2}, data.animals).items()} == {"山羊": 2}
    assert parse_animals(None, data.animals) == {} and parse_animals("", data.animals) == {}


@pytest.mark.parametrize("spec, message", [
    ("恐龙:1", "没有动物“恐龙”"), ("鸡:0", "正整数"), ("鸡两只", "名字:数量"), ({"鸡": -1}, "正整数"),
])
def test_parse_errors(data, spec, message):
    with pytest.raises(ValueError, match=message):
        parse_animals(spec, data.animals)


# ---------------------------------------------------------------------------
# 产出与成本
# ---------------------------------------------------------------------------

def test_new_chicken_matures_then_lays_daily(data):
    # 第 1 天买入，过 3 个夜晚成年（第 4 天），第一次产出按 1 + 3 + 1 = 第 5 天计
    days = produce_days(Herd(animal(data, "鸡"), 2), "spring")
    assert days == list(range(5, 29))


def test_new_goat_every_two_days(data):
    assert produce_days(Herd(animal(data, "山羊"), 1), "summer") == list(range(8, 29, 2))


def test_carried_animals_produce_from_day_one(data):
    assert produce_days(Herd(animal(data, "牛"), 1, new=False), "fall") == list(range(1, 29))


def test_pig_no_truffles_in_winter(data):
    assert produce_days(Herd(animal(data, "猪"), 1, new=False), "winter") == []
    assert len(produce_days(Herd(animal(data, "猪"), 1, new=False), "fall")) == 28


def test_purchase_and_income(data):
    r = simulate_livestock([Herd(animal(data, "鸡"), 2), Herd(animal(data, "牛"), 1)], "spring", 50)
    # 鸡：24 天 × 2 只 × 50 = 2400；牛：第 7-28 天 22 次 × 125 = 2750；买动物 2 × 800 + 1500 = 3100
    assert (r.income, r.purchase_cost, r.hay_cost, r.net) == (2400 + 2750, 3100, 0, 2050)
    assert r.cash_by_day[1] == -3100 and r.products == {"蛋": 48, "牛奶": 22}


def test_winter_hay(data):
    r = simulate_livestock([Herd(animal(data, "鸡"), 2, new=False)], "winter", 50)
    assert r.hay_cost == 2 * 28 * 50 and r.income == 2 * 28 * 50 and r.net == 0


def test_finance_includes_livestock(data):
    herds = (Herd(animal(data, "牛"), 1),)
    plain = compute_finance([], start_gold=5000, season="spring")
    cows = compute_finance([], start_gold=5000, season="spring", herds=herds, hay_price=50)
    assert cows.livestock_net == 2750 - 1500 and cows.end_gold == plain.end_gold + 1250
    assert "+ 畜牧 1250 =" in "\n".join(describe_finance(cows))
    assert cows.herds_out == (("牛", 1),)


def test_cash_shortfall_when_buying_animals(data):
    f = compute_finance([], start_gold=500, season="spring", herds=(Herd(animal(data, "牛"), 1),))
    assert f.warnings and "第 1 天买种子、肥料和动物后现金为 -1000g" in f.warnings[0]


def test_no_animals_falls_back(data):
    f = compute_finance([], start_gold=500, season="spring")
    assert f.livestock_net == 0 and f.herds_out == ()
    assert build_data_context(data, "spring")["livestock"] == "（玩家没有养动物）"


# ---------------------------------------------------------------------------
# 校验与生成
# ---------------------------------------------------------------------------

def test_check_animal_care():
    goals = {d: "钓鱼，照料动物" for d in range(1, 29)}
    assert check_animal_care(goals) == []
    goals[3], goals[4] = "下矿井", "去镇上"
    (w,) = check_animal_care(goals)
    assert "第 3、4 天（共 2 天）" in w
    assert check_animal_care({1: "给鸡收蛋"}, ["鸡"]) == []          # 写了动物名也算


class Scripted:
    def __init__(self, *payloads):
        self.payloads, self.prompts = list(payloads), []

    def complete(self, system, messages):
        self.prompts.append(messages[0]["content"])
        return json.dumps(self.payloads[min(len(self.prompts), len(self.payloads)) - 1], ensure_ascii=False)


def with_care(payload):
    p = copy.deepcopy(payload)
    p["daily_goals"] = {k: v + "，照料动物" for k, v in p["daily_goals"].items()}
    return p


def test_strategy_with_animals(data, indexed_store):
    client = Scripted(with_care(MOCK_RESPONSE))
    result = generate_strategy("养殖", "稳健", "spring", client=client, data=data, guide_store=indexed_store,
                               start_gold=10000, animals="鸡:2", revise_rounds=0)
    assert "### 畜牧（程序计算，data/animals.json）" in client.prompts[0] and "鸡 ×2（本季第 1 天从玛妮牧场买入" in client.prompts[0]
    assert result.finance.livestock_net == 2400 - 1600
    assert not any("照料动物" in p for p in result.problems)
    lazy = generate_strategy("养殖", "稳健", "spring", client=MockClient(), data=data, guide_store=indexed_store,
                             start_gold=10000, animals="鸡:2", revise_rounds=0)
    assert any("没有写“照料动物”" in p for p in lazy.problems)


def test_plan_buys_once_and_carries(data, indexed_store):
    client = Scripted(with_care(MOCK_RESPONSE), with_care(MOCK_SUMMER))
    plan = generate_plan("养殖", "稳健", "spring,summer", client=client, data=data, guide_store=indexed_store,
                         start_gold=10000, animals="牛:1", revise_rounds=0)
    spring, summer = plan.results
    assert spring.end_state.animals == (("牛", 1),)
    assert summer.finance.livestock_net == 28 * 125                    # 不再重复购买，第 1 天起每天产奶
    assert "已有动物（已成年" in client.prompts[1]


def test_unknown_animal_rejected(data):
    with pytest.raises(ValueError, match="没有动物"):
        generate_strategy("养殖", "稳健", "spring", client=MockClient(), data=data, animals="龙:1")


def test_web_accepts_animals(indexed_store):
    from fastapi.testclient import TestClient

    from web.app import Settings, create_app

    settings = Settings()
    settings.provider, settings.rate_limit = "mock", 0
    app = create_app(settings, client=MockClient(), guide_store=indexed_store, warm_up=False)
    body = {"playstyle": "种田", "pacing": "稳健", "seasons": ["spring"], "start_gold": 10000}
    with TestClient(app) as c:
        plain = c.post("/api/generate", json=body).json()["seasons"][0]["end_gold"]
        cows = c.post("/api/generate", json={**body, "animals": "牛:1"}).json()["seasons"][0]["end_gold"]
        bad = c.post("/api/generate", json={**body, "animals": "龙:1"})
    assert cows == plain + 2750 - 1500 and bad.status_code == 400
