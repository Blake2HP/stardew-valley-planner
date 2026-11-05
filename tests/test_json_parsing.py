"""大模型 JSON 输出的容错解析。"""

import pytest

from stardew_planner.json_utils import JSONParseError, clean_common_errors, parse_llm_json


@pytest.mark.parametrize(
    "text, expected, method",
    [
        ('{"a": 1}', {"a": 1}, "strict"),
        ('好的，攻略如下：\n```json\n{"a": 1}\n```\n祝你玩得开心！', {"a": 1}, "strict"),
        ('{"a": "第一行\n第二行"}', {"a": "第一行\n第二行"}, "strict"),  # 字符串里的裸换行
        ('{"a": 1,, "b": 2}', {"a": 1, "b": 2}, "cleaned"),                # 连续逗号
        ('{"a": [1, 2,], "b": {"c": 3,},}', {"a": [1, 2], "b": {"c": 3}}, "cleaned"),  # 末尾逗号
        ('{"a": "x,, y,]", "b": 1,}', {"a": "x,, y,]", "b": 1}, "cleaned"),  # 字符串内的逗号不能动
        ('{"a": "他说\\"好,\\"", "b": 2,}', {"a": '他说"好,"', "b": 2}, "cleaned"),  # 转义引号
    ],
    ids=["valid", "fenced-with-prose", "raw-newline", "double-comma",
         "trailing-commas", "commas-inside-string", "escaped-quote"],
)
def test_parse_without_external_repair(text, expected, method):
    parsed = parse_llm_json(text)
    assert parsed.data == expected
    assert parsed.method == method


@pytest.mark.parametrize(
    "text, expected",
    [
        ('{"a": 1, "b": {"c": 2', {"a": 1, "b": {"c": 2}}),             # 漏结尾括号（输出被截断）
        ('{"a": 1 "b": 2}', {"a": 1, "b": 2}),                           # 漏逗号
        ("{'a': 1, 'b': 'x'}", {"a": 1, "b": "x"}),                      # 单引号
        ('{"daily_goals": {"1": "钓鱼",, "2": "挖矿"', {"daily_goals": {"1": "钓鱼", "2": "挖矿"}}),  # 多种错误叠加
    ],
    ids=["missing-closing-braces", "missing-comma", "single-quotes", "combined-errors"],
)
def test_parse_with_json_repair(text, expected):
    pytest.importorskip("json_repair")
    parsed = parse_llm_json(text)
    assert parsed.data == expected
    assert parsed.method == "repaired"
    assert parsed.was_repaired


def test_clean_common_errors_only_touches_outside_strings():
    assert clean_common_errors('{"a": "1,,2", "b": [1,2,],}') == '{"a": "1,,2", "b": [1,2]}'


@pytest.mark.parametrize("text", ["", "   ", "抱歉，我无法生成攻略。"], ids=["empty", "blank", "no-json"])
def test_unparseable_input_raises(text):
    with pytest.raises(JSONParseError):
        parse_llm_json(text)


def test_wrong_top_level_type_raises():
    with pytest.raises(JSONParseError):
        parse_llm_json("[1, 2, 3]", expect=dict)
