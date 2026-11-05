"""大模型 JSON 输出的容错解析。

解析按代价从低到高逐级尝试，前一级成功就不再往下走：
1. strict   —— 标准 json.loads（去掉 ```json 代码块、截取 JSON 主体后）；
2. cleaned  —— 只在字符串外部修复最常见的问题：连续逗号、对象/数组末尾多余的逗号；
3. repaired —— 交给 json_repair 库，处理漏括号、漏引号、截断输出、单引号等。

第 2 级是确定性的小修补，比 json_repair 更可控：例如 `"a": 1,, "b"`，
json_repair 会把 `1,` 解析成字符串 "1,"，而清理后能得到正确的数字 1。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

try:
    import json_repair
except ImportError:  # 没装也能跑，只是少了第 3 级
    json_repair = None


class JSONParseError(ValueError):
    pass


@dataclass(frozen=True)
class ParsedJSON:
    data: Any
    method: str  # strict / cleaned / repaired

    @property
    def was_repaired(self) -> bool:
        return self.method != "strict"


_FENCE = re.compile(r"```(?:json|JSON)?")
_REPEATED_COMMA = re.compile(r",(\s*,)+")
_TRAILING_COMMA = re.compile(r",(\s*[}\]])")


def extract_json_text(text: str) -> str:
    """去掉代码块标记和前后的说明文字，返回 JSON 主体。

    有配对的结尾括号时截到最后一个 `}`；输出被截断（漏了结尾括号）时保留到文本末尾，
    交给后面的修复步骤补全。
    """
    text = _FENCE.sub("", text).strip()
    starts = [i for i in (text.find("{"), text.find("[")) if i != -1]
    if not starts:
        raise JSONParseError("输出中找不到 JSON 对象")
    body = text[min(starts):]
    end = max(body.rfind("}"), body.rfind("]"))
    if end != -1 and _brackets_balanced(body[:end + 1]):
        return body[:end + 1]
    return body


def _split_strings(text: str) -> list[tuple[bool, str]]:
    """把文本切成 (是否在字符串内, 片段) 列表，正确处理转义引号。"""
    parts, buf, in_str, escape = [], [], False, False
    for ch in text:
        if in_str:
            buf.append(ch)
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                parts.append((True, "".join(buf)))
                buf, in_str = [], False
        elif ch == '"':
            if buf:
                parts.append((False, "".join(buf)))
            buf, in_str = [ch], True
        else:
            buf.append(ch)
    if buf:
        parts.append((in_str, "".join(buf)))
    return parts


def _brackets_balanced(text: str) -> bool:
    depth = 0
    for in_str, seg in _split_strings(text):
        if in_str:
            continue
        for ch in seg:
            if ch in "{[":
                depth += 1
            elif ch in "}]":
                depth -= 1
                if depth < 0:
                    return False
    return depth == 0


def clean_common_errors(text: str) -> str:
    """只修改字符串外部：合并连续逗号，删除 `}` / `]` 前多余的逗号。"""
    # 逗号和其后的括号之间只有空白，必然落在同一个“字符串外”片段里，一次遍历即可
    return "".join(
        seg if in_str else _TRAILING_COMMA.sub(r"\1", _REPEATED_COMMA.sub(",", seg))
        for in_str, seg in _split_strings(text)
    )


def parse_llm_json(text: str, *, expect: type = dict) -> ParsedJSON:
    """容错解析大模型返回的 JSON。全部失败时抛出 JSONParseError。"""
    if not text or not text.strip():
        raise JSONParseError("模型返回为空")
    body = extract_json_text(text)
    errors = []

    for method, candidate in (("strict", body), ("cleaned", clean_common_errors(body))):
        try:
            data = json.loads(candidate, strict=False)  # strict=False 允许字符串里有换行等控制字符
        except json.JSONDecodeError as e:
            errors.append(f"{method}: {e}")
            continue
        if isinstance(data, expect):
            return ParsedJSON(data, method)
        errors.append(f"{method}: 顶层类型是 {type(data).__name__}，期望 {expect.__name__}")

    if json_repair is not None:
        data = json_repair.loads(clean_common_errors(body))
        if isinstance(data, expect) and data:
            return ParsedJSON(data, "repaired")
        errors.append(f"repaired: 修复结果为 {type(data).__name__} {str(data)[:50]!r}")
    else:
        errors.append("repaired: 未安装 json_repair，跳过")

    raise JSONParseError("JSON 解析失败（" + "；".join(errors) + "）")
