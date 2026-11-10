"""蜂房模拟：每 4 天一罐，取蜜当天室外开着的最值钱的花，冬季和温室不算。价格见 https://stardewvalleywiki.com/Honey 。"""

import pytest

from stardew_planner import generate_strategy
from stardew_planner.bonuses import NO_BONUSES, resolve_bonuses
from stardew_planner.farm_state import compute_finance, describe_finance, extract_farm_state, resolve_plantings
from stardew_planner.honey import simulate_honey
from stardew_planner.llm_client import MockClient


def finance(data, season, items, bees, bonuses=NO_BONUSES, carry=(), greenhouse=False):
    raw = [{"day": d, "crop": c, "tiles": t, **({"place": "greenhouse"} if greenhouse else {})} for d, c, t in items]
    plantings, _ = resolve_plantings(raw, data, season, 1, bonuses, greenhouse=greenhouse)
    return compute_finance(plantings, carry, start_gold=5000, bonuses=bonuses, machines={"蜂房": bees},
                           season=season, honey=data.honey), plantings


def test_wild_honey_without_flowers(data):
    f, _ = finance(data, "spring", [], 2)
    assert f.honey_income == 2 * 7 * 100                      # 第 4、8、…、28 天，共 7 罐 × 2 个蜂房
    assert "野蜂蜜 14 罐" in " ".join(f.lines)


def test_flower_honey_after_bloom(data):
    # 郁金香第 1 天种、生长 6 天，第 7 天开花：第 4 天野蜂蜜，第 8-28 天 6 罐郁金香蜂蜜（160g）
    f, _ = finance(data, "spring", [(1, "郁金香", 5)], 1)
    assert f.honey_income == 100 + 6 * 160


def test_best_flower_wins(data):
    # 蓝爵第 1 天种、7 天开花（第 8 天）：蓝爵蜂蜜 200g 比郁金香 160g 贵
    f, _ = finance(data, "spring", [(1, "郁金香", 5), (1, "蓝爵", 5)], 1)
    assert f.honey_income == 100 + 6 * 200


def test_artisan_bonus(data):
    b = resolve_bonuses(["artisan"])
    f, _ = finance(data, "spring", [(1, "郁金香", 5)], 1, b)
    assert f.honey_income == 140 + 6 * 224                     # wiki 表：郁金香蜂蜜 + 工匠 = 224g


def test_winter_and_greenhouse(data):
    assert finance(data, "winter", [], 3)[0].honey_income == 0
    f, _ = finance(data, "summer", [(1, "夏季亮片", 5)], 1, greenhouse=True)
    assert f.honey_income == 7 * 100                           # 温室里的花够不着室外的蜂房


def test_carried_sunflower_blooms_next_season(data):
    # 夏季第 25 天种向日葵（8 天）→ 秋季第 5 天开花：秋季第 8-28 天 6 罐向日葵蜂蜜（260g）
    f, plantings = finance(data, "summer", [(25, "向日葵", 5)], 0)
    state = extract_farm_state("summer", 1, plantings, f)
    fall, _ = finance(data, "fall", [], 1, carry=state.carry)
    assert fall.honey_income == 100 + 6 * 260


def test_income_counts_toward_gold(data):
    plain, _ = finance(data, "spring", [(1, "郁金香", 5)], 0)
    bees, _ = finance(data, "spring", [(1, "郁金香", 5)], 2)
    assert bees.end_gold == plain.end_gold + 2 * (100 + 6 * 160)
    assert bees.crop_net == plain.crop_net + 2120
    assert "+ 蜂蜜 2120 =" in "\n".join(describe_finance(bees))


def test_no_bee_houses(data):
    assert simulate_honey([], "spring", 0, data.honey, NO_BONUSES).income == 0


def test_generator_prompt_and_validation(data, indexed_store):
    class Recorder(MockClient):
        def complete(self, system, messages):
            self.prompt = messages[0]["content"]
            return super().complete(system, messages)

    client = Recorder()
    result = generate_strategy("养蜂", "稳健", "spring", client=client, data=data, guide_store=indexed_store,
                               bee_houses=3)
    assert "蜂房 3 个" in client.prompt and "先取蜜再收花" in client.prompt
    assert result.finance.honey_income == 3 * 7 * 100           # mock 春季日程没有种花
    with pytest.raises(ValueError, match="蜂房"):
        generate_strategy("养蜂", "稳健", "spring", client=MockClient(), data=data, bee_houses=-1)


def test_web_accepts_bee_houses(indexed_store):
    from fastapi.testclient import TestClient

    from web.app import Settings, create_app

    settings = Settings()
    settings.provider, settings.rate_limit = "mock", 0
    app = create_app(settings, client=MockClient(), guide_store=indexed_store, warm_up=False)
    body = {"playstyle": "种田", "pacing": "稳健", "seasons": ["spring"]}
    with TestClient(app) as c:
        plain = c.post("/api/generate", json=body).json()["seasons"][0]["end_gold"]
        bees = c.post("/api/generate", json={**body, "bee_houses": 2}).json()["seasons"][0]["end_gold"]
        bad = c.post("/api/generate", json={**body, "bee_houses": -1})
    assert bees == plain + 2 * 7 * 100 and bad.status_code == 422
