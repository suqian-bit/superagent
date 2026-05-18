"""
RAG 引擎：语义检索 + 问答

设计：
- ChromaDB（PersistentClient）落盘在 /www/knowledge/chroma_db/
- Embedding 模型：BAAI/bge-small-zh-v1.5（中文优化，512 维，CPU 友好）
- 单例模式：模型只加载一次（首次约 2-3 秒）

API:
  engine = get_engine()
  engine.add_or_update(item_id, title, content, tags, ...)
  engine.search(query, top_k=5) -> [{item_id, title, score, snippet}]
  engine.similar(item_id, top_k=5) -> 同上
  engine.delete(item_id)
  engine.reindex_from_db() -> 全量重建

输入文本组装策略（让检索效果好）：
  title + summary_one_line + essence + tags + categories 拼接
  ↑ essence 是主体（200-1800 字结构化），最有信息量
"""
from __future__ import annotations

import os
import sqlite3
import threading
from pathlib import Path
from typing import Optional

# 环境变量：让 transformers/sentence-transformers 用数据盘缓存
os.environ.setdefault("HF_HOME", "/www/embed_models")
os.environ.setdefault("SENTENCE_TRANSFORMERS_HOME", "/www/embed_models")

# 静音 telemetry
os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")


DB_PATH = Path("/www/knowledge/index.db")
CHROMA_DIR = Path("/www/knowledge/chroma_db")
EMBED_MODEL_NAME = "BAAI/bge-small-zh-v1.5"
COLLECTION_NAME = "items"


_lock = threading.Lock()
_singleton = None


def get_engine():
    """单例获取 RAG 引擎（线程安全）。"""
    global _singleton
    if _singleton is None:
        with _lock:
            if _singleton is None:
                _singleton = RAGEngine()
    return _singleton


class RAGEngine:
    def __init__(self):
        CHROMA_DIR.mkdir(parents=True, exist_ok=True)

        # 延迟导入，避免冷启动时拖慢其他不需要 RAG 的命令
        import chromadb
        from chromadb.config import Settings
        from sentence_transformers import SentenceTransformer

        self.client = chromadb.PersistentClient(
            path=str(CHROMA_DIR),
            settings=Settings(anonymized_telemetry=False),
        )
        # 注意：我们自己算 embedding，不让 chromadb 自动调（默认会拉 onnx）
        self.collection = self.client.get_or_create_collection(
            name=COLLECTION_NAME,
            embedding_function=None,  # 我们自己提供
            metadata={"hnsw:space": "cosine"},
        )
        self.model = SentenceTransformer(
            EMBED_MODEL_NAME,
            cache_folder="/www/embed_models",
        )

    # ─────────────────────────────────────────
    # 文本组装：决定 embedding 哪些字段
    # ─────────────────────────────────────────

    @staticmethod
    def build_text(item: dict) -> str:
        """把 item 字段拼成检索文本。"""
        parts = []
        if item.get("title"):
            parts.append(f"标题：{item['title']}")
        if item.get("summary_one_line"):
            parts.append(f"核心：{item['summary_one_line']}")
        # 平台/作者帮助消歧
        if item.get("platform") and item.get("uploader"):
            parts.append(f"{item['platform']}/{item['uploader']}")
        # tags
        tags = item.get("tags") or []
        if isinstance(tags, str):
            parts.append(f"标签：{tags}")
        elif tags:
            parts.append(f"标签：{', '.join(tags)}")
        # categories
        cats = item.get("categories") or []
        if cats:
            parts.append(f"分类：{', '.join(cats)}")
        # essence 主体（最有信息量）
        if item.get("essence"):
            parts.append(item["essence"][:3000])  # 限长，省 token 也省 embed 算力
        elif item.get("content"):
            parts.append(item["content"][:3000])
        return "\n\n".join(parts)

    # ─────────────────────────────────────────
    # CRUD
    # ─────────────────────────────────────────

    def add_or_update(self, item_id: int, text: str, metadata: dict):
        """加入或更新一条 item 到向量库。"""
        # bge 推荐做 normalize（已在模型内默认 normalize_embeddings=True，但显式更稳）
        emb = self.model.encode(
            [text], normalize_embeddings=True, show_progress_bar=False,
        )[0].tolist()
        # ChromaDB metadata 只接受 str/int/float/bool
        clean_meta = {}
        for k, v in metadata.items():
            if v is None:
                continue
            if isinstance(v, (str, int, float, bool)):
                clean_meta[k] = v
            else:
                clean_meta[k] = str(v)
        self.collection.upsert(
            ids=[str(item_id)],
            embeddings=[emb],
            documents=[text],
            metadatas=[clean_meta],
        )

    def delete(self, item_id: int):
        try:
            self.collection.delete(ids=[str(item_id)])
        except Exception:
            pass

    # ─────────────────────────────────────────
    # 检索
    # ─────────────────────────────────────────

    def search(self, query: str, top_k: int = 5,
               where: Optional[dict] = None) -> list[dict]:
        """语义检索。返回 [{item_id, title, score, snippet, metadata}]"""
        emb = self.model.encode(
            [query], normalize_embeddings=True, show_progress_bar=False,
        )[0].tolist()

        kwargs = dict(
            query_embeddings=[emb],
            n_results=top_k,
        )
        if where:
            kwargs["where"] = where

        res = self.collection.query(**kwargs)
        if not res["ids"] or not res["ids"][0]:
            return []

        out = []
        ids = res["ids"][0]
        distances = res.get("distances", [[]])[0]
        docs = res.get("documents", [[]])[0]
        metas = res.get("metadatas", [[]])[0]

        for i, raw_id in enumerate(ids):
            try:
                item_id = int(raw_id)
            except ValueError:
                continue
            # cosine distance 转 similarity（[0,1]，越大越像）
            dist = distances[i] if i < len(distances) else 1.0
            score = max(0.0, 1.0 - dist)
            doc = docs[i] if i < len(docs) else ""
            meta = metas[i] if i < len(metas) else {}

            out.append({
                "item_id": item_id,
                "title": meta.get("title", ""),
                "platform": meta.get("platform", ""),
                "uploader": meta.get("uploader", ""),
                "tier": meta.get("tier", ""),
                "weight": meta.get("weight", 0),
                "score": round(score, 3),
                "snippet": doc[:300],
                "summary_one_line": meta.get("summary_one_line", ""),
            })
        return out

    def similar(self, item_id: int, top_k: int = 5) -> list[dict]:
        """找跟 id=X 最相似的 N 条（排除自己）。"""
        # 先取出这条的 embedding
        got = self.collection.get(ids=[str(item_id)], include=["embeddings"])
        if not got["ids"]:
            return []
        emb = got["embeddings"][0]
        # 查询 top_k+1（要排除自己）
        res = self.collection.query(
            query_embeddings=[emb],
            n_results=top_k + 1,
        )
        out = []
        ids = res["ids"][0] if res["ids"] else []
        distances = res.get("distances", [[]])[0]
        docs = res.get("documents", [[]])[0]
        metas = res.get("metadatas", [[]])[0]

        for i, raw_id in enumerate(ids):
            try:
                rid = int(raw_id)
            except ValueError:
                continue
            if rid == item_id:
                continue
            dist = distances[i] if i < len(distances) else 1.0
            score = max(0.0, 1.0 - dist)
            doc = docs[i] if i < len(docs) else ""
            meta = metas[i] if i < len(metas) else {}
            out.append({
                "item_id": rid,
                "title": meta.get("title", ""),
                "tier": meta.get("tier", ""),
                "weight": meta.get("weight", 0),
                "score": round(score, 3),
                "snippet": doc[:300],
                "summary_one_line": meta.get("summary_one_line", ""),
            })
            if len(out) >= top_k:
                break
        return out

    def count(self) -> int:
        return self.collection.count()

    # ─────────────────────────────────────────
    # 一次性 Backfill（从 SQLite 重建整个向量库）
    # ─────────────────────────────────────────

    def reindex_from_db(self, *, verbose: bool = True) -> dict:
        """从 SQLite + md 文件全量重建向量库。"""
        if not DB_PATH.exists():
            return {"ok": False, "error": "index.db 不存在"}

        # 清空当前 collection（删除并重建）
        try:
            self.client.delete_collection(COLLECTION_NAME)
        except Exception:
            pass
        self.collection = self.client.get_or_create_collection(
            name=COLLECTION_NAME,
            embedding_function=None,
            metadata={"hnsw:space": "cosine"},
        )

        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT id, title, platform, uploader, tier, weight, score_total,
                   summary_one_line, file_path
            FROM items
            WHERE archived = 0
            ORDER BY id
        """).fetchall()

        total = len(rows)
        if total == 0:
            conn.close()
            return {"ok": True, "indexed": 0, "msg": "库里还没有内容"}

        indexed = 0
        skipped = 0
        for r in rows:
            d = dict(r)
            # 读 md 文件抽 essence
            essence = ""
            content = ""
            tags = []
            categories = []
            md_path = d.get("file_path")
            if md_path and Path(md_path).exists():
                md = Path(md_path).read_text(encoding="utf-8", errors="ignore")
                # 抽 ## 精华 段
                import re
                m_ess = re.search(
                    r"^## 精华\s*\n(.*?)(?=^## |\Z)", md,
                    re.MULTILINE | re.DOTALL,
                )
                if m_ess:
                    essence = m_ess.group(1).strip()
                # 抽 ## 完整原文 段
                m_cont = re.search(
                    r"^## 完整原文\s*\n(.*)", md,
                    re.MULTILINE | re.DOTALL,
                )
                if m_cont:
                    content = m_cont.group(1).strip()

            # 拿 tags + categories（从关联表）
            tag_rows = conn.execute("""
                SELECT t.name FROM tags t
                JOIN item_tags it ON t.id = it.tag_id
                WHERE it.item_id = ?
            """, (d["id"],)).fetchall()
            tags = [tr[0] for tr in tag_rows]

            cat_rows = conn.execute("""
                SELECT c.name FROM categories c
                JOIN item_categories ic ON c.id = ic.category_id
                WHERE ic.item_id = ?
            """, (d["id"],)).fetchall()
            categories = [cr[0] for cr in cat_rows]

            item = {
                **d,
                "essence": essence,
                "content": content,
                "tags": tags,
                "categories": categories,
            }

            text = self.build_text(item)
            if not text.strip():
                skipped += 1
                continue

            metadata = {
                "title": d.get("title", ""),
                "platform": d.get("platform", ""),
                "uploader": d.get("uploader", ""),
                "tier": d.get("tier", ""),
                "weight": d.get("weight", 0),
                "score_total": d.get("score_total", 0),
                "summary_one_line": d.get("summary_one_line", ""),
            }

            self.add_or_update(d["id"], text, metadata)
            indexed += 1
            if verbose:
                print(f"  [{indexed}/{total}] id={d['id']} {d['title'][:40]}")

        conn.close()
        return {
            "ok": True,
            "indexed": indexed,
            "skipped": skipped,
            "total_in_db": total,
            "collection_count": self.collection.count(),
        }
