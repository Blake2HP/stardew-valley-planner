"""Steam 成就目标（--achievements / --achievement-progress）：数据、缺口、Prompt、输出字段与成就校验。"""

import subprocess
import sys
from pathlib import Path

import pytest

from stardew_planner import generate_strategy
from stardew_planner.achievements import (
    Progress, describe_targets, load_achievement_rules, parse_progress, resolve_goals, season_targets,
)
from stardew_planner.llm_client import MockClient

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def rules():
    return load_achievement_rules()


# ---------------------------------------------------------------------------
# 任务 1：数据与参数
# ---------------------------------------------------------------------------

def test_data_file(rules, data):
    by_key = {a.key: a for a in rules.achievements}
    assert by_key["legend"].target == 10_000_000 and by_key["legend"].name == "千万富翁"
    assert by_key["millionaire"].target == 1_000_000 and by_key["millionaire"].name == "百万富翁"
    poly = by_key["polyculture"]
    assert poly.target == 15 and len(poly.crops) == 28 and "多作种植" in poly.aliases
    known = {c.name_en for c in data.crops}
    assert set(poly.crops) <= known  # 28 种作物都在 crops.json 里
    popular = by_key["popular"]
    assert (popular.target, popular.hearts, popular.points_per_heart) == (20, 5, 250)
    assert data.achievement_rules is not None


def test_resolve_goals(rules):
    goals = resolve_goals("多作种植, 万人迷，Legend", rules)
    assert [(g.achievement.key, g.label) for g in goals] == [
        ("polyculture", "多作种植"), ("popular", "万人迷"), ("legend", "Legend")]
    assert resolve_goals(None, rules) == [] and resolve_goals("", rules) == []
    assert len(resolve_goals("混合栽培,多作种植", rules)) == 1  # 同一个成就只算一次
    with pytest.raises(ValueError, match="未知的成就：钓鱼大师"):
        resolve_goals("钓鱼大师", rules)
    with pytest.raises(ValueError, match="没有成就数据"):
        resolve_goals("万人迷", None)


def test_parse_progress(rules, data):
    p = parse_progress("防风草=15, Potato:7，万人迷=12；千万富翁=50000", rules, data.crops)
    assert p.shipped == {"防风草": 15, "土豆": 7} and p.friends == {"popular": 12} and p.earned == 50000
    assert parse_progress(None, rules, data.crops) == Progress()
    assert parse_progress({"万人迷": 3}, rules, data.crops).friends == {"popular": 3}


@pytest.mark.parametrize("spec, message", [
    ("防风草", "写法应为"),
    ("钓鱼大师=3", "既不是作物"),
    ("混合栽培=3", "既不是作物"),
])
def test_bad_progress(rules, data, spec, message):
    with pytest.raises(ValueError, match=message):
        parse_progress(spec, rules, data.crops)


def test_cli_rejects_unknown_achievement():
    r = subprocess.run([sys.executable, "main.py", "--provider", "mock", "--achievements", "钓鱼大师"], cwd=ROOT,
                       capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 2 and "未知的成就：钓鱼大师" in r.stderr


def test_strategy_keeps_achievements(data, indexed_store):
    result = generate_strategy("种田", "稳健", "spring", client=MockClient(), data=data, guide_store=indexed_store,
                               achievements="多作种植", achievement_progress="防风草=3")
    assert [g.achievement.key for g in result.achievements] == ["polyculture"]
    assert result.achievement_progress.shipped == {"防风草": 3}
    solo = generate_strategy("种田", "稳健", "spring", client=MockClient(), data=data, guide_store=indexed_store)
    assert solo.achievements == [] and solo.achievement_progress == Progress()


# ---------------------------------------------------------------------------
# 任务 2：本季缺口与 Prompt 注入
# ---------------------------------------------------------------------------

class RecordingClient(MockClient):
    def complete(self, system, messages):
        self.system, self.prompt = system, messages[0]["content"]
        return super().complete(system, messages)


def test_spring_targets(rules, data):
    goals = resolve_goals("多作种植,万人迷,千万富翁", rules)
    t = season_targets(goals, parse_progress("防风草=15,土豆=6", rules, data.crops), data.crops, "spring", 1)
    assert {c.crop.name: c.need for c in t.crops} == {"花椰菜": 15, "青豆": 15, "甘蓝": 15, "土豆": 9, "草莓": 15}
    assert {c.crop.name for c in t.optional} == {"咖啡豆", "大黄"}   # 旅行货车、沙漠绿洲：可选
    assert t.later == ["大蒜（第 2 年起）"] and t.shipped_done == ["防风草"]
    (gap,) = t.friends
    assert (gap.left, gap.left_points, t.seasons_left, gap.per_season) == (20, 25000, 4, 6250)  # 20 × 1250 / 4
    assert [(g.achievement.key, gap) for g, gap in t.earnings] == [("legend", 10_000_000)]


def test_year_two_and_fall_targets(rules, data):
    goals = resolve_goals("混合栽培", rules)
    spring2 = season_targets(goals, Progress(), data.crops, "spring", 2)
    assert "大蒜" in {c.crop.name for c in spring2.crops} and spring2.later == []
    fall = season_targets(goals, Progress(shipped={"玉米": 15}), data.crops, "fall", 1)
    names = {c.crop.name for c in fall.crops}
    assert "小麦" in names and "玉米" not in names and "玉米" in fall.shipped_done  # 夏秋作物秋季没运够还要种
    assert "甜菜" in {c.crop.name for c in fall.optional}                          # 甜菜种子在沙漠绿洲
    assert "洋蓟（第 2 年起）" in fall.later


def test_friend_target_spreads_over_remaining_seasons(rules, data):
    goals = resolve_goals("万人迷", rules)
    p = Progress(friends={"popular": 4}, points={"海莉": 1300, "艾米丽": 600}, seasons_done=1)
    t = season_targets(goals, p, data.crops, "summer", 1)
    gap = t.friend_gap
    # 用户给的 4 人 + 海莉已到 5 颗心 → 还差 15 人；计分 = 4 × 1250 + 1250（封顶）+ 600 = 6850
    assert (gap.left, gap.left_points, t.seasons_left, gap.per_season) == (15, 18150, 3, 6050)


def test_prompt_block(data, indexed_store):
    client = RecordingClient()
    generate_strategy("种田", "稳健", "spring", client=client, data=data, guide_store=indexed_store,
                      achievements="多作种植,万人迷", achievement_progress="防风草=15")
    block = client.prompt.split("### 【🏆 成就规划目标】")[1].split("### 相关攻略片段")[0]
    assert "花椰菜 15 个" in block and "已经运够：防风草" in block and "可选" in block
    assert "多作种植（混合栽培：" in block and "万人迷（与 20 个人" in block
    assert "本季的日程至少要增加 6,250 点" in block and "5 颗心 = 5 × 250 = 1250 点" in block
    assert "和{村民}聊天" in block and "没聊天就 −2" in block


def test_no_achievements_prompt_unchanged(data, indexed_store):
    client = RecordingClient()
    generate_strategy("种田", "稳健", "spring", client=client, data=data, guide_store=indexed_store)
    assert "成就" not in client.prompt and "\n\n### 相关攻略片段" in client.prompt
    assert "\n\n\n### 相关攻略片段" not in client.prompt


def test_progress_carries_across_seasons(data, indexed_store):
    from stardew_planner import generate_plan
    out = generate_plan("种田", "稳健", ["spring", "summer"], client=MockClient(), data=data, guide_store=indexed_store,
                        achievements="多作种植,万人迷,千万富翁")
    spring, summer = out.results
    end = spring.achievement_progress_end
    assert summer.achievement_progress == end and end.seasons_done == 1
    assert end.shipped["花椰菜"] > 0 and end.earned > 0 and end.points  # mock 的日程里有生日送礼
    assert summer.achievement_targets.seasons_left == 3
    assert summer.achievement_progress_end.seasons_done == 2


# ---------------------------------------------------------------------------
# 任务 3：achievement_milestones 与系统 Prompt
# ---------------------------------------------------------------------------

def test_system_prompt_rules():
    from stardew_planner.prompts import SYSTEM_PROMPT, build_system_prompt
    assert build_system_prompt() == SYSTEM_PROMPT                     # 不选成就：系统 Prompt 不变
    rules_text = build_system_prompt(achievements=True)
    assert "## 成就规划" in rules_text and "achievement_plan" in rules_text and "achievement_milestones" in rules_text
    assert "## 联机模式" in build_system_prompt(2, achievements=True)  # 和联机规则可以同时出现


def test_milestones_are_parsed_leniently():
    from stardew_planner.generator import validate_payload
    from stardew_planner.llm_client import MOCK_RESPONSE
    payload = {**MOCK_RESPONSE, "achievement_milestones": [
        {"day": 9, "achievement": "万人迷", "goal": "送海莉黄水仙"},
        {"day": "3", "goal": "种 15 颗土豆"},
        {"day": 40, "goal": "超出范围"}, {"goal": "没有日期"}, "随便写的",
    ]}
    parsed = validate_payload(payload)
    assert parsed.achievement_milestones == [
        {"day": 3, "achievement": "", "goal": "种 15 颗土豆"},
        {"day": 9, "achievement": "万人迷", "goal": "送海莉黄水仙"},
    ]
    assert validate_payload(MOCK_RESPONSE).achievement_milestones == []  # 字段可选，不影响普通生成


def test_strategy_renders_achievement_section(data, indexed_store):
    client = RecordingClient()
    result = generate_strategy("种田", "稳健", "spring", client=client, data=data, guide_store=indexed_store,
                               achievements="多作种植,万人迷")
    assert "## 成就规划" in client.system
    assert result.macro_strategy["achievement_plan"] and len(result.achievement_milestones) == 2
    md = result.markdown
    assert "## 🏆 成就规划" in md and "**实现思路**" in md and "| 第 1 天 | 成就目标 |" in md
    assert md.index("## 🏆 成就规划") < md.index("## 📅 每日核心目标")


def test_plain_strategy_has_no_achievement_section(data, indexed_store):
    client = RecordingClient()
    result = generate_strategy("种田", "稳健", "spring", client=client, data=data, guide_store=indexed_store)
    assert "成就" not in result.markdown and "## 成就规划" not in client.system
    assert result.achievement_targets is None and result.achievement_milestones == []


# ---------------------------------------------------------------------------
# 任务 4：成就校验与自动修正
# ---------------------------------------------------------------------------

def _targets(rules, data, spec="多作种植,万人迷", progress=None):
    return season_targets(resolve_goals(spec, rules), progress or Progress(), data.crops, "spring", 1)


FULL = {"achievement_plan": "思路"}
MILESTONES = [{"day": 1, "achievement": "混合栽培", "goal": "种土豆"}]


def test_polyculture_check(rules, data):
    from stardew_planner.achievements import check_achievements
    t = _targets(rules, data, "多作种植", parse_progress("花椰菜=15,青豆=15,甘蓝=15,草莓=15", rules, data.crops))
    units = {"防风草": 15.0, "土豆": 12.5}
    problems = check_achievements(t, units, {"防风草", "土豆"}, {}, FULL, MILESTONES)
    assert problems == ["⚠️ 成就校验：目标包含“多作种植”，但本季安排种植的土豆预计只能直接出货 12.5 个，不足 15 个。请修改种植清单！"]
    problems = check_achievements(t, {"土豆": 20}, {"土豆"}, {}, FULL, MILESTONES)
    assert problems == ["⚠️ 成就校验：目标包含“多作种植”，但本季没有安排种植防风草（要收获并出货 15 个）。请修改种植清单！"]
    assert check_achievements(t, {"防风草": 15, "土豆": 18.75}, {"防风草", "土豆"}, {}, FULL, MILESTONES) == []


def test_popular_check(rules, data):
    from stardew_planner.achievements import check_achievements
    t = _targets(rules, data, "万人迷", Progress(points={"海莉": 500}))
    gifts = {2: ["海莉"], 9: ["阿比盖尔"], 16: ["潘妮"]}
    end = {"海莉": 1500, "阿比盖尔": 1000, "潘妮": 300}  # 计分：1250（封顶）+ 1000 + 300 = 2550，比季初多 2050
    problems = check_achievements(t, {}, set(), gifts, FULL, MILESTONES, friend_points=end)
    assert problems == [
        "⚠️ 成就校验：目标包含“万人迷”，但第 22-28 天没有安排送礼。请每周至少安排一天“送{村民}{礼物}”！",
        "⚠️ 成就校验：目标包含“万人迷”，但按日程估算本季好感度只增加 2,050 点（季末估算：海莉 6.0 颗心、"
        "阿比盖尔 4.0 颗心、潘妮 1.2 颗心），至少需要 6,125 点。请多安排给目标村民送礼，并把“和{村民}聊天”写进日程！",
    ]  # 至少需要 ⌈(25000 − 500) / 4⌉ = 6125 点
    gifts = {2: ["阿比盖尔", "潘妮"], 9: ["莉亚"], 16: ["玛鲁"], 23: ["海莉", "艾米丽"]}
    end = {n: 1250 for n in ("海莉", "阿比盖尔", "潘妮", "莉亚", "玛鲁", "艾米丽")}
    assert check_achievements(t, {}, set(), gifts, FULL, MILESTONES, friend_points=end) == []
    done = _targets(rules, data, "万人迷", Progress(friends={"popular": 20}))  # 已经达成：不再检查送礼
    assert check_achievements(done, {}, set(), {}, FULL, MILESTONES) == []


def test_plan_and_milestones_required(rules, data):
    from stardew_planner.achievements import check_achievements
    t = _targets(rules, data, "千万富翁")
    assert check_achievements(t, {}, set(), {}, FULL, MILESTONES) == []  # 赚钱类只提示，不校验金额
    problems = check_achievements(t, {}, set(), {}, {}, [])
    assert [p.split("：")[1].split("，")[0] for p in problems] == [
        "macro_strategy 里没有 achievement_plan", "没有给出 achievement_milestones"]


def test_checks_reach_the_revise_loop(data, indexed_store):
    import json

    from stardew_planner.llm_client import MOCK_RESPONSE

    class Revising(MockClient):
        calls = []

        def complete(self, system, messages):
            self.calls.append(messages[-1]["content"])
            payload = json.loads(super().complete(system, messages))
            if len(self.calls) > 1:  # 修正版：第 1 周给阿比盖尔送紫水晶
                payload["daily_goals"] = {**payload["daily_goals"], "3": "领取鱼竿，海滩钓鱼，送阿比盖尔紫水晶"}
            return json.dumps(payload, ensure_ascii=False)

    original = MOCK_RESPONSE["daily_goals"]["3"]
    client = Revising()
    result = generate_strategy("种田", "稳健", "spring", client=client, data=data, guide_store=indexed_store,
                               achievements="多作种植,万人迷", revise_rounds=1)
    assert any(p.startswith("⚠️ 成就校验：目标包含“多作种植”") for p in result.initial_problems)
    assert "成就校验：目标包含“万人迷”，但第 1-7 天没有安排送礼" in client.calls[1]  # 反馈给模型
    assert result.revisions[0]["accepted"]
    assert not any("第 1-7 天没有安排送礼" in p for p in result.problems)
    assert "第 1-7 天没有安排送礼" not in result.markdown and "成就校验" in result.markdown
    assert MOCK_RESPONSE["daily_goals"]["3"] == original  # 没有改到共享的 mock 数据


def test_plain_generation_has_no_achievement_checks(data, indexed_store):
    result = generate_strategy("种田", "稳健", "spring", client=MockClient(), data=data, guide_store=indexed_store)
    assert not any("成就校验" in p for p in result.problems + result.season_warnings)


# ---------------------------------------------------------------------------
# 混合栽培只算直接出货：加工前先留够，留给收集包的不算
# ---------------------------------------------------------------------------

def test_processing_keeps_shipping_amount(data):
    from stardew_planner.bonuses import NO_BONUSES
    from stardew_planner.processing import simulate_processing
    potato = next(c for c in data.crops if c.name == "土豆")
    harvests = [(7, potato, 20), (14, potato, 20)]  # 每次 20 格 × 1.25 = 25 个
    free = simulate_processing(harvests, data.artisan_rules_for, {"小桶": 100}, NO_BONUSES)
    kept = simulate_processing(harvests, data.artisan_rules_for, {"小桶": 100}, NO_BONUSES, keep={"土豆": 15})
    assert free.held_units["土豆"] == pytest.approx(50)
    assert kept.held_units["土豆"] == pytest.approx(35)   # 先留 15 个直接卖，其余照常加工
    assert kept.net < free.net                            # 少加工 15 个，加工净增变少


def test_shipping_counts_exclude_processing(data, indexed_store):
    goal = dict(client=MockClient(), data=data, guide_store=indexed_store, kegs=200, revise_rounds=0)
    plain = generate_strategy("种田", "稳健", "spring", **goal)
    assert plain.finance.processed_units.get("防风草", 0) > 0          # 不选成就：小桶照常吃掉防风草
    result = generate_strategy("种田", "稳健", "spring", achievements="多作种植", **goal)
    f = result.finance
    assert f.processed_units.get("防风草", 0) == pytest.approx(0)       # 15 格防风草全部留着直接出货
    assert result.achievement_progress_end.shipped["防风草"] == 15
    assert not any("防风草" in p for p in result.problems if p.startswith("⚠️ 成就校验"))


def test_bundle_units_are_not_shipped(data, indexed_store):
    result = generate_strategy("种田", "稳健", "spring", client=MockClient(), data=data, guide_store=indexed_store,
                               achievements="多作种植", route="community_center", revise_rounds=0)
    assert result.finance.bundle_units.get("防风草") == 1
    assert result.achievement_progress_end.shipped["防风草"] == 14
    assert ("⚠️ 成就校验：目标包含“多作种植”，但本季安排种植的防风草预计只能直接出货 14 个"
            "（预计收获 15 个，其中 1 个留给收集包），不足 15 个。请修改种植清单！") in result.problems


# ---------------------------------------------------------------------------
# 补全的成就：全部 49 个、单一栽培、多个友谊成就、只写进 Prompt 的成就
# ---------------------------------------------------------------------------

def test_all_achievements_loaded(rules, data):
    from stardew_planner.achievements import KINDS
    items = rules.achievements
    assert len(items) == 49
    assert len({a.key for a in items}) == 49 and len({a.name for a in items}) == 49
    assert all(a.kind in KINDS and a.category and a.description for a in items)
    kinds = {}
    for a in items:
        kinds.setdefault(a.kind, []).append(a.name)
    assert kinds["earnings"] == ["新手", "牛仔", "农场主", "百万富翁", "千万富翁"]
    assert kinds["friendship"] == ["新朋友", "最好的朋友", "深受喜爱的农夫", "拉帮结派", "网络交友", "万人迷"]
    assert kinds["ship_one_crop"] == ["单一栽培"] and len(kinds["info"]) == 36
    mono = rules.find("Monoculture")
    known = {c.name_en for c in data.crops}
    assert len(mono.crops) == 33 and set(mono.crops) - known == {"Ancient Fruit"}  # 上古水果不在 crops.json
    assert rules.find("工匠").name_en == "Artisan" and rules.find("best friends").hearts == 10


def test_monoculture_targets(rules, data):
    goals = resolve_goals("单一栽培", rules)
    t = season_targets(goals, Progress(), data.crops, "spring", 1)
    names = {c.crop.name for c in t.mono}
    assert {"防风草", "土豆", "郁金香", "蓝爵"} <= names and "大蒜" not in names  # 大蒜第 2 年起
    assert {c.need for c in t.mono} == {75}                                         # ⌈300 / 4⌉
    t = season_targets(goals, Progress(shipped={"土豆": 100}), data.crops, "spring", 1)
    assert next(c.need for c in t.mono if c.crop.name == "土豆") == 50              # ⌈200 / 4⌉
    done = season_targets(goals, Progress(shipped={"蓝莓": 300}), data.crops, "spring", 1)
    assert done.mono_done == "蓝莓" and done.mono == []


def test_monoculture_check(rules, data):
    from stardew_planner.achievements import check_achievements, keep_for_shipping
    t = season_targets(resolve_goals("单一栽培", rules), Progress(), data.crops, "spring", 1)
    assert check_achievements(t, {"土豆": 80}, {"土豆"}, {}, FULL, MILESTONES) == []
    assert check_achievements(t, {"土豆": 20}, {"土豆"}, {}, FULL, MILESTONES, {"土豆": 20, "防风草": 5}) == [
        "⚠️ 成就校验：目标包含“单一栽培”，但本季出货最多的土豆预计只能直接出货 20 个，不足 75 个。请集中种一种作物！"]
    assert check_achievements(t, {}, set(), {}, FULL, MILESTONES) == [
        "⚠️ 成就校验：目标包含“单一栽培”，但本季没有种可以计入的作物（选一种集中种，直接出货至少 75 个）。请修改种植清单！"]
    assert keep_for_shipping(t, {"土豆": 90, "防风草": 15}) == {"土豆": 75}     # 只给收获最多的那种预留
    both = season_targets(resolve_goals("单一栽培,混合栽培", rules), Progress(), data.crops, "spring", 1)
    assert keep_for_shipping(both, {"土豆": 90})["土豆"] == 75          # 同一件出货两个成就都算


def test_several_friendship_goals(rules, data):
    goals = resolve_goals("新朋友,万人迷,深受喜爱的农夫", rules)
    t = season_targets(goals, Progress(friends={"a_new_friend": 1}), data.crops, "spring", 1)
    assert [(x.goal.achievement.key, x.left, x.per_season) for x in t.friends] == [
        ("a_new_friend", 0, 0), ("popular", 20, 6250), ("the_beloved_farmer", 8, 5000)]
    assert t.friend_gap.goal.achievement.key == "popular"
    text = describe_targets(t, rules)
    assert "万人迷（与 20 个人" in text and "同时计入：深受喜爱的农夫" in text and "新朋友（与某人达到 5 颗心的等级）：按已有进度已经达成" in text
    assert "送花束之前最多 8 颗心" in text


def test_info_goals_only_in_prompt(rules, data):
    from stardew_planner.achievements import check_achievements
    t = season_targets(resolve_goals("渔夫,当地传奇", rules), Progress(), data.crops, "summer", 1)
    assert [g.achievement.key for g in t.info] == ["fisherman", "local_legend"]
    text = describe_targets(t, rules)
    assert "长期目标（程序没有相关数据，只供参考、不校验）：渔夫（抓住 10 种不同的鱼）；当地传奇" in text
    assert check_achievements(t, {}, set(), {}, FULL, MILESTONES) == []


def test_end_to_end_with_new_kinds(data, indexed_store):
    result = generate_strategy("种田", "稳健", "spring", client=MockClient(), data=data, guide_store=indexed_store,
                               achievements="单一栽培,网络交友,渔夫", revise_rounds=0)
    md = result.markdown
    assert "**单一栽培（运送 300 份一种农作物，可选 33 种）**" in md and "**长期目标**（程序不校验）：渔夫" in md
    assert any("单一栽培" in p for p in result.problems)  # mock 防风草只有 15 个


def test_list_achievements_cli():
    r = subprocess.run([sys.executable, "main.py", "--list-achievements"], cwd=ROOT,
                       capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0
    assert "【赚钱】" in r.stdout and "✔ 万人迷（Popular）" in r.stdout and "    渔夫（Fisherman）" in r.stdout


# ---------------------------------------------------------------------------
# 好感度估算（wiki Friendship）
# ---------------------------------------------------------------------------

def test_friendship_birthday_talk_and_decay(rules, data):
    from stardew_planner.achievements import friendship_season
    goals = {1: "和海莉聊天，送海莉向日葵", 14: "送海莉向日葵"}  # 第 14 天是海莉生日
    end = friendship_season(goals, data, rules.friendship, "spring", {})
    # 第 1 天 聊天 20 + 最爱 80 = 100；第 2-13 天没聊天 −2 × 12 = 76；第 14 天生日 80 × 8 = 640、没聊天 −2 → 714；
    # 第 15-28 天 −2 × 14 → 686
    assert end == {"海莉": 686}


def test_friendship_weekly_limit_and_bonus(rules, data):
    from stardew_planner.achievements import friendship_season
    goals = {d: "送阿比盖尔紫水晶" for d in (1, 2, 3)}
    end = friendship_season(goals, data, rules.friendship, "spring", {})
    # 第 1、2 天各 +80（第 3 天超过每周 2 次，不算），每天没聊天 −2；第 7 天送满 2 次 +10；之后每天 −2
    assert end == {"阿比盖尔": 80 - 2 + 80 - 2 - 2 * 5 + 10 - 2 * 21}


def test_friendship_caps(rules, data):
    from stardew_planner.achievements import friendship_season
    talk = {d: "和海莉、莱纳斯聊天" for d in range(1, 29)}
    end = friendship_season(talk, data, rules.friendship, "spring", {"海莉": 1990, "莱纳斯": 2490})
    assert end == {"海莉": 2000, "莱纳斯": 2500}  # 可以结婚的村民送花束前最多 8 颗心，其他最多 10 颗心
    idle = friendship_season({}, data, rules.friendship, "spring", {"海莉": 2000, "莱纳斯": 30})
    assert idle == {"海莉": 2000, "莱纳斯": 0}     # 到顶的不衰减；衰减不会低于 0


def test_talk_detection(data):
    from stardew_planner.achievements import talked_to
    assert talked_to("和海莉、阿比盖尔聊天，钓鱼", data) == {"海莉", "阿比盖尔"}
    assert talked_to("送海莉向日葵，钓鱼", data) == set()   # 送礼不算聊天
    assert talked_to("钓鱼；跟潘妮打招呼", data) == {"潘妮"}
