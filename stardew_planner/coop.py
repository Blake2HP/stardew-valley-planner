"""联机模式（2-4 人）：分工、按玩家的日程、联机规则（wiki Multiplayer）。

- 所有玩家共用农田和钱包（按共享钱包计算），每人有自己的体力（各 270）、技能和工具；
- 打开菜单、钓鱼小游戏时时间不会暂停，日程要少排极限卡点，钓鱼收入保守估计；
- 联机时模型输出的 daily_goals 每天是一个对象 {"P1": "...", "P2": "..."}，P1 是房主；
- 程序内部把每天的对象合成一个字符串（"P1：…；P2：…"）交给原有的全农场校验（播种、换季、送礼、献祭……），
  工具冲突和 Markdown 渲染则按玩家处理；
- 开局资金和种子只算房主的一份（农场工小屋里的防风草种子 wiki 没写数量，不计）。
"""

from __future__ import annotations

import re

MAX_PLAYERS = 4
PREFIX = "⚠️ 联机校验："
SAME_DAYS_LIMIT = 14  # 超过这么多天所有玩家目标完全相同，算没有分工

# 常见分工 → 日程里能看出在做这件事的关键词；其他分工直接用分工文字本身匹配
ROLE_WORDS = {
    "种田": ("种", "收获", "浇水", "播种", "补种"),
    "种地": ("种", "收获", "浇水", "播种", "补种"),
    "下矿": ("矿",),
    "挖矿": ("矿",),
    "钓鱼": ("钓",),
    "社交": ("送", "聊", "好感"),
    "养殖": ("动物", "喂", "鸡", "牛", "羊", "猪"),
    "采集": ("采集", "摘"),
    "加工": ("小桶", "罐头", "酿", "加工"),
}


def player_ids(players: int) -> list[str]:
    return [f"P{i}" for i in range(1, players + 1)]


def check_players(players: int) -> int:
    try:
        n = int(players)
    except (TypeError, ValueError):
        raise ValueError(f"玩家人数必须是 1-{MAX_PLAYERS} 的整数，收到 {players!r}") from None
    if not 1 <= n <= MAX_PLAYERS:
        raise ValueError(f"玩家人数必须是 1-{MAX_PLAYERS}，收到 {players}")
    return n


def parse_roles(spec, players: int) -> dict[str, str]:
    """“P1:种田, P2:下矿”或 {"P1": "种田"} → {"P1": "种田", ...}。写错或玩家编号超出人数时报 ValueError。"""
    if not spec:
        return {}
    if isinstance(spec, str):
        pairs = []
        for part in re.split(r"[,，;；]", spec):
            part = part.strip()
            if not part:
                continue
            m = re.fullmatch(r"([Pp]\d)\s*[:：]\s*(.+)", part)
            if not m:
                raise ValueError(f"分工写法应为“P1:种田, P2:下矿”，收到：{part}")
            pairs.append((m.group(1), m.group(2)))
    else:
        pairs = list(dict(spec).items())
    valid = player_ids(players)
    roles: dict[str, str] = {}
    for pid, role in pairs:
        pid = str(pid).strip().upper()
        role = str(role).strip()
        if pid not in valid:
            raise ValueError(f"分工里的 {pid} 超出了玩家人数（{players} 人：{'、'.join(valid)}）")
        if pid in roles:
            raise ValueError(f"分工里 {pid} 写了两次")
        if not role:
            raise ValueError(f"{pid} 的分工是空的")
        roles[pid] = role
    return roles


def _clean(text: str) -> str:
    text = re.sub(r"\s+", " ", str(text).strip())
    return text.rstrip("。.!！;；，,")


def merge_day(goals: dict[str, str]) -> str:
    """{"P1": "种防风草", "P2": "钓鱼"} → "P1：种防风草；P2：钓鱼"（每个玩家先规范化，方便合并连续日程）。"""
    return "；".join(f"{pid}：{_clean(goals[pid])}" for pid in sorted(goals, key=lambda p: int(p[1:])))


def parse_player_goals(raw: dict, players: int, errors: list[str] | None) -> dict[int, dict[str, str]]:
    """校验联机的 daily_goals：每天必须是 {"P1": ..., "P2": ...}，每个玩家都要有非空字符串。

    errors 为 None 时是兜底模式：缺的玩家填“（无安排）”，字符串当作 P1 的目标。
    """
    ids = player_ids(players)
    out: dict[int, dict[str, str]] = {}
    for k, v in raw.items():
        try:
            day = int(k)
        except (TypeError, ValueError):
            continue
        if not 1 <= day <= 28:
            continue
        if isinstance(v, str):
            if errors is not None:
                errors.append(f"联机模式下 daily_goals[{day}] 要写成按玩家的对象，例如 "
                              f"{{\"P1\": \"种防风草\", \"P2\": \"钓鱼\"}}，收到字符串：{v[:30]!r}")
                continue
            v = {"P1": v}
        if not isinstance(v, dict):
            if errors is not None:
                errors.append(f"daily_goals[{day}] 必须是对象：{v!r}")
            continue
        day_goals: dict[str, str] = {}
        for pid in ids:
            text = v.get(pid, v.get(pid.lower()))
            if isinstance(text, list):
                text = "，".join(str(t) for t in text)
            if isinstance(text, str) and text.strip():
                day_goals[pid] = text.strip()
            elif errors is not None:
                errors.append(f"daily_goals[{day}] 缺少 {pid} 的目标")
            else:
                day_goals[pid] = "（无安排）"
        extra = [p for p in v if str(p).upper() not in ids]
        if extra and errors is not None:
            errors.append(f"daily_goals[{day}] 有多余的玩家 {extra}（本次联机 {players} 人：{'、'.join(ids)}）")
        if len(day_goals) == len(ids):
            out[day] = day_goals
    return out


def check_division(player_goals: dict[int, dict[str, str]], roles: dict[str, str]) -> list[str]:
    """分工校验：太多天所有人干同一件事；给了分工的玩家整季都没做自己的分工。"""
    problems = []
    same = [d for d, g in sorted(player_goals.items()) if len({_clean(t) for t in g.values()}) == 1]
    if len(same) > SAME_DAYS_LIMIT:
        problems.append(f"{PREFIX}有 {len(same)} 天所有玩家的目标完全相同，没有分工；请按分工给每个人安排不同的事")
    for pid, role in roles.items():
        words = ROLE_WORDS.get(role, (role,))
        if not any(any(w in g.get(pid, "") for w in words) for g in player_goals.values()):
            problems.append(f"{PREFIX}{pid} 的分工是“{role}”，但整季的日程里 {pid} 没有安排这类事情")
    return problems


def describe_coop(players: int, roles: dict[str, str]) -> str:
    """【玩家画像】里的联机说明。"""
    if players <= 1:
        return "单人"
    division = "；".join(f"{p}：{r}" for p, r in roles.items()) if roles else "未指定，请自己安排互补的分工"
    return f"{players} 人联机（P1 为房主；分工：{division}）"


def coop_system_rules(players: int) -> str:
    """多人时追加在系统 Prompt 末尾的规则。"""
    if players <= 1:
        return ""
    ids = player_ids(players)
    example = ", ".join(f'"{p}": "{t}"' for p, t in zip(ids, ("种下防风草，清理农场", "海滩钓鱼", "下矿井推进", "采集大葱")))
    return f"""

## 联机模式（{players} 人：{'、'.join(ids)}）
16. 这是 {players} 人联机：所有人共用农田和钱包，每人有自己的体力（各 270）、技能和工具，P1 是房主。daily_goals 每天的值改为对象，键为 {'、'.join(ids)}，值为该玩家当天的目标（第 6 条对每个玩家分别适用，连续多天做同一件事时措辞要完全相同），例如 {{"1": {{{example}}}}}。
17. 必须按【玩家画像】里的分工明确分配任务，避免所有人干同一件事；没有指定分工时自己安排互补的分工。播种、送礼、第 28 天清理等全农场的事写在负责的那个玩家的目标里即可。
18. 联机时打开菜单、钓鱼小游戏时间不会暂停：请适度减少极限卡点的日程安排，钓鱼的单日期望收益要保守估计（other_income 里写低一些）。
19. tool_upgrades 每条都要写 player（如 "P1"）：工具是个人的，某个玩家的工具在铁匠铺时，只有他不能做要用这件工具的事，其他人照常；每个人升级工具都要单独付钱。"""
