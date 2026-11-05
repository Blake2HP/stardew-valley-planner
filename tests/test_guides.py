"""攻略文本切块、向量入库与语义检索。"""

import hashlib

import pytest

from stardew_planner import data_loader
from stardew_planner.data_loader import (
    NO_GUIDES_TEXT,
    GuideChunk,
    keyword_search,
    load_guides,
    parse_guide_markdown,
    search_guides,
)

GUIDES = load_guides(data_loader.DEFAULT_GUIDE_DIR)
SAMPLE = """## 钓鱼起步
标签: 钓鱼, 新手
第二天去海滩领取鱼竿。

## 矿井推进
标签: 挖矿
春季 5 日矿井开放。
"""


# ---------------------------------------------------------------------------
# 切块与 ID
# ---------------------------------------------------------------------------

def test_parse_splits_by_heading_and_reads_tags():
    chunks = parse_guide_markdown(SAMPLE, "sample.md")
    assert [c.title for c in chunks] == ["钓鱼起步", "矿井推进"]
    assert chunks[0].tags == ("钓鱼", "新手")
    assert chunks[0].text == "第二天去海滩领取鱼竿。"
    assert all(c.source == "sample.md" for c in chunks)


def test_doc_id_is_md5_of_document():
    chunk = parse_guide_markdown(SAMPLE, "sample.md")[0]
    assert chunk.document == "钓鱼起步\n标签：钓鱼、新手\n第二天去海滩领取鱼竿。"
    assert chunk.doc_id == hashlib.md5(chunk.document.encode("utf-8")).hexdigest()


def test_doc_id_ignores_source_file():
    a = GuideChunk("a.md", "标题", "正文", ())
    b = GuideChunk("b.md", "标题", "正文", ())
    assert a.doc_id == b.doc_id


def test_all_guides_loaded():
    assert len(GUIDES) == 8
    assert len({c.doc_id for c in GUIDES}) == 8


# ---------------------------------------------------------------------------
# 入库：MD5 去重、增量更新
# ---------------------------------------------------------------------------

def test_index_is_idempotent(empty_store):
    assert empty_store.index(GUIDES) == {"added": 8, "removed": 0, "total": 8}
    assert empty_store.index(GUIDES) == {"added": 0, "removed": 0, "total": 8}


def test_index_dedupes_identical_chunks(empty_store):
    chunk = GUIDES[0]
    copy = GuideChunk("another.md", chunk.title, chunk.text, chunk.tags)
    assert empty_store.index([chunk, copy])["total"] == 1


def test_index_removes_stale_chunks(empty_store):
    empty_store.index(GUIDES)
    edited = GuideChunk(GUIDES[0].source, GUIDES[0].title, GUIDES[0].text + "（已更新）", GUIDES[0].tags)
    stats = empty_store.index([edited, *GUIDES[1:]])
    assert stats == {"added": 1, "removed": 1, "total": 8}


def test_index_stores_metadata(empty_store):
    empty_store.index(GUIDES[:1])
    meta = empty_store.collection.get(ids=[GUIDES[0].doc_id], include=["metadatas"])["metadatas"][0]
    assert meta["source"] == GUIDES[0].source
    assert meta["title"] == GUIDES[0].title
    assert meta["tags"] == ",".join(GUIDES[0].tags)


# ---------------------------------------------------------------------------
# 语义检索
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "query, expected_title",
    [
        ("春季，钓鱼赚钱，休闲节奏", "钓鱼起步"),
        ("下矿井挖铜矿", "矿井推进"),
        ("小桶酿酒和罐头瓶加工", "加工业基础"),
    ],
)
def test_search_ranks_relevant_chunk_first(indexed_store, query, expected_title):
    hits = indexed_store.search(query, n_results=3)
    assert hits[0][0].title == expected_title
    distances = [d for _, d in hits]
    assert distances == sorted(distances)


def test_search_respects_n_results(indexed_store):
    assert len(indexed_store.search("春季", n_results=2)) == 2
    assert len(indexed_store.search("春季", n_results=100)) == 8


def test_search_on_empty_store_returns_nothing(empty_store):
    assert empty_store.search("钓鱼") == []
    assert search_guides("钓鱼", store=empty_store) == NO_GUIDES_TEXT


def test_search_guides_returns_prompt_text(indexed_store):
    text = search_guides("春季，钓鱼赚钱，休闲节奏", n_results=2, store=indexed_store)
    assert text.startswith("### 钓鱼起步（来源：spring_basics.md）")
    assert text.count("### ") == 2


def test_search_guides_uses_default_store():
    # conftest 把默认向量库替换成了测试库
    assert "### 矿井推进" in search_guides("下矿井挖铜矿", n_results=1)


# ---------------------------------------------------------------------------
# 兜底：向量库不可用时退回关键词检索
# ---------------------------------------------------------------------------

class BrokenStore:
    def search(self, query_text, n_results=3):
        raise RuntimeError("embedding model unavailable")


def test_falls_back_to_keywords_when_search_fails():
    text = search_guides("钓鱼 新手", n_results=1, store=BrokenStore(), fallback_chunks=GUIDES)
    assert text.startswith("### 钓鱼起步")


def test_falls_back_when_vector_store_disabled(monkeypatch):
    monkeypatch.setattr(data_loader, "_default_store", None)
    monkeypatch.setattr(data_loader, "_vector_disabled", True)
    assert "### 钓鱼起步" in search_guides("钓鱼 新手", fallback_chunks=GUIDES)


@pytest.mark.parametrize(
    "query, expected_title",
    [("矿", "矿井推进"), ("桶", "加工业基础"), ("春 草莓", "草莓与复活节")],
    ids=["single-char", "single-char-2", "single-char-with-term"],
)
def test_keyword_search_keeps_single_char_terms(query, expected_title):
    assert keyword_search(GUIDES, query, k=1)[0].title == expected_title


def test_keyword_search_ignores_empty_terms():
    assert keyword_search(GUIDES, " ，、 ", k=3) == []


def test_keyword_search_scores_tags_and_terms():
    titles = [c.title for c in keyword_search(GUIDES, "挖矿 矿井", k=2)]
    assert titles[0] == "矿井推进"
    assert keyword_search(GUIDES, "完全无关的内容", k=3) == []
