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
