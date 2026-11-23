"""导入《星露谷物语》1.6 主存档（XML），把进度映射成生成器的参数。

主存档是存档文件夹里和文件夹同名的那个文件（如 Name_123456789），不是 SaveGameInfo。
存档在睡觉时写入，里面的日期就是醒来的那一天，所以 dayOfMonth 直接作为季中重新规划的起始日。

安全：存档是用户上传的不可信文件。带 DOCTYPE / ENTITY 声明的 XML 一律拒绝（星露谷的存档没有这些），
这样既不会解析外部实体（XXE），也不会被“实体爆炸”拖垮；装了 defusedxml 时再用它解析一遍。

容错：每一部分单独解析，缺字段、模组物品、看不懂的值都只记进 warnings，不让整个导入失败。
物品 ID、职业 ID、农场编号见 data/save_ids.json（按 wiki 核对）。

地里的作物：农场、温室、姜岛农场的耕地（HoeDirt）上的 crop。还要几天成熟按生长阶段算：
    当前阶段还剩 = phaseDays[currentPhase] − dayOfCurrentPhase，再加上之后各阶段的天数（最后一个阶段是成熟）；
    已经在最后阶段：第一次成熟就是今天，可再生作物收过一次后（fullyGrown）还要 dayOfCurrentPhase 天。
成熟日 = 存档日期 + 还要几天。枯死的作物、野生作物（forageCrop）、巨型作物和花盆里的作物不导入。
"""

from __future__ import annotations

import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

from .data_loader import DEFAULT_DATA_DIR, GameData

try:  # 可选依赖：有就用（更严格），没有就用标准库 + 上面的 DOCTYPE 检查
    from defusedxml.ElementTree import fromstring as _safe_fromstring
except ImportError:  # pragma: no cover - 取决于环境
    _safe_fromstring = None

MAX_SAVE_BYTES = 64 * 1024 * 1024  # 命令行读取的上限；网页上传另有更小的上限
SEASONS = ("spring", "summer", "fall", "winter")
_DTD = re.compile(rb"<!\s*(DOCTYPE|ENTITY)", re.IGNORECASE)


class SaveParseError(ValueError):
    """整个文件无法作为星露谷主存档读取（不是 XML、是 SaveGameInfo、带 DOCTYPE 等）。"""


@dataclass(frozen=True)
class SaveIds:
    crops: dict          # 物品 ID → 作物英文名
    professions: dict    # 职业 ID → 本项目的职业 key
    farm_types: dict     # whichFarm → 本项目的农场 key
    machines: dict       # 存档里的物品名 → kegs / jars / bee_houses
    animal_keywords: dict  # type 里的关键词 → animals.json 里的英文名
    skip_animals: tuple
    greenhouse_mail: tuple
    joja_mail: str
    cc_complete_mail: str


def load_save_ids(path: Path | None = None) -> SaveIds:
    raw = json.loads((path or DEFAULT_DATA_DIR / "save_ids.json").read_text(encoding="utf-8"))
    return SaveIds({v: k for k, v in raw["crop_item_ids"].items()}, raw["professions"], raw["farm_types"],
                   raw["machines"], raw["animal_keywords"], tuple(raw["skip_animals"]), tuple(raw["greenhouse_mail"]),
                   raw["joja_mail"], raw["cc_complete_mail"])


@dataclass
class SaveState:
    """从存档里读出的、本项目用得上的进度。读不到的字段为 None（或空），不覆盖默认值。"""
    farmer: str = ""
    farm_name: str = ""
    year: int | None = None
    season: str | None = None
    start_day: int | None = None
    start_gold: int | None = None
    farm_type: str | None = None
    farming_level: int | None = None
    professions: list[str] = field(default_factory=list)
    greenhouse: bool = False
    route: str | None = None
    kegs: int = 0
    jars: int = 0
    bee_houses: int = 0
    animals: dict[str, int] = field(default_factory=dict)      # 已有的动物：中文名 → 只数（已成年，不计购买费）
    shipped: dict[str, int] = field(default_factory=dict)      # 已出货的作物：中文名 → 个数（basicShipped）
    friendship: dict[str, int] = field(default_factory=dict)   # 村民（中文名，没收录的用英文名）→ 好感度点数
    money_earned: int | None = None                           # 累计赚取的金币
    players: int = 1                                           # 存档里的玩家数（房主 + 已加入的农场帮手）
    in_ground: list[dict] = field(default_factory=list)        # 地里的作物：{"crop", "tiles", "ready_day", "place"}
    bundle_done: list[tuple[str, str]] = field(default_factory=list)  # 已交的收集包作物 (收集包英文名, 作物英文名)
    warnings: list[str] = field(default_factory=list)

    # ---- 映射成生成器参数 ----
    def progress(self):
        """成就进度（achievements.Progress）：已出货的作物、每位村民的好感度点数、累计赚取的金币。"""
        from .achievements import Progress
        return Progress(dict(self.shipped), {}, dict(self.friendship), self.money_earned or 0)

    def animals_text(self) -> str:
        return ",".join(f"{name}:{n}" for name, n in self.animals.items())

    def progress_text(self, rules) -> str:
        """网页表单用的成就进度文字：作物=已出货个数，友谊类成就名=已达到心数的人数，赚钱类成就名=累计金币。"""
        parts = [f"{name}={n}" for name, n in self.shipped.items() if n > 0]
        for a in rules.achievements:
            if a.kind == "friendship":
                people = sum(1 for p in self.friendship.values() if p >= a.hearts * a.points_per_heart)
                if people:
                    parts.append(f"{a.name}={people}")
        earnings = max((a for a in rules.achievements if a.kind == "earnings"), key=lambda a: a.target, default=None)
        if earnings and self.money_earned:
            parts.append(f"{earnings.name}={self.money_earned}")
        return ",".join(parts)

    def generator_kwargs(self) -> dict:
        """generate_strategy / generate_plan 的参数（只放读到了的）；季节放在 "season"。"""
        out: dict = {}
        for key in ("season", "year", "start_day", "start_gold", "farm_type", "farming_level", "route"):
            value = getattr(self, key)
            if value is not None:
                out[key] = value
        if self.professions:
            out["professions"] = list(self.professions)
        if self.greenhouse:
            out["greenhouse"] = True
        for key in ("kegs", "jars", "bee_houses"):
            if getattr(self, key):
                out[key] = getattr(self, key)
        if self.animals:
            out["owned_animals"] = dict(self.animals)
        if self.shipped or self.friendship or self.money_earned:
            out["achievement_progress"] = self.progress()
        if self.in_ground:
            out["in_ground"] = [dict(x) for x in self.in_ground]
        if self.bundle_done:
            out["bundle_done"] = [list(x) for x in self.bundle_done]
        return out

    def form_defaults(self, rules) -> dict:
        """网页表单的默认值（JSON），字段名和 /api/generate 的请求一致。"""
        out = {k: v for k, v in self.generator_kwargs().items() if k not in ("season", "owned_animals",
                                                                           "achievement_progress")}
        if self.season:
            out["seasons"] = [self.season]
        if self.animals:
            out["owned_animals"] = self.animals_text()
        text = self.progress_text(rules)
        if text:
            out["achievement_progress"] = text
        return out

    def summary_lines(self, data: GameData | None = None) -> list[str]:
        """终端和网页上的“存档解析摘要”。"""
        from .data_loader import SEASON_ZH
        lines = []
        who = "、".join(x for x in (self.farmer and f"农夫 {self.farmer}", self.farm_name and f"{self.farm_name}农场") if x)
        if who:
            lines.append(who)
        if self.season and self.year and self.start_day:
            lines.append(f"日期：第 {self.year} 年{SEASON_ZH[self.season]}第 {self.start_day} 天")
        if self.start_gold is not None:
            lines.append(f"现金：{self.start_gold:,}g" + (f"（累计赚取 {self.money_earned:,}g）" if self.money_earned else ""))
        if self.farm_type:
            name = data.farm_types.get(self.farm_type, {}).get("name_zh", self.farm_type) if data else self.farm_type
            lines.append(f"农场：{name}")
        if self.farming_level is not None:
            prof = {"tiller": "农耕人", "artisan": "工匠", "agriculturist": "农业学家"}
            lines.append(f"耕种等级 {self.farming_level}" + (f"，职业：{'、'.join(prof[p] for p in self.professions)}"
                                                         if self.professions else ""))
        facilities = [x for x in (self.greenhouse and "温室已修好",
                                  self.route == "community_center" and "社区中心路线",
                                  self.kegs and f"小桶 {self.kegs} 个", self.jars and f"罐头瓶 {self.jars} 个",
                                  self.bee_houses and f"室外蜂房 {self.bee_houses} 个") if x]
        if facilities:
            lines.append("设施：" + "、".join(facilities))
        if self.animals:
            lines.append("动物：" + "、".join(f"{n} {c} 只" for n, c in self.animals.items()))
        if self.in_ground:
            where = {"field": "", "greenhouse": "温室，", "island": "姜岛，"}
            lines.append("地里的作物：" + "、".join(
                f"{x['crop']} {x['tiles']} 格（{where[x['place']]}"
                + (f"第 {x['ready_day']} 天成熟）" if x["ready_day"] > (self.start_day or 1) else "已成熟）")
                for x in self.in_ground))
        if self.bundle_done:
            lines.append(f"社区中心已交的收集包作物：{len(self.bundle_done)} 项（献祭路线不再要求预留）")
        if self.players > 1:
            lines.append(f"联机存档：{self.players} 位玩家（只读取了房主的进度）")
        if self.shipped:
            lines.append(f"已出货的作物：{len(self.shipped)} 种")
        if self.friendship:
            five = sum(1 for p in self.friendship.values() if p >= 1250)
            lines.append(f"好感度：{len(self.friendship)} 位村民，其中 {five} 位达到 5 颗心")
        return lines


# ---------------------------------------------------------------------------
# 解析
# ---------------------------------------------------------------------------

def _int(text) -> int | None:
    try:
        return int(str(text).strip())
    except (TypeError, ValueError):
        return None


def _safe_root(raw: bytes) -> ET.Element:
    """安全解析 XML：拒绝 DOCTYPE / ENTITY，有 defusedxml 时用它。"""
    if _DTD.search(raw):
        raise SaveParseError("存档里不能有 DOCTYPE / ENTITY 声明（星露谷的存档不会有），为了安全拒绝解析")
    try:
        return _safe_fromstring(raw) if _safe_fromstring else ET.fromstring(raw)
    except ET.ParseError as e:
        raise SaveParseError(f"不是有效的 XML 文件：{e}") from None
    except Exception as e:  # defusedxml 拒绝时抛的异常类型不同
        raise SaveParseError(f"存档解析失败：{type(e).__name__}: {e}") from None


def _parse_xml(raw: bytes) -> ET.Element:
    root = _safe_root(raw)
    if root.tag == "Farmer":
        raise SaveParseError("这是 SaveGameInfo（只有存档列表用的摘要）；请选文件夹里和文件夹同名的主存档文件")
    if root.tag != "SaveGame":
        raise SaveParseError(f"不是星露谷的主存档（根元素是 {root.tag}，应为 SaveGame）")
    return root


def _section(state: SaveState, label: str):
    """包住一段解析：出错只记一条提示。"""
    class _Guard:
        def __enter__(self):
            return self

        def __exit__(self, kind, exc, tb):
            if exc is not None and not isinstance(exc, SaveParseError):
                state.warnings.append(f"{label}读取失败，已跳过（{type(exc).__name__}: {exc}）")
                return True
            return False
    return _Guard()


def _dict_items(parent: ET.Element | None):
    """XML 序列化的字典：<item><key>…</key><value>…</value></item>。返回 (key 元素, value 元素)。"""
    if parent is None:
        return
    for item in parent.findall("item"):
        key, value = item.find("key"), item.find("value")
        if key is not None and value is not None:
            yield key, value


def _key_text(key: ET.Element) -> str:
    child = next(iter(key), None)
    return (child.text if child is not None else key.text or "").strip()


def _farm_location(root: ET.Element) -> ET.Element | None:
    for loc in root.findall("locations/GameLocation"):
        if (loc.findtext("name") or "").strip() == "Farm":
            return loc
    return None


def parse_save(source: bytes | str | Path, data: GameData | None = None, ids: SaveIds | None = None) -> SaveState:
    """解析主存档。source 是文件路径或文件内容（bytes）。整体读不了时抛 SaveParseError，局部问题记进 warnings。"""
    if isinstance(source, (str, Path)):
        path = Path(source)
        if not path.is_file():
            raise SaveParseError(f"找不到存档文件：{path}")
        if path.stat().st_size > MAX_SAVE_BYTES:
            raise SaveParseError(f"存档文件超过 {MAX_SAVE_BYTES // 1024 // 1024}MB，不像星露谷的存档")
        raw = path.read_bytes()
    else:
        raw = source
    root = _parse_xml(raw)
    if data is None:
        from .data_loader import load_game_data
        data = load_game_data()
    ids = ids or load_save_ids()
    state = SaveState()
    player = root.find("player")
    if player is None:
        state.warnings.append("存档里没有 player（房主）信息，现金、等级、职业、出货和好感度都没有读到")
        player = ET.Element("player")

    with _section(state, "日期"):
        season = (root.findtext("currentSeason") or "").strip().lower()
        if season in SEASONS:
            state.season = season
        elif season:
            state.warnings.append(f"看不懂的季节 {season!r}，没有导入季节")
        day, year = _int(root.findtext("dayOfMonth")), _int(root.findtext("year"))
        if day is not None and 1 <= day <= 28:
            state.start_day = day
        elif day is not None:
            state.warnings.append(f"日期 {day} 不在 1-28 之间，没有导入")
        if year is not None and year >= 1:
            state.year = year

    with _section(state, "农场地图"):
        which = (root.findtext("whichFarm") or "").strip()
        mod_farm = (root.findtext("whichModFarm") or "").strip()
        if "meadowlands" in f"{which} {mod_farm}".lower():
            state.farm_type = "meadowlands"
        elif which in ids.farm_types:
            state.farm_type = ids.farm_types[which]
        elif which:
            state.warnings.append(f"农场地图 {which}{'（' + mod_farm + '）' if mod_farm else ''} 不是原版的 8 种，按标准农场计算")

    with _section(state, "基本信息"):
        state.farmer = (player.findtext("name") or "").strip()
        state.farm_name = (player.findtext("farmName") or "").strip()
        money = _int(player.findtext("money"))
        if money is not None and money >= 0:
            state.start_gold = money
        level = _int(player.findtext("farmingLevel"))
        if level is not None:
            state.farming_level = max(0, min(level, 14))
        state.players = 1 + sum(1 for f in root.findall("farmhands/Farmer") if (f.findtext("name") or "").strip())

    with _section(state, "职业"):
        for el in player.findall("professions/int"):
            key = ids.professions.get((el.text or "").strip())
            if key and key not in state.professions:
                state.professions.append(key)

    with _section(state, "邮件标记（温室、路线）"):
        mail = {(el.text or "").strip() for el in player.findall("mailReceived/string")}
        state.greenhouse = any(m in mail for m in ids.greenhouse_mail)
        if ids.joja_mail in mail:
            state.warnings.append("存档走的是 Joja 路线：本项目没有 Joja 路线的献祭规划，不导入路线")
        elif ids.cc_complete_mail in mail or "ccPantry" in mail:
            pass  # 社区中心已经完成（至少食品储藏室的作物收集包都交了），不需要再预留作物
        else:
            state.route = "community_center"
            state.warnings.append("社区中心还没修好，已选社区中心献祭路线（已经交过的收集包作物会导入，不再要求预留）")

    with _section(state, "累计赚取的金币"):
        earned = _int(player.findtext("totalMoneyEarned"))
        if earned is None:
            for key, value in _dict_items(player.find("stats/Values")):
                if _key_text(key) in ("totalMoneyEarned", "totalMoneyGEarned"):
                    earned = _int(_key_text(value))
        if earned is not None and earned >= 0:
            state.money_earned = earned

    crops_by_en = {c.name_en: c for c in data.crops}
    with _section(state, "已出货的作物"):
        modded = 0
        for key, value in _dict_items(player.find("basicShipped")):
            item_id = _key_text(key).removeprefix("(O)")
            count = _int(_key_text(value))
            if not item_id.isdigit():
                modded += 1
                continue
            name_en = ids.crops.get(item_id)
            if name_en in crops_by_en and count:
                name = crops_by_en[name_en].name
                state.shipped[name] = state.shipped.get(name, 0) + count
        if modded:
            state.warnings.append(f"出货记录里有 {modded} 种不是原版数字 ID 的物品（模组或 1.6 新物品），已跳过")

    npc_zh = {n.name_en: n.name for n in data.npc_data.npcs}
    with _section(state, "好感度"):
        for key, value in _dict_items(player.find("friendshipData")):
            name = _key_text(key)
            points = _int(value.findtext("Friendship/Points"))
            if name and points is not None and points > 0:
                state.friendship[npc_zh.get(name, name)] = points

    farm = _farm_location(root)
    if farm is None:
        state.warnings.append("存档里没有找到农场（Farm）这个地点，机器和动物都没有读到")
        return state

    with _section(state, "机器"):
        counts = {"kegs": 0, "jars": 0, "bee_houses": 0}
        for objects in farm.iter("objects"):
            outdoors = objects is farm.find("objects")
            for obj in objects.findall("item/value/Object"):
                key = ids.machines.get((obj.findtext("name") or "").strip())
                if key == "bee_houses" and not outdoors:
                    continue  # 蜂房放在室内不产蜜
                if key:
                    counts[key] += 1
        state.kegs, state.jars, state.bee_houses = counts["kegs"], counts["jars"], counts["bee_houses"]

    animals_by_en = {a.name_en: a.name for a in data.animals}
    with _section(state, "动物"):
        skipped: dict[str, int] = {}
        for animal in farm.iter("FarmAnimal"):
            kind = (animal.findtext("type") or "").strip()
            en = next((v for k, v in ids.animal_keywords.items() if k in kind), None)
            if kind in ids.skip_animals or en not in animals_by_en:
                skipped[kind or "未知"] = skipped.get(kind or "未知", 0) + 1
                continue
            name = animals_by_en[en]
            state.animals[name] = state.animals.get(name, 0) + 1
        if skipped:
            listed = "、".join(f"{k} ×{n}" for k, n in skipped.items())
            state.warnings.append(f"这些动物不在 data/animals.json 里（或产品不同），没有计入：{listed}")

    with _section(state, "地里的作物"):
        _parse_crops(root, state, ids, crops_by_en)
    with _section(state, "社区中心收集包"):
        _parse_bundles(root, state, ids, crops_by_en, data)
    return state


def _parse_bundles(root: ET.Element, state: SaveState, ids: SaveIds, crops_by_en: dict, data: GameData) -> None:
    """已交的收集包作物。

    bundleData：“房间/编号” → “名字/奖励/物品 ID 数量 品质 …/颜色/需要几件//中文名”（需要几件为空 = 全部）；
    社区中心的 bundles：编号 → 布尔数组，第 j 个表示第 j 件物品交了没有（交满后整组都是 true）。
    交满的收集包里所有作物都算交过（“4 选 3”这类收集包剩下的也不用再留）。按英文名对应 data/bundles.json
    的“名字 + Bundle”；对不上的（1.6 的混合版收集包）记一条提示。
    """
    definitions = {}
    for key, value in _dict_items(root.find("bundleData")):
        index = _int(_key_text(key).rsplit("/", 1)[-1])
        if index is not None:
            definitions[index] = _key_text(value).split("/")
    cc = next((loc for loc in root.findall("locations/GameLocation")
               if (loc.findtext("name") or "").strip() == "CommunityCenter"), None)
    flags = {}
    for key, value in _dict_items(cc.find("bundles") if cc is not None else None):
        index = _int(_key_text(key))
        array = next(iter(value), None)
        if index is not None and array is not None:
            flags[index] = [(el.text or "").strip().lower() == "true" for el in array]
    known = {b.name_en: b for b in data.bundles}
    done: list[tuple[str, str]] = []
    unknown_names = []
    for index, fields_ in sorted(definitions.items()):
        if len(fields_) < 3:
            continue
        tokens = fields_[2].split()
        items = [tokens[i:i + 3] for i in range(0, len(tokens) - 2, 3)]
        crops = [crops_by_en.get(ids.crops.get(item[0].removeprefix("(O)"))) for item in items]
        if not any(crops):
            continue  # 不涉及作物的收集包（鱼、矿石……）和规划无关
        bundle = known.get(f"{fields_[0]} Bundle")
        if bundle is None:
            unknown_names.append(fields_[-1] or fields_[0])
            continue
        got = flags.get(index, [])
        need = _int(fields_[4]) if len(fields_) > 4 and fields_[4].strip() else len(items)
        complete = sum(got[:len(items)]) >= need
        for j, crop in enumerate(crops):
            if crop is not None and (complete or (j < len(got) and got[j])):
                done.append((bundle.name_en, crop.name_en))
    state.bundle_done = list(dict.fromkeys(done))
    if unknown_names:
        state.warnings.append(f"这些收集包不是标准版（可能是 1.6 的混合版收集包），没有导入：{'、'.join(unknown_names)}")


CROP_LOCATIONS = {"Farm": "field", "Greenhouse": "greenhouse", "IslandWest": "island"}


def days_to_harvest(crop: ET.Element) -> int | None:
    """按生长阶段算还要几天可以收获（0 = 今天就能收）；字段不全时返回 None。"""
    phases = [_int(el.text) for el in crop.findall("phaseDays/int")]
    phase, day = _int(crop.findtext("currentPhase")), _int(crop.findtext("dayOfCurrentPhase"))
    if not phases or None in phases or phase is None or day is None:
        return None
    last = len(phases) - 1  # 最后一个阶段是成熟（天数是一个很大的占位数）
    if phase >= last:
        grown = (crop.findtext("fullyGrown") or "").strip().lower() == "true"
        return max(0, day) if grown else 0
    return max(0, phases[phase] - day) + sum(phases[phase + 1:last])


def _parse_crops(root: ET.Element, state: SaveState, ids: SaveIds, crops_by_en: dict) -> None:
    today = state.start_day or 1
    groups: dict[tuple[str, int, str], int] = {}
    unknown = unreadable = 0
    for loc in root.findall("locations/GameLocation"):
        place = CROP_LOCATIONS.get((loc.findtext("name") or "").strip())
        if place is None:
            continue
        for _, value in _dict_items(loc.find("terrainFeatures")):
            crop = value.find("TerrainFeature/crop")
            if crop is None:
                continue
            if any((crop.findtext(f) or "").strip().lower() == "true" for f in ("dead", "forageCrop")):
                continue
            harvest = (crop.findtext("indexOfHarvest") or "").strip().removeprefix("(O)")
            name_en = ids.crops.get(harvest)
            if name_en not in crops_by_en:
                unknown += 1
                continue
            left = days_to_harvest(crop)
            if left is None:
                unreadable += 1
                continue
            key = (crops_by_en[name_en].name, today + left, place)
            groups[key] = groups.get(key, 0) + 1
    state.in_ground = [{"crop": name, "tiles": n, "ready_day": ready, "place": place}
                       for (name, ready, place), n in sorted(groups.items(), key=lambda x: (x[0][1], x[0][0]))]
    if unknown:
        state.warnings.append(f"地里有 {unknown} 格作物不在 data/crops.json 里（模组或 1.6 新作物），没有计入收入")
    if unreadable:
        state.warnings.append(f"地里有 {unreadable} 格作物读不出生长阶段，没有计入收入")


# ---------------------------------------------------------------------------
# 本机存档查找
# ---------------------------------------------------------------------------

MAX_INFO_BYTES = 4 * 1024 * 1024   # SaveGameInfo 通常只有几 KB
SAVE_DIR_HINTS = {
    "Windows": r"%APPDATA%\StardewValley\Saves",
    "macOS": "~/.config/StardewValley/Saves",
    "Linux": "~/.config/StardewValley/Saves",
    "Steam Deck / Flatpak": "~/.var/app/com.valvesoftware.Steam/.config/StardewValley/Saves",
}


def get_default_save_dirs() -> list[Path]:
    """本机星露谷的默认存档根目录（按操作系统；只返回路径，不检查是否存在）。"""
    if sys.platform.startswith("win"):
        appdata = os.environ.get("APPDATA", "")
        return [Path(appdata) / "StardewValley" / "Saves"] if appdata else []
    home = Path.home()
    dirs = [home / ".config" / "StardewValley" / "Saves"]
    if sys.platform.startswith("linux"):
        dirs.append(home / ".var" / "app" / "com.valvesoftware.Steam" / ".config" / "StardewValley" / "Saves")
    return dirs


@dataclass(frozen=True)
class SaveSummary:
    """存档列表里的一项：来自 SaveGameInfo（读不了时只有文件夹名）。"""
    folder: str                 # 存档文件夹名，也是主存档的文件名（如 Name_123456789）
    path: Path                  # 主存档文件
    modified: float             # 主存档的最后修改时间（时间戳）
    farmer: str = ""
    farm_name: str = ""
    year: int | None = None
    season: str | None = None
    day: int | None = None
    money: int | None = None
    info_ok: bool = False       # SaveGameInfo 读到了

    @property
    def label(self) -> str:
        """下拉框和列表里显示的文字，如“星露农场 - 第1年春季第15天 (3,200g)”。"""
        from .data_loader import SEASON_ZH
        if not self.info_ok:
            return f"{self.folder}（没有读到存档摘要）"
        name = f"{self.farm_name}农场" if self.farm_name else self.folder
        when = (f"第{self.year}年{SEASON_ZH[self.season]}第{self.day}天"
                if self.year and self.season and self.day else "日期未知")
        gold = f" ({self.money:,}g)" if self.money is not None else ""
        who = f"，{self.farmer}" if self.farmer else ""
        return f"{name} - {when}{gold}{who}"

    def as_dict(self) -> dict:
        return {"folder": self.folder, "label": self.label, "farmer": self.farmer, "farm_name": self.farm_name,
                "year": self.year, "season": self.season, "day": self.day, "money": self.money,
                "modified": self.modified, "info_ok": self.info_ok}


def _parse_save_info(path: Path) -> dict | None:
    """读 SaveGameInfo（根元素是 Farmer）；文件缺失、过大或损坏时返回 None。"""
    try:
        if not path.is_file() or path.stat().st_size > MAX_INFO_BYTES:
            return None
        root = _safe_root(path.read_bytes())
    except (OSError, SaveParseError):
        return None
    if root.tag != "Farmer":
        return None
    season = (root.findtext("seasonForSaveGame") or "").strip().lower()
    if season.isdigit():
        season = SEASONS[int(season)] if int(season) < len(SEASONS) else ""
    day = _int(root.findtext("dayOfMonthForSaveGame"))
    return {
        "farmer": (root.findtext("name") or "").strip(),
        "farm_name": (root.findtext("farmName") or "").strip(),
        "year": _int(root.findtext("yearForSaveGame")),
        "season": season if season in SEASONS else None,
        "day": day if day is not None and 1 <= day <= 28 else None,
        "money": _int(root.findtext("money")),
    }


def discover_local_saves(custom_dirs=None) -> list[SaveSummary]:
    """扫描存档根目录（默认 get_default_save_dirs()），找出有主存档（和文件夹同名的文件）的存档文件夹。

    按主存档的最后修改时间倒序（最近玩的在前）。SaveGameInfo 缺失或损坏时只显示文件夹名；
    读不了的目录直接跳过，不报错。
    """
    out: list[SaveSummary] = []
    seen: set[Path] = set()
    for root in (custom_dirs if custom_dirs is not None else get_default_save_dirs()):
        root = Path(root)
        try:
            folders = [p for p in root.iterdir() if p.is_dir()]
        except OSError:
            continue
        for folder in folders:
            main = folder / folder.name
            try:
                if not main.is_file() or main.resolve() in seen:
                    continue
                seen.add(main.resolve())
                modified = main.stat().st_mtime
            except OSError:
                continue
            info = _parse_save_info(folder / "SaveGameInfo")
            out.append(SaveSummary(folder.name, main, modified, info_ok=info is not None, **(info or {})))
    return sorted(out, key=lambda s: s.modified, reverse=True)


def find_local_save(folder: str, custom_dirs=None) -> SaveSummary:
    """按文件夹名找本机存档（只认 discover_local_saves 扫描到的，防止目录穿越）。"""
    name = str(folder or "").strip()
    if not name or name in (".", "..") or any(ch in name for ch in "/\\:\0"):
        raise SaveParseError(f"存档文件夹名不合法：{folder!r}")
    for s in discover_local_saves(custom_dirs):
        if s.folder == name:
            return s
    raise SaveParseError(f"本机没有找到存档：{name}")
