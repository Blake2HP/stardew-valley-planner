"""data/calendar.json：日期已与 Stardew Valley Wiki（1.6）核对。"""

import pytest

from stardew_planner.analysis import build_data_context


def days(data, season, name):
    return [e.day for e in data.events_in_season(season) if e.name == name]


@pytest.mark.parametrize(
    "season, name, expected",
    [
        ("spring", "矿井开放", [5]), ("spring", "复活节", [13]), ("spring", "鲑莓采集季开始", [15]),
        ("spring", "沙漠节", [15, 16, 17]), ("spring", "花舞节", [24]),
        ("summer", "夏威夷宴会", [11]), ("summer", "鳟鱼大赛", [20, 21]), ("summer", "月光水母起舞", [28]),
        ("fall", "黑莓采集季开始", [8]), ("fall", "星露谷展览会", [16]), ("fall", "万灵节", [27]),
        ("winter", "冰雪节", [8]), ("winter", "鱿鱼节", [12, 13]), ("winter", "夜市", [15]),
        ("winter", "冬日星盛宴", [25]),
    ],
)
def test_event_dates_match_wiki(data, season, name, expected):
    assert days(data, season, name) == expected


def test_every_season_ends_with_season_change(data):
    for season in ("spring", "summer", "fall", "winter"):
        assert 28 in days(data, season, "换季日")


def test_new_16_events_keep_shops_open(data):
    for season, name in (("spring", "沙漠节"), ("summer", "鳟鱼大赛"), ("winter", "鱿鱼节")):
        events = [e for e in data.events_in_season(season) if e.name == name]
        assert all(e.type == "event" for e in events)
    assert {e.name_en for e in data.events_in_season("spring") if e.name == "沙漠节"} == {"Desert Festival"}


def test_summer_tip_lists_all_multi_season_crops(data):
    tip = next(e.tip for e in data.events_in_season("summer") if e.name == "换季日")
    multi = [c.name for c in data.crops if c.seasons == ("summer", "fall")]
    assert multi and all(name in tip for name in multi)


def test_events_reach_prompt(data):
    assert "第 20 天【鳟鱼大赛】" in build_data_context(data, "summer")["calendar_events"]
