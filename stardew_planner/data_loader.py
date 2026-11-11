"""数据加载模块。

两类数据：
1. 结构化数值（JSON）：作物、加工倍率、季节日历。
2. 非结构化攻略文本（Markdown）：按 `## 标题` 切块，每块可带一行 `标签: a, b`。
   切块存入 ChromaDB（持久化在 data/.chroma_db），用语义向量检索；
   chromadb 不可用（未安装、Embedding 模型下载失败等）时退回关键词检索。
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DEFAULT_GUIDE_DIR = DEFAULT_DATA_DIR / "guides"
CHROMA_DIR = DEFAULT_DATA_DIR / ".chroma_db"
COLLECTION_NAME = "stardew_guides"

SEASON_ZH = {"spring": "春季", "summer": "夏季", "fall": "秋季", "winter": "冬季"}
_SEASON_ALIASES = {
    **{k: k for k in SEASON_ZH},
    **{v: k for k, v in SEASON_ZH.items()},
    "春": "spring", "夏": "summer", "秋": "fall", "冬": "winter", "autumn": "fall",
}


def normalize_season(season: str) -> str:
    key = _SEASON_ALIASES.get(season.strip().lower())
    if key is None:
        raise ValueError(f"未知季节: {season!r}，可选: spring/summer/fall/winter 或 春季/夏季/秋季/冬季")
    return key


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Byproduct:
    """收获时附带的副产物（如小麦的干草）。"""
    name: str
    name_en: str
    chance: float          # 每次收获掉落 1 个的几率
    sell_price: int = 0
    note: str = ""


@dataclass(frozen=True)
class Crop:
    name: str
    name_en: str
    seasons: tuple[str, ...]
    seed_price: int
    sell_price: int
    grow_days: int
    regrow_days: int | None
    category: str  # fruit / vegetable / flower / special
    harvest_yield: float = 1.0
    seed_source: str = "皮埃尔杂货店"
    earliest_available_day: int = 1  # 当季最早能买到种子的日期（如草莓 = 13，复活节）
    available_year: int = 1  # 种子从第几年开始出售（如大蒜、红叶卷心菜 = 2）
    # 加速后的生长天数，键为总加速百分比（"10"/"20"/"25"/"33"/"35"/"43"），见 bonuses.py
    speed_grow_days: dict = field(default_factory=dict, compare=False, hash=False)
    seed_drop: float = 0.0  # 每次收获平均掉落的种子数（向日葵 = 1），补种时先用掉落的种子
    requires_bus: bool = False  # 种子只在沙漠绿洲出售，要先修好巴士
    aliases: tuple[str, ...] = field(default=(), compare=False, hash=False)  # 其他叫法，如官方名“西红柿”
    byproducts: tuple[Byproduct, ...] = field(default=(), compare=False, hash=False)
    note: str = field(default="", compare=False, hash=False)  # 表格列放不下的说明（如未碾米的灌溉与产量）

    @property
    def names(self) -> tuple[str, ...]:
        """本项目的名字 + 别名，长的在前（匹配时“虞美人花”优先于“虞美人”）。"""
        return tuple(sorted((self.name, *self.aliases), key=len, reverse=True))


@dataclass(frozen=True)
class ArtisanRule:
    machine: str
    product: str
    input_category: str
    multiplier: float
    bonus: int
    fixed_price: int | None
    processing_minutes: int
    processing_days: float
    input_crops: tuple[str, ...] = ()  # 非空时只适用于这些作物（name_en），如小麦 → 啤酒
    product_en: str = ""               # 加工品英文名，送礼校验用（如 Wine）
    input_count: int = 1               # 做 1 件加工品要几个原料（咖啡 = 5 颗咖啡豆）

    def applies_to(self, crop: Crop) -> bool:
        return crop.name_en in self.input_crops if self.input_crops else crop.category == self.input_category

    def price_for(self, crop: Crop) -> int:
        if self.fixed_price is not None:
            return self.fixed_price
        return int(crop.sell_price * self.multiplier + self.bonus)


@dataclass(frozen=True)
class CalendarEvent:
    day: int
    name: str
    type: str  # festival / event / forage / unlock / season，见 data/calendar.json 的 _note
    tip: str
    name_en: str = ""  # 中文名为本项目译名时附上英文名


@dataclass(frozen=True)
class GuideChunk:
    source: str
    title: str
    text: str
    tags: tuple[str, ...]

    @property
    def document(self) -> str:
        """存入向量库并参与 Embedding 的文本：标题 + 标签 + 正文。"""
        tag_line = f"\n标签：{'、'.join(self.tags)}" if self.tags else ""
        return f"{self.title}{tag_line}\n{self.text}"

    @property
    def doc_id(self) -> str:
        """文本的 MD5，作为 Document ID：内容不变就不会重复写入。"""
        return hashlib.md5(self.document.encode("utf-8")).hexdigest()

    def to_prompt(self) -> str:
        return f"### {self.title}（来源：{self.source}）\n{self.text}"


@dataclass(frozen=True)
class GiftItem:
    name: str                       # 中文名（官方 wiki 中文版）
    name_en: str
    aliases: tuple[str, ...] = ()   # 本项目其他数据里的叫法，如 crops.json 的“虞美人”

    @property
    def names(self) -> tuple[str, ...]:
        return (self.name, *self.aliases)


@dataclass(frozen=True)
class NPC:
    name: str
    name_en: str
    birthday_season: str
    birthday_day: int
    loves: tuple[GiftItem, ...] = ()
    likes: tuple[GiftItem, ...] = ()
    universal_love_exceptions: tuple[str, ...] = ()  # name_en，例如海莉讨厌五彩碎片
    universal_likes_note: str = ""
    dislikes: tuple[GiftItem, ...] = ()
    hates: tuple[GiftItem, ...] = ()
    # wiki 表格上方的类别说明，键为 loves / likes / dislikes / hates，如 "All Vegetables (except Hops, Tea Leaves, & Wheat)"
    gift_notes: dict = field(default_factory=dict, compare=False, hash=False)
    marriage: bool = False  # 可以结婚的村民：生日当天要有送礼任务


@dataclass(frozen=True)
class GiftGroup:
    """通用喜欢 / 中立 / 不喜欢 / 讨厌：wiki 的类别说明（不逐项展开）和逐项列出的物品。"""
    categories: tuple[str, ...] = ()
    items: tuple[GiftItem, ...] = ()


@dataclass(frozen=True)
class NPCData:
    npcs: tuple[NPC, ...] = ()
    universal_loves: tuple[GiftItem, ...] = ()
    universal_likes: GiftGroup = GiftGroup()
    universal_neutrals: GiftGroup = GiftGroup()
    universal_dislikes: GiftGroup = GiftGroup()
    universal_hates: GiftGroup = GiftGroup()

    def find(self, name: str) -> NPC | None:
        key = name.strip().lower()
        return next((n for n in self.npcs if key in (n.name, n.name_en.lower())), None)

    def loved_items(self, npc: NPC) -> tuple[GiftItem, ...]:
        """专属最爱 + 通用最爱（去掉该 NPC 的例外）。"""
        universal = tuple(i for i in self.universal_loves if i.name_en not in npc.universal_love_exceptions)
        return npc.loves + universal

    def hated_universal(self, npc: NPC) -> tuple[GiftItem, ...]:
        return tuple(i for i in self.universal_loves if i.name_en in npc.universal_love_exceptions)

    def birthdays_in_season(self, season: str) -> list[NPC]:
        season = normalize_season(season)
        return sorted((n for n in self.npcs if n.birthday_season == season), key=lambda n: n.birthday_day)


@dataclass(frozen=True)
class HoneyRule:
    """蜂房附近有成熟的花时产花蜜：售价 = base_price + 花的基础售价 × flower_multiplier（data/artisan.json）。"""
    base_price: int
    flower_multiplier: float
    production_minutes: int
    production_days: int   # wiki：每 4 天一罐
    range_tiles: int

    def price_for(self, crop: Crop) -> int | None:
        """花蜜售价；不是花的作物返回 None。"""
        if crop.category != "flower":
            return None
        return int(self.base_price + crop.sell_price * self.flower_multiplier)


@dataclass
class GameData:
    crops: list[Crop]
    artisan_rules: list[ArtisanRule]
    calendar: dict[str, list[CalendarEvent]]
    guide_chunks: list[GuideChunk] = field(default_factory=list)
    starting_gold: int | None = None  # 开局金币（data/farm.json），用于第 1 年春季的资金起点
    starting_seeds: dict[str, int] = field(default_factory=dict)  # 开局赠送的种子（作物英文名 → 数量）
    farm_types: dict[str, dict] = field(default_factory=dict)      # 农场地图；可覆盖 starting_seeds
    greenhouse_tiles: int = 120  # 温室耕地格数（data/farm.json）
    honey: HoneyRule | None = None  # 花蜜规则（data/artisan.json）
    animals: list = field(default_factory=list)  # 农场动物（data/animals.json），元素为 livestock.Animal
    hay_price: int = 50                          # 玛妮牧场的干草价格（data/animals.json）
    bundles: list = field(default_factory=list)  # 社区中心收集包（data/bundles.json），元素为 bundles.Bundle
    tool_rules: object = None                    # 工具升级规则（data/tools.json），tools.ToolRules

    def resolve_farm_type(self, name: str | None) -> str:
        """农场地图的 key；接受 key、中文名或英文名，不传为标准农场。"""
        if not name:
            return "standard"
        key = name.strip().lower().replace("-", "").replace(" ", "_")
        for k, info in self.farm_types.items():
            if key in (k, k.replace("_", ""), info["name_zh"], info["name_en"].lower().replace("-", "").replace(" ", "_")):
                return k
        options = "、".join(f"{k}（{v['name_zh']}）" for k, v in self.farm_types.items())
        raise ValueError(f"未知农场类型：{name!r}，可选：{options}")

    def starting_seed_stock(self, farm_type: str | None = None) -> dict[str, int]:
        """开局赠送的种子，按本项目的作物中文名（data/crops.json 里没有的作物忽略）。草原农场没有。"""
        seeds = self.starting_seeds
        if self.farm_types:
            seeds = self.farm_types[self.resolve_farm_type(farm_type)].get("starting_seeds", seeds)
        by_en = {c.name_en: c.name for c in self.crops}
        return {by_en[en]: n for en, n in seeds.items() if en in by_en}
    npc_data: NPCData = field(default_factory=NPCData)  # NPC 生日与送礼喜好（data/npcs.json）

    # ---- 结构化查询 ----
    def crops_in_season(self, season: str, year: int | None = None) -> list[Crop]:
        """某季可种的作物；传入 year 时只保留当年能买到种子的（available_year ≤ year）。"""
        season = normalize_season(season)
        return [c for c in self.crops_for_year(year) if season in c.seasons]

    def crops_for_year(self, year: int | None = None) -> list[Crop]:
        """year 为 None 时不过滤；否则去掉要到之后年份才出售种子的作物。"""
        if year is None:
            return list(self.crops)
        if year < 1:
            raise ValueError(f"年份必须 ≥ 1，收到 {year}")
        return [c for c in self.crops if c.available_year <= year]

    def events_in_season(self, season: str) -> list[CalendarEvent]:
        return sorted(self.calendar.get(normalize_season(season), []), key=lambda e: e.day)

    def artisan_rules_for(self, crop: Crop) -> list[ArtisanRule]:
        """作物可用的加工规则。某台机器有该作物的专属规则时（如小麦 → 啤酒），不再套用这台机器的类别规则。"""
        specific = [r for r in self.artisan_rules if r.input_crops and r.applies_to(crop)]
        machines = {r.machine for r in specific}
        general = [r for r in self.artisan_rules
                   if not r.input_crops and r.applies_to(crop) and r.machine not in machines]
        return specific + general


# ---------------------------------------------------------------------------
# 加载函数
# ---------------------------------------------------------------------------

def _read_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def load_crops(path: Path) -> list[Crop]:
    raw = _read_json(path)["crops"]
    return [Crop(**{**item, "seasons": tuple(item["seasons"]), "aliases": tuple(item.get("aliases", ())),
                    "byproducts": tuple(Byproduct(**b) for b in item.get("byproducts", ()))}) for item in raw]


def load_tool_rules(path: Path):
    """工具升级规则；文件不存在时为 None（不校验 tool_upgrades）。"""
    from .tools import Tool, ToolRules, UpgradeLevel  # 避免循环导入
    if not path.exists():
        return None
    raw = _read_json(path)
    tools = tuple(Tool(t["name"], t["name_en"], tuple(t.get("aliases", ())), tuple(t.get("conflict_words", ())),
                       bool(t.get("blocks_planting", False))) for t in raw["tools"])
    levels = tuple(UpgradeLevel(**lv) for lv in raw["levels"])
    return ToolRules(tools, levels, int(raw.get("upgrade_days", 2)), tuple(raw.get("open_festivals", ())))


def load_bundles(path: Path) -> list:
    """社区中心收集包；文件不存在时为空（献祭路线没有可检查的物品）。"""
    from .bundles import Bundle, BundleItem  # 避免循环导入
    if not path.exists():
        return []
    return [Bundle(b["name"], b["name_en"], b["room"], b["room_en"], int(b["slots"]),
                   tuple(BundleItem(i["name_en"], int(i["count"]), i.get("quality", "normal")) for i in b["items"]))
            for b in _read_json(path)["bundles"]]


def load_animals(path: Path) -> list:
    """农场动物；文件不存在时为空（不支持 --animals）。"""
    from .livestock import Animal  # 避免循环导入
    if not path.exists():
        return []
    return [Animal(**a) for a in _read_json(path)["animals"]]


def load_honey(path: Path) -> HoneyRule | None:
    raw = _read_json(path).get("honey")
    return HoneyRule(**raw) if raw else None


def load_artisan_rules(path: Path) -> list[ArtisanRule]:
    return [ArtisanRule(**{**item, "input_crops": tuple(item.get("input_crops", ()))})
            for item in _read_json(path)["rules"]]


def load_calendar(path: Path) -> dict[str, list[CalendarEvent]]:
    raw = _read_json(path)
    return {
        season: [CalendarEvent(**e) for e in events]
        for season, events in raw.items()
        if not season.startswith("_")
    }


_TAG_LINE = re.compile(r"^\s*(?:标签|tags)\s*[:：]\s*(.+)$", re.IGNORECASE)


def parse_guide_markdown(text: str, source: str) -> list[GuideChunk]:
    chunks: list[GuideChunk] = []
    for section in re.split(r"^##\s+", text, flags=re.MULTILINE):
        section = section.strip()
        if not section:
            continue
        title, _, body = section.partition("\n")
        tags: tuple[str, ...] = ()
        body_lines = []
        for line in body.splitlines():
            m = _TAG_LINE.match(line)
            if m and not tags:
                tags = tuple(t.strip() for t in re.split(r"[,，、]", m.group(1)) if t.strip())
            else:
                body_lines.append(line)
        chunks.append(GuideChunk(source, title.strip(), "\n".join(body_lines).strip(), tags))
    return chunks


def load_guides(guide_dir: Path) -> list[GuideChunk]:
    chunks: list[GuideChunk] = []
    for path in sorted(guide_dir.glob("*.md")) + sorted(guide_dir.glob("*.txt")):
        chunks.extend(parse_guide_markdown(path.read_text(encoding="utf-8"), path.name))
    return chunks


def load_game_data(data_dir: str | Path = DEFAULT_DATA_DIR) -> GameData:
    data_dir = Path(data_dir)
    return GameData(
        crops=load_crops(data_dir / "crops.json"),
        artisan_rules=load_artisan_rules(data_dir / "artisan.json"),
        honey=load_honey(data_dir / "artisan.json"),
        animals=load_animals(data_dir / "animals.json"),
        bundles=load_bundles(data_dir / "bundles.json"),
        tool_rules=load_tool_rules(data_dir / "tools.json"),
        hay_price=int(_read_json(data_dir / "animals.json").get("hay_price", 50)) if (data_dir / "animals.json").exists()
        else 50,
        calendar=load_calendar(data_dir / "calendar.json"),
        guide_chunks=load_guides(data_dir / "guides"),
        starting_gold=load_starting_gold(data_dir / "farm.json"),
        starting_seeds=load_starting_seeds(data_dir / "farm.json"),
        farm_types=load_farm_types(data_dir / "farm.json"),
        greenhouse_tiles=int((_read_json(data_dir / "farm.json").get("greenhouse") or {}).get("tiles", 120))
        if (data_dir / "farm.json").exists() else 120,
        npc_data=load_npcs(data_dir / "npcs.json"),
    )


def _gift_items(raw) -> tuple[GiftItem, ...]:
    return tuple(GiftItem(i["name"], i["name_en"], tuple(i.get("aliases", ()))) for i in raw or ())


def load_npcs(path: Path) -> NPCData:
    """NPC 生日与喜好；文件不存在时返回空数据（不注入生日、不做送礼校验）。"""
    if not path.exists():
        return NPCData()
    raw = _read_json(path)
    npcs = tuple(
        NPC(
            name=n["name"], name_en=n["name_en"],
            birthday_season=normalize_season(n["birthday"]["season"]), birthday_day=int(n["birthday"]["day"]),
            loves=_gift_items(n.get("loves")), likes=_gift_items(n.get("likes")),
            universal_love_exceptions=tuple(n.get("universal_love_exceptions", ())),
            universal_likes_note=n.get("universal_likes_note", ""),
            dislikes=_gift_items(n.get("dislikes")), hates=_gift_items(n.get("hates")),
            gift_notes={k: tuple(v) for k, v in n.get("gift_notes", {}).items()},
            marriage=bool(n.get("marriage", False)),
        )
        for n in raw["npcs"]
    )

    def group(key: str) -> GiftGroup:
        g = raw.get(key) or {}
        return GiftGroup(tuple(g.get("categories", ())), _gift_items(g.get("items")))

    return NPCData(npcs, _gift_items(raw.get("universal_loves")), group("universal_likes"),
                   group("universal_neutrals"), group("universal_dislikes"), group("universal_hates"))


def load_farm_types(path: Path) -> dict[str, dict]:
    return _read_json(path).get("farm_types", {}) if path.exists() else {}


def load_starting_seeds(path: Path) -> dict[str, int]:
    return {k: int(v) for k, v in _read_json(path).get("starting_seeds", {}).items()} if path.exists() else {}


def load_starting_gold(path: Path) -> int | None:
    """开局金币；文件不存在时返回 None（资金只给净收支，不给总额）。"""
    return int(_read_json(path)["starting_gold"]) if path.exists() else None


# ---------------------------------------------------------------------------
# 向量检索（ChromaDB）
# ---------------------------------------------------------------------------

NO_GUIDES_TEXT = "（无相关攻略片段）"

# 多语言句向量模型（支持中文等 50+ 种语言，384 维）。换模型后旧向量不可再用，见 _drop_if_model_changed。
EMBEDDING_MODEL = "paraphrase-multilingual-MiniLM-L12-v2"


def make_embedding_function(model_name: str = EMBEDDING_MODEL):
    """本地运行的 SentenceTransformer 模型（首次使用从 Hugging Face 下载，之后离线可用）。"""
    from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction

    return SentenceTransformerEmbeddingFunction(model_name=model_name, normalize_embeddings=True)


def _drop_if_model_changed(client: Any, embedding_model: str) -> bool:
    """集合由别的 Embedding 模型建立时删除它，返回是否删除。

    不同模型的向量不在同一个空间里，即使维度相同（如旧的 all-MiniLM-L6-v2 也是 384 维），
    混用也会得到错误的检索结果，所以必须整体重建。旧版本建立的集合没有 embedding_model 元数据，
    同样视为不匹配。
    """
    for col in client.list_collections():
        name = col if isinstance(col, str) else col.name
        if name != COLLECTION_NAME:
            continue
        metadata = (client.get_collection(name).metadata if isinstance(col, str) else col.metadata) or {}
        if metadata.get("embedding_model") == embedding_model:
            return False
        logger.info("Embedding 模型已从 %s 换成 %s，重建向量库", metadata.get("embedding_model", "未记录"), embedding_model)
        client.delete_collection(name)
        return True
    return False


class GuideStore:
    """攻略片段的向量库：入库（upsert）与语义检索（query）。

    默认使用持久化客户端（data/.chroma_db）和本地运行的多语言模型
    paraphrase-multilingual-MiniLM-L12-v2（首次使用下载约 470MB，之后离线可用）。
    集合元数据记录了建库所用的模型，换模型后会自动删除旧集合并重建。
    测试时可以传入自己的 client / embedding_function / embedding_model。
    """

    def __init__(
        self,
        path: str | Path = CHROMA_DIR,
        *,
        client: Any = None,
        embedding_function: Any = None,
        embedding_model: str = EMBEDDING_MODEL,
    ):
        """embedding_model 记录在集合元数据里；传入自定义 embedding_function 时也要给出对应的模型名。"""
        import chromadb
        from chromadb.config import Settings

        if client is None:
            client = chromadb.PersistentClient(path=str(path), settings=Settings(anonymized_telemetry=False))
        if embedding_function is None:
            embedding_function = make_embedding_function(embedding_model)

        self.embedding_model = embedding_model
        self.rebuilt = _drop_if_model_changed(client, embedding_model)
        self.collection = client.get_or_create_collection(
            COLLECTION_NAME,
            embedding_function=embedding_function,
            metadata={"hnsw:space": "cosine", "embedding_model": embedding_model},
        )

    def index(self, chunks: list[GuideChunk]) -> dict[str, int]:
        """把切块写入集合，并删除攻略文件里已经不存在的旧片段。

        只对新增片段计算 Embedding：已在库中的 ID（同一 MD5）直接跳过。
        返回 {"added": 新写入数, "removed": 删除数, "total": 当前总数}。
        """
        by_id = {c.doc_id: c for c in chunks}  # 内容相同的片段只保留一份
        # 显式 limit=None：取回全部 ID，不受任何版本的默认分页影响，否则孤儿片段可能删不掉
        existing = set(self.collection.get(include=[], limit=None)["ids"])
        new_ids = [i for i in by_id if i not in existing]
        stale_ids = sorted(existing - by_id.keys())

        if new_ids:
            self.collection.upsert(
                ids=new_ids,
                documents=[by_id[i].document for i in new_ids],
                metadatas=[
                    {"source": by_id[i].source, "title": by_id[i].title,
                     "tags": ",".join(by_id[i].tags), "text": by_id[i].text}
                    for i in new_ids
                ],
            )
        if stale_ids:
            self.collection.delete(ids=stale_ids)
        return {"added": len(new_ids), "removed": len(stale_ids), "total": self.collection.count()}

    def search(self, query_text: str, n_results: int = 3) -> list[tuple[GuideChunk, float]]:
        """返回最相关的 N 个片段及其余弦距离（越小越相关）。"""
        count = self.collection.count()
        if count == 0 or n_results <= 0:
            return []
        res = self.collection.query(
            query_texts=[query_text],
            n_results=min(n_results, count),
            include=["metadatas", "distances"],
        )
        return [
            (GuideChunk(m["source"], m["title"], m["text"], tuple(t for t in m["tags"].split(",") if t)), float(d))
            for m, d in zip(res["metadatas"][0], res["distances"][0])
        ]


_default_store: GuideStore | None = None
_vector_disabled = False
_fallback_chunks: list[GuideChunk] | None = None  # 兜底检索用的切块缓存，避免每次都读盘
# 网页启动时的预加载线程和第一个请求可能同时初始化：加锁，保证只建一次库
_store_lock = threading.Lock()


def get_fallback_chunks() -> list[GuideChunk]:
    """懒加载并缓存 data/guides 的切块。和向量库一样，进程运行期间不感知攻略文件的修改。"""
    global _fallback_chunks
    if _fallback_chunks is None:
        with _store_lock:
            if _fallback_chunks is None:
                _fallback_chunks = load_guides(DEFAULT_GUIDE_DIR)
    return _fallback_chunks


def get_guide_store() -> GuideStore | None:
    """懒加载默认向量库，并同步 data/guides 的最新内容。chromadb 不可用时返回 None。"""
    global _default_store, _vector_disabled
    if _default_store is None and not _vector_disabled:
        with _store_lock:  # 双重检查：拿到锁后再确认一次，另一个线程可能已经建好了
            if _default_store is None and not _vector_disabled:
                try:
                    store = GuideStore()
                    stats = store.index(load_guides(DEFAULT_GUIDE_DIR))
                    logger.info("攻略向量库已同步：%s", stats)
                    _default_store = store
                except Exception as e:  # 未安装 chromadb、模型下载失败、数据库损坏等
                    _vector_disabled = True
                    logger.warning("向量检索不可用，改用关键词检索：%s", e)
    return _default_store


def index_guides(guide_dir: str | Path = DEFAULT_GUIDE_DIR, store: GuideStore | None = None) -> dict[str, int]:
    """遍历 guide_dir 下的攻略文件，按 `## 标题` 切块后写入向量库。"""
    store = store or GuideStore()
    return store.index(load_guides(Path(guide_dir)))


def search_guides(
    query_text: str,
    n_results: int = 3,
    *,
    store: GuideStore | None = None,
    fallback_chunks: list[GuideChunk] | None = None,
) -> str:
    """语义检索与用户意图（如“春季，钓鱼赚钱，休闲节奏”）最相关的 N 个片段，返回拼接好的文本。

    向量库不可用或检索出错时，用关键词检索 fallback_chunks 兜底（默认为缓存的 data/guides 切块）。
    """
    store = store or get_guide_store()
    chunks: list[GuideChunk] = []
    if store is not None:
        try:
            chunks = [c for c, _ in store.search(query_text, n_results)]
        except Exception as e:
            logger.warning("向量检索失败，改用关键词检索：%s", e)
            store = None
    if store is None:
        pool = fallback_chunks if fallback_chunks is not None else get_fallback_chunks()
        chunks = keyword_search(pool, query_text, k=n_results)
    return "\n\n".join(c.to_prompt() for c in chunks) or NO_GUIDES_TEXT


def keyword_search(chunks: list[GuideChunk], query: str, k: int = 3) -> list[GuideChunk]:
    """关键词检索（兜底方案）：标签出现在 query 中记 3 分，query 里的词出现在标题或正文记 1 分。"""
    # 不按长度过滤：中文里“春”“矿”“草”这类单字本身就是关键词
    terms = [t for t in re.split(r"[\s,，、/；;。]+", query) if t.strip()]

    def score(chunk: GuideChunk) -> int:
        body = chunk.title + chunk.text
        return sum(3 for tag in chunk.tags if tag in query) + sum(1 for t in terms if t in body)

    ranked = sorted(chunks, key=score, reverse=True)
    return [c for c in ranked[:k] if score(c) > 0]
