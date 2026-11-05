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

    def price_for(self, crop: Crop) -> int:
        if self.fixed_price is not None:
            return self.fixed_price
        return int(crop.sell_price * self.multiplier + self.bonus)


@dataclass(frozen=True)
class CalendarEvent:
    day: int
    name: str
    type: str
    tip: str


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


@dataclass
class GameData:
    crops: list[Crop]
    artisan_rules: list[ArtisanRule]
    calendar: dict[str, list[CalendarEvent]]
    guide_chunks: list[GuideChunk] = field(default_factory=list)

    # ---- 结构化查询 ----
    def crops_in_season(self, season: str) -> list[Crop]:
        season = normalize_season(season)
        return [c for c in self.crops if season in c.seasons]

    def events_in_season(self, season: str) -> list[CalendarEvent]:
        return sorted(self.calendar.get(normalize_season(season), []), key=lambda e: e.day)

    def artisan_rules_for(self, crop: Crop) -> list[ArtisanRule]:
        return [r for r in self.artisan_rules if r.input_category == crop.category]


# ---------------------------------------------------------------------------
# 加载函数
# ---------------------------------------------------------------------------

def _read_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def load_crops(path: Path) -> list[Crop]:
    raw = _read_json(path)["crops"]
    return [Crop(**{**item, "seasons": tuple(item["seasons"])}) for item in raw]


def load_artisan_rules(path: Path) -> list[ArtisanRule]:
    return [ArtisanRule(**item) for item in _read_json(path)["rules"]]


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
    )


# ---------------------------------------------------------------------------
# 向量检索（ChromaDB）
# ---------------------------------------------------------------------------

NO_GUIDES_TEXT = "（无相关攻略片段）"


class GuideStore:
    """攻略片段的向量库：入库（upsert）与语义检索（query）。

    默认使用持久化客户端（data/.chroma_db）和 ChromaDB 自带的本地 Embedding 函数
    （all-MiniLM-L6-v2，ONNX 本地推理，首次使用会下载约 80MB 模型，之后离线可用）。
    测试时可以传入自己的 client / embedding_function。
    """

    def __init__(self, path: str | Path = CHROMA_DIR, *, client: Any = None, embedding_function: Any = None):
        import chromadb
        from chromadb.config import Settings
        from chromadb.utils import embedding_functions

        if client is None:
            client = chromadb.PersistentClient(path=str(path), settings=Settings(anonymized_telemetry=False))
        self.collection = client.get_or_create_collection(
            COLLECTION_NAME,
            embedding_function=embedding_function or embedding_functions.DefaultEmbeddingFunction(),
            metadata={"hnsw:space": "cosine"},
        )

    def index(self, chunks: list[GuideChunk]) -> dict[str, int]:
        """把切块写入集合，并删除攻略文件里已经不存在的旧片段。

        只对新增片段计算 Embedding：已在库中的 ID（同一 MD5）直接跳过。
        返回 {"added": 新写入数, "removed": 删除数, "total": 当前总数}。
        """
        by_id = {c.doc_id: c for c in chunks}  # 内容相同的片段只保留一份
        existing = set(self.collection.get(include=[])["ids"])
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

    向量库不可用或检索出错时，用关键词检索 fallback_chunks（默认读取 data/guides）兜底。
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
        pool = fallback_chunks if fallback_chunks is not None else load_guides(DEFAULT_GUIDE_DIR)
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
