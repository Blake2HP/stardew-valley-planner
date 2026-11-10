"""开局刘易斯镇长赠送的 15 颗防风草种子（wiki：Getting Started / The Farm）。"""

from stardew_planner import generate_strategy
from stardew_planner.farm_state import compute_finance, render_state_for_prompt, resolve_plantings
from stardew_planner.llm_client import MockClient


class Recorder(MockClient):
    def complete(self, system, messages):
        self.prompt = messages[0]["content"]
        return super().complete(system, messages)


def test_starting_seed_data(data):
    assert data.starting_seeds == {"Parsnip": 15}
    assert data.starting_seed_stock() == {"防风草": 15}


def plantings(data, *items):
    raw = [{"day": d, "crop": c, "tiles": t} for d, c, t in items]
    return resolve_plantings(raw, data, "spring", 1)[0]


def test_free_seeds_used_first(data):
    f = compute_finance(plantings(data, (1, "防风草", 20)), seed_stock={"防风草": 15}, start_gold=500)
    assert f.seed_cost == 5 * 20                                # 只买不够的 5 颗
    assert "(20 − 开局赠送 15) × 20" in f.lines[-1]


def test_free_seeds_run_out(data):
    f = compute_finance(plantings(data, (1, "防风草", 10), (6, "防风草", 10)), seed_stock={"防风草": 15})
    assert f.seed_cost == 5 * 20                                # 第二次只剩 5 颗赠送的种子
    assert "(10 − 开局赠送 10) × 20" in f.lines[-1] and "(10 − 开局赠送 5) × 20" in f.lines[-1]


def test_free_seeds_only_for_matching_crop(data):
    f = compute_finance(plantings(data, (1, "土豆", 5)), seed_stock={"防风草": 15})
    assert f.seed_cost == 5 * 50


def test_prompt_mentions_gift():
    text = render_state_for_prompt(None, 500, {"防风草": 15})
    assert "开局赠送：防风草种子 15 颗" in text
    assert "开局赠送" not in render_state_for_prompt(None, 500)


def test_new_game_gets_free_seeds(data, indexed_store):
    client = Recorder()
    result = generate_strategy("种田", "稳健", "spring", client=client, data=data, guide_store=indexed_store)
    assert "开局赠送：防风草种子 15 颗" in client.prompt
    assert any("开局赠送 15" in line for line in result.finance.lines)


def test_other_seasons_and_years_get_none(data, indexed_store):
    for season, year in (("summer", 1), ("spring", 2)):
        client = Recorder()
        result = generate_strategy("种田", "稳健", season, year, client=client, data=data,
                                   guide_store=indexed_store, start_gold=5000)
        assert "开局赠送" not in client.prompt
        assert not any("开局赠送" in line for line in result.finance.lines)


# ---------------------------------------------------------------------------
# 农场地图：草原农场没有开局种子
# ---------------------------------------------------------------------------

import pytest  # noqa: E402


def test_farm_types_from_wiki(data):
    assert list(data.farm_types) == ["standard", "riverland", "forest", "hilltop", "wilderness", "four_corners",
                                     "beach", "meadowlands"]
    assert data.farm_types["meadowlands"]["name_zh"] == "草原农场"


@pytest.mark.parametrize("name", ["meadowlands", "草原农场", "Meadowlands Farm", "MEADOWLANDS"])
def test_meadowlands_has_no_free_seeds(data, name):
    assert data.resolve_farm_type(name) == "meadowlands"
    assert data.starting_seed_stock(name) == {}


@pytest.mark.parametrize("name", [None, "standard", "标准农场", "Hill-top Farm", "four_corners", "海滩农场"])
def test_other_farms_get_parsnip_seeds(data, name):
    assert data.starting_seed_stock(name) == {"防风草": 15}


def test_unknown_farm_type(data):
    with pytest.raises(ValueError, match="未知农场类型"):
        data.resolve_farm_type("火星农场")


def test_generator_uses_farm_type(data, indexed_store):
    client = Recorder()
    result = generate_strategy("种田", "稳健", "spring", client=client, data=data, guide_store=indexed_store,
                               farm_type="草原农场")
    assert "开局赠送" not in client.prompt
    assert result.finance.end_gold == 2424                     # 防风草种子要自己买


def test_generator_rejects_unknown_farm_type(data, indexed_store):
    with pytest.raises(ValueError, match="未知农场类型"):
        generate_strategy("种田", "稳健", "spring", client=Recorder(), data=data, guide_store=indexed_store,
                          farm_type="火星农场")


def test_web_farm_type(indexed_store):
    from fastapi.testclient import TestClient

    from web.app import Settings, create_app

    settings = Settings()
    settings.provider, settings.rate_limit = "mock", 0
    app = create_app(settings, client=MockClient(), guide_store=indexed_store, warm_up=False)
    body = {"playstyle": "种田", "pacing": "稳健", "seasons": ["spring"]}
    with TestClient(app) as c:
        assert {"key": "meadowlands", "name": "草原农场"} in c.get("/api/options").json()["farm_types"]
        assert c.post("/api/generate", json={**body, "farm_type": "meadowlands"}).json()["seasons"][0]["end_gold"] == 2424
        assert c.post("/api/generate", json=body).json()["seasons"][0]["end_gold"] == 2724
        r = c.post("/api/generate", json={**body, "farm_type": "火星农场"})
    assert r.status_code == 400 and "未知农场类型" in r.json()["detail"]
