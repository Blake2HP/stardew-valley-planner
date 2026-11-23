"""导入星露谷 1.6 存档（save_parser.py）：字段提取、容错、安全、命令行覆盖、网页接口。

tests/fixtures/Farmer_123456789 是手工精简的 1.6 主存档：第 2 年夏季第 12 天、河边农场、
耕种 10 级（农耕人、工匠）、温室已修好、农场上 2 个小桶 1 个罐头瓶 2 个蜂房（棚屋里另有 1 个小桶、1 个蜂房），
牛 1、鸡 2、鸭 1、虚空鸡 1，出货记录里有一个模组物品。
"""

import argparse
from pathlib import Path

import pytest

from stardew_planner import generate_strategy
from stardew_planner.achievements import Progress, load_achievement_rules, parse_progress
from stardew_planner.llm_client import MockClient
from stardew_planner.save_parser import SaveParseError, load_save_ids, parse_save

FIXTURE = Path(__file__).parent / "fixtures" / "Farmer_123456789"


@pytest.fixture(scope="module")
def save(data):
    return parse_save(FIXTURE, data)


# ---------------------------------------------------------------------------
# 字段提取
# ---------------------------------------------------------------------------

def test_basic_progress(save):
    assert (save.farmer, save.farm_name) == ("Blake", "Sunny")
    assert (save.season, save.start_day, save.year) == ("summer", 12, 2)
    assert save.start_gold == 12345 and save.money_earned == 250000
    assert save.farm_type == "riverland"
    assert save.players == 2


def test_skills_and_facilities(save):
    assert save.farming_level == 10
    assert save.professions == ["tiller", "artisan"]       # 6（渔夫）不是农耕职业，忽略
    assert save.greenhouse and save.route is None           # ccPantry：温室修好、作物收集包已经交完
    assert (save.kegs, save.jars, save.bee_houses) == (3, 1, 2)   # 棚屋里的小桶算，室内的蜂房不算；镇上的不算


def test_animals(save):
    assert save.animals == {"牛": 1, "鸡": 2, "鸭": 1}
    assert any("Void Chicken ×1" in w for w in save.warnings)


def test_achievement_progress(save):
    assert save.shipped == {"防风草": 20, "花椰菜": 3, "草莓": 16}   # “(O)400” 也认得；鸡蛋不是作物
    assert save.friendship == {"海莉": 1500, "阿比盖尔": 600, "科罗布斯": 2600}   # 0 点的路易斯不记
    assert any("1 种不是原版数字 ID" in w for w in save.warnings)
    p = save.progress()
    assert isinstance(p, Progress) and p.earned == 250000 and p.points["海莉"] == 1500


def test_generator_kwargs_and_form_defaults(save):
    kw = save.generator_kwargs()
    assert kw["season"] == "summer" and kw["start_day"] == 12 and kw["owned_animals"] == {"牛": 1, "鸡": 2, "鸭": 1}
    assert "route" not in kw
    form = save.form_defaults(load_achievement_rules())
    assert form["seasons"] == ["summer"] and form["owned_animals"] == "牛:1,鸡:2,鸭:1"
    assert "防风草=20" in form["achievement_progress"] and "万人迷=2" in form["achievement_progress"]
    assert "千万富翁=250000" in form["achievement_progress"]


def test_form_progress_text_parses(save, data):
    text = save.form_defaults(data.achievement_rules)["achievement_progress"]
    p = parse_progress(text, data.achievement_rules, data.crops)
    assert p.shipped["草莓"] == 16 and p.earned == 250000 and p.friends["popular"] == 2


def test_ids_from_data_file():
    ids = load_save_ids()
    assert ids.crops["24"] == "Parsnip" and ids.crops["400"] == "Strawberry"
    assert ids.professions == {"1": "tiller", "4": "artisan", "5": "agriculturist"}
    assert ids.farm_types["7"] == "meadowlands"


def test_generate_from_save(save, data):
    result = generate_strategy("种田", "稳健", client=MockClient(), data=data, revise_rounds=0,
                               **save.generator_kwargs())
    assert result.start_day == 12 and result.finance.start_gold == 12345
    text = "\n".join(result.finance.lines)
    assert "鸡 ×2（已经养着）" in text and "买鸡" not in text        # 已有的动物不扣购买费


# ---------------------------------------------------------------------------
# 容错与安全
# ---------------------------------------------------------------------------

def xml(body: str, root: str = "SaveGame") -> bytes:
    return f'<?xml version="1.0"?><{root} xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">{body}</{root}>'.encode()


def test_minimal_save_only_warns(data):
    s = parse_save(xml("<currentSeason>fall</currentSeason><dayOfMonth>3</dayOfMonth><year>1</year>"), data)
    assert (s.season, s.start_day, s.year) == ("fall", 3, 1)
    assert s.start_gold is None and s.professions == [] and s.animals == {}
    assert any("没有 player" in w for w in s.warnings) and any("没有找到农场" in w for w in s.warnings)


def test_bad_values_are_skipped(data):
    body = ("<player><money>lots</money><farmingLevel>99</farmingLevel><professions><int>x</int><int>5</int></professions>"
            "<basicShipped><item><key><string>24</string></key><value><int>?</int></value></item></basicShipped>"
            "<mailReceived><string>JojaMember</string></mailReceived></player>"
            "<currentSeason>monsoon</currentSeason><dayOfMonth>40</dayOfMonth><whichFarm>9</whichFarm>")
    s = parse_save(xml(body), data)
    assert s.start_gold is None and s.farming_level == 14 and s.professions == ["agriculturist"]
    assert s.season is None and s.start_day is None and s.farm_type is None and s.shipped == {}
    assert s.route is None and any("Joja" in w for w in s.warnings)
    assert any("monsoon" in w for w in s.warnings) and any("不是原版的 8 种" in w for w in s.warnings)


def test_meadowlands_mod_farm_id(data):
    assert parse_save(xml("<whichFarm>7</whichFarm>"), data).farm_type == "meadowlands"
    assert parse_save(xml("<whichFarm>MeadowlandsFarm</whichFarm>"), data).farm_type == "meadowlands"


@pytest.mark.parametrize("raw, message", [
    (b"not xml at all", "不是有效的 XML"),
    (xml("<name>x</name>", root="Farmer"), "SaveGameInfo"),
    (xml("", root="Something"), "根元素"),
    (b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]><SaveGame>&a;</SaveGame>', "DOCTYPE"),
    (b'<!DOCTYPE SaveGame SYSTEM "file:///etc/passwd"><SaveGame/>', "DOCTYPE"),
])
def test_rejects_bad_files(data, raw, message):
    with pytest.raises(SaveParseError, match=message):
        parse_save(raw, data)


def test_missing_file(data, tmp_path):
    with pytest.raises(SaveParseError, match="找不到存档文件"):
        parse_save(tmp_path / "nope", data)


# ---------------------------------------------------------------------------
# 命令行：存档填参数，命令行显式写的优先
# ---------------------------------------------------------------------------

def cli(argv, save):
    import main
    parser = main.build_parser()
    args = parser.parse_args(argv)
    notes = main.apply_save(args, save, main.explicit_dests(parser, argv))
    return args, notes


def test_cli_fills_from_save(save):
    args, notes = cli(["--save", "x"], save)
    assert (args.season, args.year, args.start_day, args.start_gold) == ("summer", 2, 12, 12345)
    assert args.professions == "tiller,artisan" and args.greenhouse and args.kegs == 3
    assert args.owned_animals == {"牛": 1, "鸡": 2, "鸭": 1}
    assert isinstance(args.achievement_progress, Progress) and notes == []


def test_cli_overrides_save(save):
    args, notes = cli(["--save", "x", "--start-gold=999", "--kegs", "0", "--achievement-progress", "防风草=1"], save)
    assert args.start_gold == 999 and args.kegs == 0 and args.achievement_progress == "防风草=1"
    assert args.season == "summer" and args.start_day == 12
    assert any("--start-gold 以命令行为准" in n for n in notes)


def test_cli_other_season_starts_on_day_one(save):
    args, notes = cli(["--save", "x", "-s", "fall"], save)
    assert args.season == "fall" and args.start_day == 1 and any("从第 1 天开始" in n for n in notes)
    args, _ = cli(["--save", "x", "-s", "summer,fall"], save)
    assert args.start_day == 12                                    # 第一个季节和存档一样：保留日期
    args, _ = cli(["--save", "x", "-s", "fall", "--start-day", "5"], save)
    assert args.start_day == 5


def test_explicit_dests_forms():
    import main
    parser = main.build_parser()
    assert {"season", "start_gold", "year"} <= main.explicit_dests(parser, ["-sfall", "--start-gold=5", "-y", "2"])
    assert "season" not in main.explicit_dests(parser, ["--save", "x"])


def test_cli_end_to_end(capsys):
    import main
    main.main(["--save", str(FIXTURE), "--provider", "mock", "--revise-rounds", "0"])
    out, err = capsys.readouterr()
    assert "[存档解析摘要]" in err and "日期：第 2 年夏季第 12 天" in err and "Void Chicken" in err
    assert "第 2 年夏季（第 12–28 天）攻略" in out


def test_cli_bad_save(capsys, tmp_path):
    import main
    bad = tmp_path / "SaveGameInfo"
    bad.write_bytes(xml("", root="Farmer"))
    with pytest.raises(SystemExit):
        main.main(["--save", str(bad), "--provider", "mock"])
    assert "存档导入失败" in capsys.readouterr().err
