"""大模型客户端：统一 `complete(system, messages) -> str` 接口，支持 Anthropic / OpenAI / Mock。"""

from __future__ import annotations

import json
import os
from typing import Protocol

Message = dict  # {"role": "user" | "assistant", "content": str}


class LLMClient(Protocol):
    def complete(self, system: str, messages: list[Message]) -> str: ...


class AnthropicClient:
    def __init__(self, model: str | None = None, max_tokens: int = 8000):
        import anthropic  # 延迟导入：只装 openai 时也能用

        self.client = anthropic.Anthropic()  # 读取 ANTHROPIC_API_KEY
        self.model = model or os.getenv("ANTHROPIC_MODEL", "claude-opus-5-5")
        self.max_tokens = max_tokens

    def complete(self, system: str, messages: list[Message]) -> str:
        resp = self.client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system,
            messages=messages,
        )
        return "".join(block.text for block in resp.content if block.type == "text")


class OpenAIClient:
    def __init__(self, model: str | None = None, max_tokens: int = 8000):
        import openai

        self.client = openai.OpenAI()  # 读取 OPENAI_API_KEY
        self.model = model or os.getenv("OPENAI_MODEL", "gpt-4.1")
        self.max_tokens = max_tokens

    def complete(self, system: str, messages: list[Message]) -> str:
        resp = self.client.chat.completions.create(
            model=self.model,
            max_tokens=self.max_tokens,
            response_format={"type": "json_object"},
            messages=[{"role": "system", "content": system}, *messages],
        )
        return resp.choices[0].message.content or ""


class MockClient:
    """离线调试用：返回一份固定的春季规划，不消耗 API 额度。"""

    def __init__(self, model: str | None = None, **_):
        self.model = model or "mock"

    def complete(self, system: str, messages: list[Message]) -> str:
        return json.dumps(MOCK_RESPONSE, ensure_ascii=False)


_PROVIDERS = {"anthropic": AnthropicClient, "openai": OpenAIClient, "mock": MockClient}


def get_client(provider: str | None = None, model: str | None = None) -> LLMClient:
    provider = (provider or os.getenv("LLM_PROVIDER", "anthropic")).lower()
    if provider not in _PROVIDERS:
        raise ValueError(f"未知 provider: {provider!r}，可选: {', '.join(_PROVIDERS)}")
    return _PROVIDERS[provider](model=model)


MOCK_RESPONSE = {
    "macro_strategy": {
        "summary": "前半季靠钓鱼和防风草滚雪球，复活节后转向草莓与花椰菜，季末为夏季蓝莓攒足资金。",
        "pillars": [
            {"title": "钓鱼作为启动资金", "detail": "前两周钓鱼几乎不耗体力且收益稳定，用来快速积累买种子的钱。"},
            {"title": "复活节押注草莓", "detail": "13 日买草莓种子、14 日种下，季末前可收获两次以上，是春季最高回报的投资。"},
            {"title": "雨天下矿", "detail": "下雨免浇水，把雨天留给矿井，尽早拿到铜矿升级工具。"},
            {"title": "为夏季留钱", "detail": "第 25 天后不再大量购买春季种子，现金留给夏季第一天的蓝莓。"},
        ],
        "season_goals": ["钓鱼等级达到 5 级", "复活节前存款达到 2000g", "矿井推进到 40 层", "季末现金不少于 5000g"],
    },
    "key_milestones": [
        {"day": 5, "event": "矿井开放", "preparation": "准备食物回复体力", "prepare_by_day": 4},
        {"day": 13, "event": "复活节", "preparation": "预留 2000g 以上购买草莓种子", "prepare_by_day": 12},
        {"day": 15, "event": "鲑莓采集季", "preparation": "空出背包格子，计划好采集路线", "prepare_by_day": 14},
        {"day": 24, "event": "花舞节", "preparation": "当天商店不营业，提前买好补给", "prepare_by_day": 23},
        {"day": 28, "event": "换季日", "preparation": "收完所有作物，确认夏季种子资金", "prepare_by_day": 25},
    ],
    "daily_goals": {
        "1": "种下防风草并清理农场",
        "2": "领取鱼竿，海滩钓鱼",
        "3": "海滩钓鱼攒钱",
        "4": "海滩钓鱼攒钱",
        "5": "下矿井推进到 10 层",
        "6": "下矿井推进到 10 层",
        "7": "补种花椰菜和土豆",
        "8": "河边钓鱼攒钱",
        "9": "河边钓鱼攒钱",
        "10": "河边钓鱼攒钱",
        "11": "下矿井推进到 20 层",
        "12": "出售作物，凑齐草莓资金",
        "13": "复活节购买草莓种子",
        "14": "种下草莓",
        "15": "采集鲑莓，钓鱼",
        "16": "采集鲑莓，钓鱼",
        "17": "采集鲑莓，钓鱼",
        "18": "采集鲑莓，钓鱼",
        "19": "下矿井推进到 30 层",
        "20": "下矿井推进到 30 层",
        "21": "升级铜水壶",
        "22": "收获花椰菜，补种土豆",
        "23": "下矿井推进到 40 层",
        "24": "参加花舞节",
        "25": "收获草莓，存钱备夏",
        "26": "山间湖泊钓鱼",
        "27": "山间湖泊钓鱼",
        "28": "收完作物，购买夏季种子",
    },
}
