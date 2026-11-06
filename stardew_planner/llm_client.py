"""大模型客户端：统一 `complete(system, messages) -> str` 接口，支持 Anthropic / OpenAI / Mock。"""

from __future__ import annotations

import json
import os
import re
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
    """离线调试用：按 Prompt 中的季节返回一份固定规划，不消耗 API 额度。"""

    _SEASON = re.compile(r"年(春季|夏季|秋季|冬季)的攻略")
    _KEYS = {"春季": "spring", "夏季": "summer", "秋季": "fall", "冬季": "winter"}

    def __init__(self, model: str | None = None, **_):
        self.model = model or "mock"

    def complete(self, system: str, messages: list[Message]) -> str:
        m = self._SEASON.search(messages[0]["content"]) if messages else None
        season = self._KEYS[m.group(1)] if m else "spring"
        return json.dumps(MOCK_RESPONSES[season], ensure_ascii=False)


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
        {"day": 14, "event": "海莉生日", "preparation": "采集一朵黄水仙留作礼物", "prepare_by_day": 13},
        {"day": 20, "event": "谢恩生日", "preparation": "提前在酒吧买好披萨", "prepare_by_day": 19},
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
        "14": "种下草莓，送海莉黄水仙",
        "15": "采集鲑莓，钓鱼",
        "16": "采集鲑莓，钓鱼",
        "17": "采集鲑莓，钓鱼",
        "18": "采集鲑莓，钓鱼",
        "19": "去酒吧买披萨，下矿井",
        "20": "送谢恩披萨，下矿井",
        "21": "升级铜水壶",
        "22": "收获花椰菜，补种土豆",
        "23": "下矿井推进到 40 层",
        "24": "参加花舞节",
        "25": "收获草莓，存钱备夏",
        "26": "山间湖泊钓鱼",
        "27": "山间湖泊钓鱼",
        "28": "收完作物，购买夏季种子",
    },
    "plantings": [
        {"day": 1, "crop": "防风草", "tiles": 15},
        {"day": 7, "crop": "花椰菜", "tiles": 5},
        {"day": 7, "crop": "土豆", "tiles": 5},
        {"day": 14, "crop": "草莓", "tiles": 5},
        {"day": 22, "crop": "土豆", "tiles": 5},
    ],
    "other_income": [
        {"source": "钓鱼", "gold": 2500, "note": "约 12 天钓鱼"},
        {"source": "挖矿", "gold": 800, "note": "出售矿石与宝石"},
    ],
}


def _goals(*ranges: tuple[int, int, str]) -> dict[str, str]:
    """用 (起始日, 结束日, 目标) 区间生成 28 天日程。"""
    out = {str(d): goal for start, end, goal in ranges for d in range(start, end + 1)}
    assert sorted(map(int, out)) == list(range(1, 29))
    return out


MOCK_SUMMER = {
    "macro_strategy": {
        "summary": "用春季积蓄一次性种下蓝莓和玉米，蓝莓反复收获回本，玉米留到秋季继续产出。",
        "pillars": [
            {"title": "第一天投入全部种植资金", "detail": "蓝莓和玉米都可再生，越早种收获次数越多。"},
            {"title": "玉米跨季", "detail": "玉米夏秋两季都能生长，季末不必清理。"},
            {"title": "宴会前备好食材", "detail": "夏威夷宴会要带一份好菜放进汤锅。"},
        ],
        "season_goals": ["蓝莓收获 4 次", "钓鱼等级达到 8 级"],
    },
    "key_milestones": [
        {"day": 11, "event": "夏威夷宴会", "preparation": "准备一份高品质作物放进汤锅", "prepare_by_day": 10},
        {"day": 28, "event": "月光水母起舞", "preparation": "晚上去海滩", "prepare_by_day": 28},
        {"day": 28, "event": "换季日", "preparation": "收完蓝莓，玉米保留到秋季", "prepare_by_day": 27},
    ],
    "daily_goals": _goals(
        (1, 1, "种下蓝莓和玉米"),
        (2, 10, "海滩钓鱼攒钱"),
        (11, 11, "参加夏威夷宴会"),
        (12, 13, "下矿井推进到 60 层"),
        (14, 27, "收获蓝莓和玉米，出售作物"),
        (28, 28, "收获蓝莓；清理农场/停止种植（保留玉米）"),
    ),
    "plantings": [
        {"day": 1, "crop": "蓝莓", "tiles": 15},
        {"day": 1, "crop": "玉米", "tiles": 8},
    ],
    "other_income": [{"source": "钓鱼", "gold": 3000, "note": "海滩与河边钓鱼"}],
}

MOCK_FALL = {
    "macro_strategy": {
        "summary": "秋季继续收获夏季留下的玉米，新种蔓越莓和南瓜，为冬季攒钱。",
        "pillars": [
            {"title": "玉米不用重种", "detail": "夏季种下的玉米会继续按 4 天周期产出。"},
            {"title": "蔓越莓多次收获", "detail": "蔓越莓可再生且单次产量高。"},
            {"title": "展览会前准备展品", "detail": "星露谷展览会需要 9 类展品。"},
        ],
        "season_goals": ["南瓜收获一轮", "冬季前存款翻倍"],
    },
    "key_milestones": [
        {"day": 16, "event": "星露谷展览会", "preparation": "准备 9 类展品", "prepare_by_day": 15},
        {"day": 27, "event": "万灵节前夜", "preparation": "晚上去小镇广场", "prepare_by_day": 27},
        {"day": 28, "event": "换季日", "preparation": "收完所有作物", "prepare_by_day": 27},
    ],
    "daily_goals": _goals(
        (1, 1, "种下蔓越莓"),
        (2, 2, "种下南瓜，送潘妮甜瓜（夏季预留）"),
        (3, 12, "收获玉米和蔓越莓，钓鱼"),
        (13, 13, "送阿比盖尔紫水晶，钓鱼"),
        (14, 15, "收获玉米和蔓越莓，钓鱼"),
        (16, 16, "参加星露谷展览会"),
        (17, 27, "收获作物，出售换钱"),
        (28, 28, "收完作物；清理农场/停止种植"),
    ),
    "plantings": [
        {"day": 1, "crop": "蔓越莓", "tiles": 20},
        {"day": 2, "crop": "南瓜", "tiles": 10},
    ],
    "other_income": [{"source": "钓鱼", "gold": 2500, "note": "雨天钓鱼"}],
}

MOCK_WINTER = {
    "macro_strategy": {
        "summary": "冬季不能露天种植，专心钓鱼、挖矿和社交，为来年春季攒种子钱。",
        "pillars": [
            {"title": "矿井与骷髅洞穴", "detail": "冬季时间充裕，适合推进矿井。"},
            {"title": "冰钓", "detail": "冰雪节可以参加钓鱼比赛。"},
            {"title": "规划来年", "detail": "春季第一天就要买种子，提前留好资金。"},
        ],
        "season_goals": ["矿井推进到 120 层", "为春季留足种子钱"],
    },
    "key_milestones": [
        {"day": 8, "event": "冰雪节", "preparation": "带上鱼竿", "prepare_by_day": 7},
        {"day": 15, "event": "夜市", "preparation": "留出购物资金", "prepare_by_day": 14},
        {"day": 25, "event": "冬日星盛宴", "preparation": "准备礼物", "prepare_by_day": 24},
    ],
    "daily_goals": _goals(
        (1, 7, "下矿井推进"),
        (8, 8, "参加冰雪节"),
        (9, 9, "下矿井推进"),
        (10, 10, "送塞巴斯蒂安泪晶"),
        (11, 14, "下矿井推进"),
        (15, 17, "逛夜市，钓鱼"),
        (18, 22, "下矿井推进"),
        (23, 23, "送莉亚果酒"),
        (24, 24, "下矿井推进"),
        (25, 25, "参加冬日星盛宴"),
        (26, 27, "准备来年春季"),
        (28, 28, "整理仓库；清理农场/停止种植"),
    ),
    "plantings": [],
    "other_income": [{"source": "挖矿", "gold": 4000, "note": "出售矿石与宝石"}],
}

MOCK_RESPONSES = {"spring": MOCK_RESPONSE, "summer": MOCK_SUMMER, "fall": MOCK_FALL, "winter": MOCK_WINTER}
