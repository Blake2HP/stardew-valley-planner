"""规划参数 PlanOptions：构造、检查、跨季清理，以及命令行 / 网页 / 存档导入都按同一份字段名接线。"""

import pytest

from stardew_planner import generate_plan, generate_strategy
from stardew_planner.llm_client import MockClient
from stardew_planner.options import FIRST_SEASON_ONLY, PlanOptions


def test_build_overrides_and_rejects_unknown():
    base = PlanOptions(kegs=3)
    opts = PlanOptions.build(base, jars=2, professions=["artisan"])
    assert (opts.kegs, opts.jars, opts.professions) == (3, 2, ("artisan",))
    assert PlanOptions.build(base) is base
    with pytest.raises(TypeError, match="未知的规划参数：kegz"):
        PlanOptions.build(kegz=1)


@pytest.mark.parametrize("patch, message", [
    ({"start_day": 0}, "起始日"), ({"start_day": True}, "起始日"), ({"start_gold": -1}, "期初资金"),
    ({"processing_share": 1.5}, "加工比例"), ({"revise_rounds": -1}, "自动修正"), ({"bee_houses": -2}, "机器数量"),
])
def test_validate(patch, message):
    with pytest.raises(ValueError, match=message):
        PlanOptions(**patch).validate()


def test_next_season_clears_first_season_only():
    opts = PlanOptions(start_gold=900, start_day=12, animals="鸡:1", owned_animals={"牛": 1},
                       in_ground=[{"crop": "蓝莓", "tiles": 1, "ready_day": 14}], kegs=4, achievement_progress="防风草=1")
    nxt = opts.next_season("进度")
    assert all(getattr(nxt, n) == getattr(PlanOptions(), n) for n in FIRST_SEASON_ONLY)
    assert nxt.kegs == 4 and nxt.achievement_progress == "进度"
    assert opts.next_season().achievement_progress == "防风草=1"


def test_options_object_and_keywords_are_equivalent(data, indexed_store):
    common = dict(client=MockClient(), data=data, guide_store=indexed_store)
    a = generate_strategy("种田", "稳健", "fall", options=PlanOptions(kegs=5, start_day=10), **common)
    b = generate_strategy("种田", "稳健", "fall", kegs=5, start_day=10, **common)
    c = generate_strategy("种田", "稳健", "fall", options=PlanOptions(kegs=1), kegs=5, start_day=10, **common)
    assert a.markdown == b.markdown == c.markdown and a.options == b.options


def test_plan_passes_options_and_clears_first_season(data, indexed_store):
    plan = generate_plan("种田", "稳健", "spring,summer", client=MockClient(), data=data, guide_store=indexed_store,
                         options=PlanOptions(start_day=15, start_gold=1000, kegs=2), animals="鸡:1")
    spring, summer = plan.results
    assert (spring.options.start_day, spring.options.animals, spring.options.kegs) == (15, "鸡:1", 2)
    assert (summer.options.start_day, summer.options.animals, summer.options.kegs) == (1, None, 2)


def test_every_field_is_wired_to_cli_and_web():
    """新加的 PlanOptions 字段要同时有同名的命令行参数和网页请求字段（in_ground 只从存档导入）。"""
    import main
    from web.app import SERVER_SIDE_OPTIONS, GenerateRequest

    dests = {a.dest for a in main.build_parser()._actions} | set(main.build_parser().parse_args([]).__dict__)
    assert set(PlanOptions.names()) <= dests
    assert set(PlanOptions.names()) - SERVER_SIDE_OPTIONS <= set(GenerateRequest.model_fields)


def test_save_kwargs_are_plan_options():
    from pathlib import Path

    from stardew_planner.save_parser import parse_save
    kw = parse_save(Path(__file__).parent / "fixtures" / "Farmer_123456789").generator_kwargs()
    assert set(kw) - {"season", "year"} <= set(PlanOptions.names())
