"""收益表里的跨季作物：按完整存活期计算、按平均每季净利排序、始终列出。"""

from stardew_planner.analysis import build_data_context, rank_crops, season_profit
from stardew_planner.bonuses import resolve_bonuses


def ranked(data, season, **kw):
    return {r.crop.name: r for r in rank_crops(data, season, year=1, **kw)}


def test_corn_counts_harvests_through_fall(crops):
    # 夏季第 1 天种：可用 28 + 28 = 56 天；1 + ⌊(56 − 1 − 14) / 4⌋ = 11 次；11 × 50 − 150 = 400
    assert season_profit(crops["玉米"], season="summer") == (11, 400)
    assert season_profit(crops["玉米"]) == (4, 50)                     # 不传季节仍只算当季
    assert season_profit(crops["玉米"], season="fall") == (4, 50)      # 秋季种活不到冬季


def test_non_regrowing_multi_season_crops(crops):
    # 小麦：⌊55 / 4⌋ = 13 次；13 × 25 − 13 × 10 = 195
    assert season_profit(crops["小麦"], season="summer") == (13, 195)
    # 向日葵：⌊55 / 8⌋ = 6 次；6 × 80 − 只买第一份种子 200 = 280
    assert season_profit(crops["向日葵"], season="summer") == (6, 280)


def test_single_season_crops_unchanged(crops):
    assert season_profit(crops["蓝莓"], season="summer") == season_profit(crops["蓝莓"])


def test_fertilizer_charged_once_over_full_life(crops):
    b = resolve_bonuses([], "speed_gro")
    harvests, profit = season_profit(crops["玉米"], bonuses=b, season="summer")
    assert harvests == 1 + (56 - 1 - b.grow_days(crops["玉米"])) // 4
    assert profit == harvests * 50 - 150 - 100


def test_multi_season_crops_always_listed(data):
    summer = ranked(data, "summer")
    assert {"玉米", "向日葵", "小麦"} <= set(summer)
    corn = summer["玉米"]
    assert (corn.harvests, corn.profit_per_tile, corn.seasons_alive) == (11, 400, 2)
    assert corn.available_days == 56 and corn.profit_per_season == 200


def test_sorted_by_average_per_season(data):
    # top_n 足够大时不触发“补列”，整张表都应按平均每季净利降序
    rows = rank_crops(data, "summer", top_n=50, year=1)
    values = [r.profit_per_season for r in rows]
    assert values == sorted(values, reverse=True)


def test_total_does_not_outrank_better_single_season_crop(data):
    # 玉米总额 400g 高于番茄，但平均每季只有 200g，不应排在番茄前面
    rows = [r.crop.name for r in rank_crops(data, "summer", top_n=50, year=1)]
    assert rows.index("番茄") < rows.index("玉米")


def test_fall_lists_nothing_extra(data):
    assert all(r.seasons_alive == 1 for r in rank_crops(data, "fall", year=1))
    assert len(rank_crops(data, "fall", year=1)) == 6


def test_prompt_shows_full_life(data):
    table = build_data_context(data, "summer", year=1)["crop_table"]
    row = next(line for line in table.splitlines() if line.startswith("| 玉米 |"))
    assert "11（跨 2 季）" in row and "400g（跨 2 季，平均每季 200g）" in row
    assert "- 向日葵：每次收获平均掉落 1 颗种子" in table


def test_plan_profit_uses_full_life(data):
    corn = ranked(data, "summer")["玉米"]
    assert corn.harvests == 11
    assert corn.profit_with_plan == int(11 * corn.sale_plan.expected_best - 150)
