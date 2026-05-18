#!/usr/bin/env python3
"""
save: 拿 step4 完整数据，落盘到知识库：
  - md 文件 (/www/knowledge/items/<date>/<id>_<fingerprint>.md)
  - 更新 SQLite (/www/knowledge/index.db)
  - 更新分类 MOC

含查重逻辑（按 fingerprint）。

用法: save --in /tmp/ingest/<run_id>_step4.json
输出: { ok, id, file_path, action(inserted/duplicate), summary_for_agent }
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, "/root/.hermes/profiles/knowledge/skills/_lib")
import pipeline_io


KB_ROOT = Path("/www/knowledge")
ITEMS_DIR = KB_ROOT / "items"
ESSENCE_DIR = KB_ROOT / "essence"
CATEGORIES_DIR = KB_ROOT / "categories"
INDEX_DB = KB_ROOT / "index.db"

REVIEW_INTERVALS = [1, 3, 7, 14, 30]  # 天


# ---- schema ----

DDL = """
CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fingerprint TEXT UNIQUE NOT NULL,
    platform TEXT,
    title TEXT,
    uploader TEXT,
    source_url TEXT,
    file_path TEXT NOT NULL,
    tier TEXT,
    weight INTEGER,
    score_total INTEGER,
    score_density INTEGER,
    score_actionable INTEGER,
    score_uniqueness INTEGER,
    score_reliability INTEGER,
    score_reusability INTEGER,
    summary_one_line TEXT,
    created_at TEXT NOT NULL,
    -- 复习
    review_enabled INTEGER DEFAULT 0,
    review_idx INTEGER DEFAULT 0,
    ease REAL DEFAULT 2.5,
    review_count INTEGER DEFAULT 0,
    last_reviewed_at TEXT,
    next_review_at TEXT,
    mastery INTEGER DEFAULT 0,
    archived INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS tags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    count INTEGER DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS item_tags (
    item_id INTEGER NOT NULL,
    tag_id INTEGER NOT NULL,
    PRIMARY KEY (item_id, tag_id),
    FOREIGN KEY (item_id) REFERENCES items(id),
    FOREIGN KEY (tag_id) REFERENCES tags(id)
);

CREATE TABLE IF NOT EXISTS categories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    description TEXT,
    count INTEGER DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS item_categories (
    item_id INTEGER NOT NULL,
    category_id INTEGER NOT NULL,
    affinity INTEGER DEFAULT 0,
    PRIMARY KEY (item_id, category_id),
    FOREIGN KEY (item_id) REFERENCES items(id),
    FOREIGN KEY (category_id) REFERENCES categories(id)
);

CREATE TABLE IF NOT EXISTS questions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id INTEGER NOT NULL,
    question TEXT NOT NULL,
    FOREIGN KEY (item_id) REFERENCES items(id)
);

CREATE INDEX IF NOT EXISTS idx_fingerprint ON items(fingerprint);
CREATE INDEX IF NOT EXISTS idx_next_review ON items(next_review_at) WHERE review_enabled=1 AND archived=0;
CREATE INDEX IF NOT EXISTS idx_created ON items(created_at DESC);
"""


def init_db() -> sqlite3.Connection:
    KB_ROOT.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(INDEX_DB)
    conn.row_factory = sqlite3.Row
    conn.executescript(DDL)
    return conn


def now_iso() -> str:
    return pipeline_io.now_iso()


def date_dir() -> str:
    return pipeline_io.today_str()


def slugify_fp(fp: str) -> str:
    return "".join(c if c.isalnum() or c == "-" else "-" for c in fp)[:80]


def fp_tokens(fp: str) -> set[str]:
    """把 fingerprint 拆成 token 集合（小写、去停用词），用于模糊查重。"""
    STOPWORDS = {"the", "a", "an", "of", "for", "to", "and", "in", "with", "guide"}
    raw = re.split(r"[-_\s]+", fp.lower())
    return {t for t in raw if t and t not in STOPWORDS and len(t) >= 2}


def find_similar_fingerprint(conn: sqlite3.Connection, new_fp: str,
                              threshold: float = 0.8) -> Optional[dict]:
    """
    用 token-set 比对找语义相似的指纹。
    threshold = Jaccard 系数下限（交集/并集），默认 0.8。
    """
    tokens_new = fp_tokens(new_fp)
    if not tokens_new:
        return None

    rows = conn.execute(
        "SELECT id, fingerprint, title, file_path, created_at FROM items WHERE archived=0"
    ).fetchall()

    for r in rows:
        tokens_old = fp_tokens(r["fingerprint"])
        if not tokens_old:
            continue
        inter = tokens_new & tokens_old
        union = tokens_new | tokens_old
        if not union:
            continue
        jaccard = len(inter) / len(union)
        if jaccard >= threshold:
            return {**dict(r), "jaccard": round(jaccard, 2),
                    "matched_tokens": sorted(inter)}
    return None


def write_md_file(item_id: int, data: dict) -> Path:
    """生成 md 文件，frontmatter + 一句话核心 + 精华 + 反思 + 原文。"""
    today = date_dir()
    dir_path = ITEMS_DIR / today
    dir_path.mkdir(parents=True, exist_ok=True)

    fp_slug = slugify_fp(data.get("fingerprint", "item"))
    md_path = dir_path / f"{item_id:04d}_{fp_slug}.md"

    scores = data.get("scores", {})
    cats = data.get("categories", [])
    cats_yaml = "\n".join(f"  {c['name']}: {c.get('affinity', 0)}" for c in cats)
    tags_yaml = "[" + ", ".join(f'"{t}"' for t in data.get("tags", [])) + "]"

    next_review = ""
    if data.get("tier") == "A" or (data.get("score_total", 0) >= 90):
        next_review = pipeline_io.add_days(REVIEW_INTERVALS[0])

    frontmatter = f"""---
id: {item_id}
fingerprint: {data.get('fingerprint', '')}
title: {json.dumps(data.get('title', ''), ensure_ascii=False)}
platform: {data.get('platform', '')}
uploader: {json.dumps(data.get('uploader', ''), ensure_ascii=False)}
source_url: {data.get('url', '')}
created_at: {now_iso()}
tier: {data.get('tier', 'C')}
weight: {data.get('weight', 0)}
scores:
  density: {scores.get('density', 0)}
  actionable: {scores.get('actionable', 0)}
  uniqueness: {scores.get('uniqueness', 0)}
  reliability: {scores.get('reliability', 0)}
  reusability: {scores.get('reusability', 0)}
  total: {data.get('score_total', 0)}
tags: {tags_yaml}
categories:
{cats_yaml or '  uncategorized: 50'}
review:
  enabled: {'true' if next_review else 'false'}
  next: {next_review or 'null'}
  count: 0
  mastery: 0
---
"""

    questions_md = "\n".join(f"{i+1}. {q}" for i, q in enumerate(data.get("questions", [])))
    body = f"""
## 一句话核心
{data.get('summary_one_line', '')}

## 评分要点
- ✅ 亮点：{data.get('score_rationale', {}).get('strength', '无')}
- ⚠️ 弱点：{data.get('score_rationale', {}).get('weakness', '无')}

## 精华
{data.get('essence', '')}

## 反思问题
{questions_md or '（无）'}

## 完整原文
{data.get('content', '(无文字内容)')}
"""

    md_path.write_text(frontmatter + body, encoding="utf-8")
    return md_path


def upsert_item_projects(conn: sqlite3.Connection, item_id: int, data: dict):
    """从 classify 阶段产出的 project_relevance 字段，写入 item_projects 表。"""
    rels = data.get("project_relevance", []) or []
    if not rels:
        return

    now_ts = now_iso()
    for r in rels:
        name = r.get("project", "").strip()
        if not name:
            continue
        relevance = int(r.get("relevance", 0))
        reason = r.get("reason", "")

        prow = conn.execute("SELECT id FROM projects WHERE name=?", (name,)).fetchone()
        if not prow:
            continue
        project_id = prow["id"]

        conn.execute("""
            INSERT OR REPLACE INTO item_projects
            (item_id, project_id, relevance, reason, created_at)
            VALUES (?, ?, ?, ?, ?)
        """, (item_id, project_id, relevance, reason, now_ts))

        # 项目档案的 updated_at 也更新（表示这个项目最近又有新关联）
        conn.execute("UPDATE projects SET updated_at=? WHERE id=?",
                     (now_ts, project_id))


def upsert_tags_and_categories(conn: sqlite3.Connection, item_id: int, data: dict):
    """把 tags 和 categories 写入对应表 + 关联表。"""
    now = now_iso()

    for tag in data.get("tags", []):
        if not tag:
            continue
        row = conn.execute("SELECT id FROM tags WHERE name=?", (tag,)).fetchone()
        if row:
            tag_id = row["id"]
            conn.execute("UPDATE tags SET count=count+1 WHERE id=?", (tag_id,))
        else:
            cur = conn.execute(
                "INSERT INTO tags (name, count, created_at) VALUES (?, 1, ?)",
                (tag, now),
            )
            tag_id = cur.lastrowid
        conn.execute(
            "INSERT OR IGNORE INTO item_tags (item_id, tag_id) VALUES (?, ?)",
            (item_id, tag_id),
        )

    for cat in data.get("categories", []):
        name = cat.get("name", "").strip()
        if not name:
            continue
        affinity = int(cat.get("affinity", 0))
        row = conn.execute("SELECT id FROM categories WHERE name=?", (name,)).fetchone()
        if row:
            cat_id = row["id"]
            conn.execute("UPDATE categories SET count=count+1 WHERE id=?", (cat_id,))
        else:
            cur = conn.execute(
                "INSERT INTO categories (name, description, count, created_at) VALUES (?, ?, 1, ?)",
                (name, cat.get("reason", ""), now),
            )
            cat_id = cur.lastrowid
        conn.execute(
            "INSERT OR REPLACE INTO item_categories (item_id, category_id, affinity) VALUES (?, ?, ?)",
            (item_id, cat_id, affinity),
        )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="input_file", required=True)
    args = ap.parse_args()

    data = pipeline_io.read_step_file(args.input_file)
    run_id = data.get("run_id")

    fp = data.get("fingerprint", "")
    if not fp:
        pipeline_io.emit_error("save", "缺少 fingerprint，无法入库", run_id=run_id)

    conn = init_db()

    # ---- 查重 ① 精确：指纹完全相同 ----
    existing = conn.execute(
        "SELECT id, title, file_path, created_at FROM items WHERE fingerprint=?",
        (fp,),
    ).fetchone()
    if existing:
        conn.close()
        out = {
            "ok": True,
            "step": "save",
            "run_id": run_id,
            "action": "duplicate",
            "match_type": "exact",
            "existing_id": existing["id"],
            "existing_title": existing["title"],
            "existing_file": existing["file_path"],
            "existing_created_at": existing["created_at"],
            "summary_for_agent": (
                f"⚠️ 重复（精确）：已收过同指纹内容（id={existing['id']}, "
                f"《{existing['title']}》，{existing['created_at'][:10]}）。未重复入库。"
            ),
            "_next_action": None,
        }
        print(json.dumps(out, ensure_ascii=False, default=str))
        return

    # ---- 查重 ② 模糊：token-set 重叠度 >= 0.8 ----
    fuzzy = find_similar_fingerprint(conn, fp, threshold=0.8)
    if fuzzy:
        conn.close()
        out = {
            "ok": True,
            "step": "save",
            "run_id": run_id,
            "action": "duplicate",
            "match_type": "fuzzy",
            "existing_id": fuzzy["id"],
            "existing_title": fuzzy["title"],
            "existing_file": fuzzy["file_path"],
            "existing_created_at": fuzzy["created_at"],
            "jaccard": fuzzy["jaccard"],
            "matched_tokens": fuzzy["matched_tokens"],
            "new_fingerprint": fp,
            "old_fingerprint": fuzzy["fingerprint"],
            "summary_for_agent": (
                f"⚠️ 重复（模糊）：库里已有类似主题（id={fuzzy['id']}, "
                f"《{fuzzy['title']}》，{fuzzy['created_at'][:10]}）\n"
                f"相似度 {int(fuzzy['jaccard']*100)}%, 共同关键词：{', '.join(fuzzy['matched_tokens'])}\n"
                f"未重复入库。如果新内容确有补充，可手动 merge 或调整指纹后再存。"
            ),
            "_next_action": None,
        }
        print(json.dumps(out, ensure_ascii=False, default=str))
        return

    # 评分
    scores = data.get("scores", {})
    review_enabled = 1 if data.get("score_total", 0) >= 90 else 0
    next_review_at = None
    if review_enabled:
        next_review_at = pipeline_io.add_days(REVIEW_INTERVALS[0])

    # 占位插入（拿 id 用于命名 md）
    cur = conn.execute("""
        INSERT INTO items (
            fingerprint, platform, title, uploader, source_url,
            file_path, tier, weight,
            score_total, score_density, score_actionable, score_uniqueness, score_reliability, score_reusability,
            summary_one_line, created_at,
            review_enabled, next_review_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        fp,
        data.get("platform", ""),
        data.get("title", ""),
        data.get("uploader", ""),
        data.get("url", ""),
        "",  # file_path 待会儿更新
        data.get("tier", "C"),
        data.get("weight", 0),
        data.get("score_total", 0),
        int(scores.get("density", 0)),
        int(scores.get("actionable", 0)),
        int(scores.get("uniqueness", 0)),
        int(scores.get("reliability", 0)),
        int(scores.get("reusability", 0)),
        data.get("summary_one_line", ""),
        now_iso(),
        review_enabled,
        next_review_at,
    ))
    item_id = cur.lastrowid

    # 写 md 文件
    md_path = write_md_file(item_id, data)
    conn.execute("UPDATE items SET file_path=? WHERE id=?", (str(md_path), item_id))

    # 反思问题
    for q in data.get("questions", []):
        conn.execute("INSERT INTO questions (item_id, question) VALUES (?, ?)", (item_id, q))

    # tags + categories
    upsert_tags_and_categories(conn, item_id, data)

    # item_projects（来自 classify 阶段的 project_relevance）
    upsert_item_projects(conn, item_id, data)

    conn.commit()
    conn.close()

    # ───── 写入向量库（RAG）─────
    # 失败不阻塞主流程，记录错误到返回 JSON
    rag_status = None
    try:
        import rag
        eng = rag.get_engine()
        # 拼检索文本
        item_for_rag = {
            "title": data.get("title", ""),
            "summary_one_line": data.get("summary_one_line", ""),
            "platform": data.get("platform", ""),
            "uploader": data.get("uploader", ""),
            "tags": data.get("tags", []),
            "categories": [c.get("name", "") for c in data.get("categories", [])],
            "essence": data.get("essence", ""),
            "content": data.get("content", ""),
        }
        text = eng.build_text(item_for_rag)
        metadata = {
            "title": data.get("title", ""),
            "platform": data.get("platform", ""),
            "uploader": data.get("uploader", ""),
            "tier": data.get("tier", "C"),
            "weight": data.get("weight", 0),
            "score_total": data.get("score_total", 0),
            "summary_one_line": data.get("summary_one_line", ""),
        }
        eng.add_or_update(item_id, text, metadata)
        rag_status = {"ok": True, "indexed_chars": len(text)}
    except Exception as e:
        rag_status = {"ok": False, "error": str(e)}

    # 输出给 agent
    tier = data.get("tier", "C")
    cats_str = ", ".join(f"{c['name']}({c.get('affinity', 0)})"
                         for c in data.get("categories", []))
    tags_str = " ".join(f"#{t}" for t in data.get("tags", [])[:6])
    essence = data.get("essence", "")
    questions = data.get("questions", [])

    # 项目关联段
    proj_rels = data.get("project_relevance", []) or []
    project_section = ""
    if proj_rels:
        # 按 relevance 倒序，最多展示 3 个
        sorted_rels = sorted(proj_rels, key=lambda x: -x.get("relevance", 0))[:3]
        lines = ["🎯 跟你正在做的项目"]
        for r in sorted_rels:
            mark = "⭐" if r.get("relevance", 0) >= 80 else ("💡" if r.get("relevance", 0) >= 60 else "·")
            lines.append(
                f"  {mark} {r['project']} ({r.get('relevance', 0)}) — {r.get('reason', '')}"
            )
        project_section = "\n".join(lines) + "\n"

    # 飞书消息：先放精华（最重要，让主人省去看视频），后放评分/反思/决策
    summary = (
        f"📌 {data.get('summary_one_line', '')}\n"
        f"🏷 {tags_str}\n"
        f"📂 {cats_str}\n"
        + (f"\n{project_section}" if project_section else "")
        + f"\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"💎 精华（{len(essence)} 字,原文 {data.get('content_chars', 0)} 字）\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"{essence}\n"
        f"\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📊 评分：{data.get('score_total', 0)}/100 ({tier} 级)\n"
        f"   密度{scores.get('density', 0)} / 可操作{scores.get('actionable', 0)} / "
        f"独特{scores.get('uniqueness', 0)} / 可靠{scores.get('reliability', 0)} / "
        f"复用{scores.get('reusability', 0)}\n"
        f"💡 {data.get('score_rationale', {}).get('strength', '')}\n"
        f"🤔 {data.get('score_rationale', {}).get('weakness', '')}\n"
        f"\n"
        f"🧠 反思 {len(questions)} 问：\n"
        + "\n".join(f"  {i+1}. {q}" for i, q in enumerate(questions))
        + f"\n\n🔁 "
        + (f"已沉淀（{tier} 级），{REVIEW_INTERVALS[0]} 天后第一次复习"
           if review_enabled else f"归入知识库（{tier} 级，不主动催）")
    )

    out = {
        "ok": True,
        "step": "save",
        "run_id": run_id,
        "action": "inserted",
        "id": item_id,
        "file_path": str(md_path),
        "tier": tier,
        "score_total": data.get("score_total", 0),
        "review_enabled": bool(review_enabled),
        "next_review_at": next_review_at,
        "rag": rag_status,
        "summary_for_agent": summary,
        "_next_action": None,
    }
    print(json.dumps(out, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
