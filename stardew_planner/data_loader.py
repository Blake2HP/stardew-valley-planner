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
    byproducts: tuple[Byproduct, ...] = field(default=(), compare=False, hash=False)


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


@dataclass(frozen=True)
class NPCData:
    npcs: tuple[NPC, ...] = ()
    universal_loves: tuple[GiftItem, ...] = ()

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


@dataclass
class GameData:
    crops: list[Crop]
    artisan_rules: list[ArtisanRule]
    calendar: dict[str, list[CalendarEvent]]
    guide_chunks: list[GuideChunk] = field(default_factory=list)
    starting_gold: int | None = None  # 开局金币（data/farm.json），用于第 1 年春季的资金起点
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
    return [Crop(**{**item, "seasons": tuple(item["seasons"]),
                    "byproducts": tuple(Byproduct(**b) for b in item.get("byproducts", ()))}) for item in raw]


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
        calendar=load_calendar(data_dir / "calendar.json"),
        guide_chunks=load_guides(data_dir / "guides"),
        starting_gold=load_starting_gold(data_dir / "farm.json"),
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
        )
        for n in raw["npcs"]
    )
    return NPCData(npcs, _gift_items(raw.get("universal_loves")))


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


def get_fallback_chunks() -> list[GuideChunk]:
    """懒加载并缓存 data/guides 的切块。和向量库一样，进程运行期间不感知攻略文件的修改。"""
    global _fallback_chunks
    if _fallback_chunks is None:
        _fallback_chunks = load_guides(DEFAULT_GUIDE_DIR)
    return _fallback_chunks


def get_guide_store() -> GuideStore | None:
    """懒加载默认向量库，并同步 data/guides 的最新内容。chromadb 不可用时返回 None。"""
    global _default_store, _vector_disabled
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
