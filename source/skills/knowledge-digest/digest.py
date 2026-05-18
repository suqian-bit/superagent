#!/usr/bin/env python3
"""
digest (v2): 查询 + 复习反馈，操作 /www/knowledge/index.db。

子命令:
  digest pending-review        最近 24h 推过但未回答的复习题（agent 看主人回 1-5 时先查这个）
  digest review <id> <q1-5>    主人答完，按 SM-2 更新间隔
  digest due [--limit N]       今天该推的（cron 用）
  digest list [--limit N]      最近收录
  digest stats                 全库统计
  digest by-tag <tag>          按标签找
  digest by-cat <cat>          按分类找
  digest find <fingerprint>    精确指纹查重

时间一律北京时间 ISO，例：2026-05-12T20:45:13+08:00
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, "/root/.hermes/profiles/knowledge/skills/_lib")
import pipeline_io


INDEX_DB = Path("/www/knowledge/index.db")

# SM-2 风格的间隔表
REVIEW_INTERVALS = [1, 3, 7, 14, 30]  # 天


def connect() -> sqlite3.Connection:
    if not INDEX_DB.exists():
        die(f"知识库还是空的，没收过任何内容（{INDEX_DB} 不存在）")
    conn = sqlite3.connect(INDEX_DB)
    conn.row_factory = sqlite3.Row
    return conn


def die(msg: str):
    print(json.dumps({"ok": False, "error": msg}, ensure_ascii=False))
    sys.exit(1)


# ─────────────────────────────────────────────────────────
# pending-review: agent 看到主人疑似回复时查"待回答的复习"
# ─────────────────────────────────────────────────────────

def cmd_pending_review(args):
    conn = connect()
    # 查找：最近 24 小时内推送过 + response_quality 还是 NULL（没回答）
    cutoff = (pipeline_io.now_dt() - timedelta(hours=24)).isoformat(timespec="seconds")

    rows = conn.execute("""
        SELECT p.id AS push_id, p.item_id, p.mode, p.pushed_at, p.question,
               i.title, i.tier, i.score_total, i.uploader,
               i.review_count, i.mastery, i.next_review_at
        FROM push_history p
        JOIN items i ON p.item_id = i.id
        WHERE p.response_quality IS NULL
          AND p.pushed_at >= ?
        ORDER BY p.pushed_at DESC
    """, (cutoff,)).fetchall()

    items = [dict(r) for r in rows]
    print(json.dumps({
        "ok": True,
        "count": len(items),
        "pending": items,
        "hint": (
            "如果 count > 0，主人回复 1-5 数字或文字时应该调 "
            "`digest review <item_id> <q>` 更新最新一条 pending 的复习。"
            if items else
            "没有待回答的复习题。主人当前消息不是复习应答。"
        ),
    }, ensure_ascii=False, default=str))


# ─────────────────────────────────────────────────────────
# review: SM-2 更新
# ─────────────────────────────────────────────────────────

def cmd_review(args):
    conn = connect()
    item = conn.execute("SELECT * FROM items WHERE id=?", (args.id,)).fetchone()
    if not item:
        die(f"item id={args.id} 不存在")

    q = max(1, min(5, args.quality))
    ease = item["ease"] or 2.5
    idx = item["review_idx"] or 0
    mastery = item["mastery"] or 0
    review_count = item["review_count"] or 0

    if q >= 4:
        ease = min(3.0, ease + 0.15)
        idx = min(idx + 1, len(REVIEW_INTERVALS) - 1)
        mastery = min(100, mastery + 15)
        verdict = "记得很好"
    elif q == 3:
        ease = min(3.0, ease + 0.05)
        # idx 不变
        mastery = min(100, mastery + 5)
        verdict = "勉强记得"
    else:  # q=1, q=2
        ease = max(1.3, ease - 0.2)
        idx = max(0, idx - 1)
        mastery = max(0, mastery - 10)
        verdict = "忘了"

    base_days = REVIEW_INTERVALS[idx]
    actual_days = base_days * (ease / 2.5)
    next_at = pipeline_io.add_days(actual_days)

    conn.execute("""
        UPDATE items SET
            review_idx=?, ease=?, review_count=review_count+1,
            last_reviewed_at=?, next_review_at=?, mastery=?
        WHERE id=?
    """, (idx, round(ease, 2), pipeline_io.now_iso(), next_at, mastery, args.id))

    # 找最近一条该 item 的 pending push_history，标记 response
    conn.execute("""
        UPDATE push_history SET response_at=?, response_quality=?
        WHERE id IN (
            SELECT id FROM push_history
            WHERE item_id=? AND response_quality IS NULL
            ORDER BY pushed_at DESC LIMIT 1
        )
    """, (pipeline_io.now_iso(), q, args.id))

    conn.commit()
    conn.close()

    print(json.dumps({
        "ok": True,
        "item_id": args.id,
        "quality": q,
        "verdict": verdict,
        "review_count_now": review_count + 1,
        "ease": round(ease, 2),
        "mastery": mastery,
        "next_review_at": next_at,
        "next_in_days": round(actual_days, 1),
        "summary_for_agent": (
            f"✅ 已记录复习反馈：{verdict}（{q}/5）。\n"
            f"   累计复习 {review_count + 1} 次，掌握度 {mastery}/100，"
            f"下次推送：{next_at[:16]}（约 {round(actual_days)} 天后）"
        ),
    }, ensure_ascii=False, default=str))


# ─────────────────────────────────────────────────────────
# due: 列出该推送的
# ─────────────────────────────────────────────────────────

def cmd_due(args):
    conn = connect()
    rows = conn.execute("""
        SELECT id, title, uploader, tier, score_total, summary_one_line,
               next_review_at, review_count, mastery, file_path
        FROM items
        WHERE review_enabled=1 AND archived=0 AND next_review_at<=?
        ORDER BY
          CASE tier WHEN 'A' THEN 0 WHEN 'B' THEN 1 ELSE 2 END,
          next_review_at ASC
        LIMIT ?
    """, (pipeline_io.now_iso(), args.limit)).fetchall()
    items = [dict(r) for r in rows]
    print(json.dumps({"ok": True, "count": len(items), "items": items},
                     ensure_ascii=False, default=str))


# ─────────────────────────────────────────────────────────
# list / stats / by-tag / by-cat / find
# ─────────────────────────────────────────────────────────

def cmd_list(args):
    conn = connect()
    rows = conn.execute("""
        SELECT id, title, uploader, platform, tier, score_total, weight,
               summary_one_line, review_enabled, next_review_at, review_count,
               mastery, created_at
        FROM items
        WHERE archived=0
        ORDER BY created_at DESC
        LIMIT ?
    """, (args.limit,)).fetchall()
    print(json.dumps({"ok": True, "count": len(rows),
                      "items": [dict(r) for r in rows]},
                     ensure_ascii=False, default=str))


def cmd_stats(args):
    conn = connect()
    s = {}
    s["total"] = conn.execute("SELECT COUNT(*) FROM items WHERE archived=0").fetchone()[0]
    s["by_tier"] = dict(conn.execute(
        "SELECT tier, COUNT(*) FROM items WHERE archived=0 GROUP BY tier"
    ).fetchall())
    s["by_platform"] = dict(conn.execute(
        "SELECT platform, COUNT(*) FROM items WHERE archived=0 GROUP BY platform"
    ).fetchall())
    s["avg_score"] = round(conn.execute(
        "SELECT AVG(score_total) FROM items WHERE archived=0"
    ).fetchone()[0] or 0, 1)
    s["due_now"] = conn.execute(
        "SELECT COUNT(*) FROM items WHERE review_enabled=1 AND archived=0 AND next_review_at<=?",
        (pipeline_io.now_iso(),),
    ).fetchone()[0]
    s["review_enabled_count"] = conn.execute(
        "SELECT COUNT(*) FROM items WHERE review_enabled=1 AND archived=0"
    ).fetchone()[0]
    s["top_tags"] = [
        {"name": r[0], "count": r[1]}
        for r in conn.execute(
            "SELECT name, count FROM tags ORDER BY count DESC LIMIT 10"
        ).fetchall()
    ]
    s["categories"] = [
        {"name": r[0], "count": r[1]}
        for r in conn.execute(
            "SELECT name, count FROM categories ORDER BY count DESC"
        ).fetchall()
    ]
    s["recent_pushes"] = conn.execute(
        "SELECT COUNT(*) FROM push_history WHERE pushed_at>=?",
        ((pipeline_io.now_dt() - timedelta(days=7)).isoformat(timespec="seconds"),),
    ).fetchone()[0]

    print(json.dumps({"ok": True, "stats": s}, ensure_ascii=False, default=str))


def cmd_by_tag(args):
    conn = connect()
    tag = args.tag
    rows = conn.execute("""
        SELECT i.id, i.title, i.tier, i.score_total, i.summary_one_line, i.created_at
        FROM items i
        JOIN item_tags it ON i.id=it.item_id
        JOIN tags t ON it.tag_id=t.id
        WHERE t.name=? AND i.archived=0
        ORDER BY i.created_at DESC
    """, (tag,)).fetchall()
    print(json.dumps({"ok": True, "tag": tag, "count": len(rows),
                      "items": [dict(r) for r in rows]},
                     ensure_ascii=False, default=str))


def cmd_by_cat(args):
    conn = connect()
    cat = args.category
    min_aff = args.min_affinity
    rows = conn.execute("""
        SELECT i.id, i.title, i.tier, i.score_total, i.summary_one_line,
               ic.affinity, i.created_at
        FROM items i
        JOIN item_categories ic ON i.id=ic.item_id
        JOIN categories c ON ic.category_id=c.id
        WHERE c.name=? AND ic.affinity>=? AND i.archived=0
        ORDER BY ic.affinity DESC, i.created_at DESC
    """, (cat, min_aff)).fetchall()
    print(json.dumps({"ok": True, "category": cat, "min_affinity": min_aff,
                      "count": len(rows), "items": [dict(r) for r in rows]},
                     ensure_ascii=False, default=str))


def cmd_find(args):
    conn = connect()
    row = conn.execute("""
        SELECT id, title, uploader, tier, score_total, source_url, file_path,
               created_at, review_count, mastery, next_review_at
        FROM items WHERE fingerprint=?
    """, (args.fingerprint,)).fetchone()
    if not row:
        print(json.dumps({"ok": True, "found": False}, ensure_ascii=False))
    else:
        print(json.dumps({"ok": True, "found": True, **dict(row)},
                         ensure_ascii=False, default=str))


# ─────────────────────────────────────────────────────────
# promote / demote / archive：手动调整 tier 和复习状态
# ─────────────────────────────────────────────────────────

def cmd_promote(args):
    """
    把 item 提级。默认推到 A 级 + 启动复习（明天复习）。
      digest promote 5                  # 推到 A
      digest promote 5 --tier B         # 推到 B
      digest promote 5 --weight 90      # 同时调权重
    """
    conn = connect()
    item = conn.execute("SELECT * FROM items WHERE id=?", (args.id,)).fetchone()
    if not item:
        die(f"item id={args.id} 不存在")

    new_tier = args.tier or "A"
    new_weight = args.weight if args.weight is not None else (95 if new_tier == "A" else (85 if new_tier == "B" else 70))

    # A/B 自动启用复习；C 不启动
    enable_review = 1 if new_tier in ("A", "B") else 0
    next_review = pipeline_io.add_days(1) if enable_review else None

    conn.execute("""
        UPDATE items SET tier=?, weight=?, review_enabled=?,
                         next_review_at=COALESCE(?, next_review_at),
                         archived=0
        WHERE id=?
    """, (new_tier, new_weight, enable_review, next_review, args.id))
    conn.commit()
    conn.close()

    print(json.dumps({
        "ok": True,
        "id": args.id,
        "old_tier": item["tier"],
        "new_tier": new_tier,
        "old_weight": item["weight"],
        "new_weight": new_weight,
        "review_enabled": bool(enable_review),
        "next_review_at": next_review,
        "summary_for_agent": (
            f"✅ 已提级：id={args.id}《{item['title']}》"
            f" {item['tier']}({item['weight']}) → {new_tier}({new_weight})"
            + (f"，{next_review[:16] if next_review else ''} 开始复习" if enable_review else "")
        ),
    }, ensure_ascii=False, default=str))


def cmd_demote(args):
    """
    把 item 降级。默认推到 C 级 + 停止复习。
      digest demote 5                   # 推到 C，不再推送
      digest demote 5 --tier B          # 推到 B
      digest demote 5 --archive         # 直接 archive（彻底归档，从查询/推送中消失）
    """
    conn = connect()
    item = conn.execute("SELECT * FROM items WHERE id=?", (args.id,)).fetchone()
    if not item:
        die(f"item id={args.id} 不存在")

    if args.archive:
        conn.execute("UPDATE items SET archived=1, review_enabled=0 WHERE id=?", (args.id,))
        conn.commit()
        conn.close()
        print(json.dumps({
            "ok": True, "id": args.id, "action": "archived",
            "summary_for_agent": f"📦 已归档：id={args.id}《{item['title']}》（不再出现在查询和推送里）",
        }, ensure_ascii=False, default=str))
        return

    new_tier = args.tier or "C"
    new_weight = args.weight if args.weight is not None else (75 if new_tier == "B" else 40)
    enable_review = 1 if new_tier in ("A", "B") else 0

    conn.execute("""
        UPDATE items SET tier=?, weight=?, review_enabled=? WHERE id=?
    """, (new_tier, new_weight, enable_review, args.id))
    conn.commit()
    conn.close()

    print(json.dumps({
        "ok": True,
        "id": args.id,
        "old_tier": item["tier"],
        "new_tier": new_tier,
        "old_weight": item["weight"],
        "new_weight": new_weight,
        "review_enabled": bool(enable_review),
        "summary_for_agent": (
            f"⬇️ 已降级：id={args.id}《{item['title']}》"
            f" {item['tier']}({item['weight']}) → {new_tier}({new_weight})"
            + ("（继续复习）" if enable_review else "（不再主动推送）")
        ),
    }, ensure_ascii=False, default=str))


def cmd_impact(args):
    """
    场景化检索：主人说"我现在要做 X / 卡在 Y"，找库里相关内容。

    实现：
      1. 调 LLM 把场景拆成关键词 + 评估意图
      2. 用关键词在 title/summary/tags 做 LIKE 搜
      3. 用 item_projects 找相关项目下的高 weight 内容
      4. 返回 top N 相关 items + 推荐使用方式
    """
    scenario = args.scenario
    conn = connect()

    # 1. 先看场景是否提到某个项目名
    proj_rows = conn.execute("SELECT name, display_name FROM projects WHERE archived=0").fetchall()
    project_match = None
    for p in proj_rows:
        if p["name"].lower() in scenario.lower() or (p["display_name"] and p["display_name"] in scenario):
            project_match = dict(p)
            break

    # 2. 用 LLM 拆关键词 + 项目猜测
    KW_SYSTEM = "你是搜索助手。你只输出 JSON。"
    KW_PROMPT = f"""主人说："{scenario}"

库里已有的项目：
{json.dumps([dict(r) for r in proj_rows], ensure_ascii=False)}

请你：
1. 判断他说的事**最可能跟哪个项目相关**（如果有）
2. 抽 3-5 个最关键的中英文检索关键词

返回 JSON：
{{
  "matched_project": "quant" 或 null,
  "keywords": ["训练数据", "回测", "时间分离", "backtest"]
}}
"""

    try:
        reply = pipeline_io.call_deepseek(KW_PROMPT, system=KW_SYSTEM,
                                           temperature=0.0, max_tokens=300)
        parsed = pipeline_io.parse_json_reply(reply)
    except Exception as e:
        die(f"LLM 关键词提取失败: {e}")

    matched_project = parsed.get("matched_project") or (project_match["name"] if project_match else None)
    keywords = parsed.get("keywords", [])

    # 3. 多渠道检索
    candidates = {}  # item_id -> {score, item, reasons}

    # 3a. 项目关联（如果有 matched_project）
    if matched_project:
        rows = conn.execute("""
            SELECT i.id, i.title, i.tier, i.score_total, i.weight, i.summary_one_line,
                   i.file_path, ip.relevance, ip.reason
            FROM item_projects ip
            JOIN items i ON i.id=ip.item_id
            JOIN projects p ON p.id=ip.project_id
            WHERE p.name=? AND i.archived=0 AND ip.relevance>=60
            ORDER BY ip.relevance DESC, i.weight DESC
            LIMIT 15
        """, (matched_project,)).fetchall()
        for r in rows:
            d = dict(r)
            iid = d["id"]
            candidates.setdefault(iid, {"item": d, "hits": [], "score": 0})
            candidates[iid]["hits"].append(f"项目 {matched_project} 关联 {d['relevance']}")
            candidates[iid]["score"] += d["relevance"] * 1.5  # 项目关联权重高

    # 3b. 关键词在 title/summary/tags 中 LIKE 搜
    for kw in keywords:
        like = f"%{kw}%"
        rows = conn.execute("""
            SELECT i.id, i.title, i.tier, i.score_total, i.weight, i.summary_one_line, i.file_path
            FROM items i
            WHERE i.archived=0 AND (
              i.title LIKE ? OR i.summary_one_line LIKE ?
              OR EXISTS (
                SELECT 1 FROM item_tags it JOIN tags t ON t.id=it.tag_id
                WHERE it.item_id=i.id AND t.name LIKE ?
              )
            )
            LIMIT 10
        """, (like, like, like)).fetchall()
        for r in rows:
            d = dict(r)
            iid = d["id"]
            candidates.setdefault(iid, {"item": d, "hits": [], "score": 0})
            candidates[iid]["hits"].append(f"关键词命中 `{kw}`")
            candidates[iid]["score"] += 30

    # 4. 排序 + 取 top
    if not candidates:
        print(json.dumps({
            "ok": True,
            "scenario": scenario,
            "matched_project": matched_project,
            "keywords": keywords,
            "results": [],
            "summary_for_agent": f"库里**没有**跟「{scenario}」相关的内容。可能是：\n1. 主题没收过\n2. 关键词没命中（试试不同说法）\n3. 这是新方向，要不要建项目档案？",
        }, ensure_ascii=False))
        return

    sorted_results = sorted(candidates.values(), key=lambda x: -x["score"])[:8]

    # 5. 构造给主人的回复
    lines = [f"🔍 跟「{scenario}」相关的库存："]
    if matched_project:
        lines.append(f"   （识别到项目：{matched_project}）")
    lines.append("")
    for i, c in enumerate(sorted_results, 1):
        it = c["item"]
        hits_str = " / ".join(c["hits"][:2])
        title = it["title"][:50]
        lines.append(f"{i}. [{it['tier']}·{it['weight']}] id={it['id']} {title}")
        lines.append(f"   {it.get('summary_one_line', '')[:80]}")
        lines.append(f"   ↳ {hits_str}")
        lines.append("")

    lines.append("💡 想看哪条详细内容？回我「看 id=X」")

    print(json.dumps({
        "ok": True,
        "scenario": scenario,
        "matched_project": matched_project,
        "keywords": keywords,
        "results": [{"item_id": c["item"]["id"], "title": c["item"]["title"],
                     "score": c["score"], "hits": c["hits"]} for c in sorted_results],
        "summary_for_agent": "\n".join(lines),
    }, ensure_ascii=False))


def cmd_view(args):
    """看一条 item 的详细信息（md 文件路径 + 全字段）"""
    conn = connect()
    row = conn.execute("""
        SELECT * FROM items WHERE id=?
    """, (args.id,)).fetchone()
    if not row:
        die(f"item id={args.id} 不存在")

    data = dict(row)

    # 拼上 tags 和 categories
    tags = [r[0] for r in conn.execute("""
        SELECT t.name FROM tags t JOIN item_tags it ON t.id=it.tag_id WHERE it.item_id=?
    """, (args.id,)).fetchall()]

    cats = [
        {"name": r[0], "affinity": r[1]}
        for r in conn.execute("""
            SELECT c.name, ic.affinity FROM categories c
            JOIN item_categories ic ON c.id=ic.category_id
            WHERE ic.item_id=? ORDER BY ic.affinity DESC
        """, (args.id,)).fetchall()
    ]

    data["tags"] = tags
    data["categories"] = cats

    print(json.dumps({"ok": True, "item": data}, ensure_ascii=False, default=str))


# ─────────────────────────────────────────────────────────
# RAG 检索类（search / similar / ask / reindex）
# ─────────────────────────────────────────────────────────

def _load_rag():
    """延迟加载 rag.py（首次约 2-3 秒装模型）"""
    import sys as _sys
    if "/root/.hermes/profiles/knowledge/skills/_lib" not in _sys.path:
        _sys.path.insert(0, "/root/.hermes/profiles/knowledge/skills/_lib")
    import rag
    return rag.get_engine()


def cmd_search(args):
    """语义检索 - 返回最相似的 Top K 条 item。"""
    try:
        eng = _load_rag()
    except Exception as e:
        die(f"RAG 引擎加载失败: {e}")

    results = eng.search(args.query, top_k=args.limit)

    if not results:
        print(json.dumps({
            "ok": True,
            "query": args.query,
            "results": [],
            "summary_for_agent": f"语义检索「{args.query}」无结果。库里可能还没有相关内容。",
        }, ensure_ascii=False))
        return

    # 同步从 SQLite 补充字段（review_count / mastery / created_at 等）
    conn = connect()
    enriched = []
    for r in results:
        row = conn.execute("""
            SELECT created_at, review_count, mastery, next_review_at, source_url
            FROM items WHERE id=?
        """, (r["item_id"],)).fetchone()
        if row:
            r["created_at"] = row["created_at"]
            r["review_count"] = row["review_count"]
            r["mastery"] = row["mastery"]
            r["source_url"] = row["source_url"]
        enriched.append(r)
    conn.close()

    # 拼给 agent 的回复
    lines = [f"🔍 语义检索「{args.query}」找到 {len(enriched)} 条："]
    for i, r in enumerate(enriched, 1):
        title = r["title"][:50]
        lines.append(
            f"{i}. [{r['tier']}·{r['weight']}] id={r['item_id']} "
            f"《{title}》(相似度 {r['score']:.2f})"
        )
        if r.get("summary_one_line"):
            lines.append(f"   {r['summary_one_line'][:80]}")

    lines.append("\n💡 想看哪条详细内容？回我「看 id=X」")

    print(json.dumps({
        "ok": True,
        "query": args.query,
        "count": len(enriched),
        "results": enriched,
        "summary_for_agent": "\n".join(lines),
    }, ensure_ascii=False, default=str))


def cmd_similar(args):
    """找跟 id=X 最相似的 N 条（同主题/同类型的关联内容）"""
    try:
        eng = _load_rag()
    except Exception as e:
        die(f"RAG 引擎加载失败: {e}")

    conn = connect()
    src = conn.execute(
        "SELECT id, title FROM items WHERE id=?", (args.id,)
    ).fetchone()
    if not src:
        conn.close()
        die(f"item id={args.id} 不存在")

    results = eng.similar(args.id, top_k=args.limit)
    conn.close()

    if not results:
        print(json.dumps({
            "ok": True,
            "item_id": args.id,
            "results": [],
            "summary_for_agent": f"库里没有跟 id={args.id} 相似的其他内容。",
        }, ensure_ascii=False))
        return

    lines = [
        f"🔗 跟 id={args.id}《{src['title'][:40]}》相似的内容："
    ]
    for i, r in enumerate(results, 1):
        title = r["title"][:50]
        lines.append(
            f"{i}. [{r['tier']}·{r['weight']}] id={r['item_id']} "
            f"《{title}》(相似度 {r['score']:.2f})"
        )

    print(json.dumps({
        "ok": True,
        "item_id": args.id,
        "source_title": src["title"],
        "results": results,
        "summary_for_agent": "\n".join(lines),
    }, ensure_ascii=False, default=str))


def cmd_ask(args):
    """RAG 问答 - 检索 Top K + DeepSeek 合成回答 + 标注来源 id。"""
    try:
        eng = _load_rag()
    except Exception as e:
        die(f"RAG 引擎加载失败: {e}")

    results = eng.search(args.question, top_k=args.limit)

    if not results:
        print(json.dumps({
            "ok": True,
            "question": args.question,
            "answer": "库里没有跟这个问题相关的内容，没法基于你的库存回答。",
            "sources": [],
            "summary_for_agent": f"❓ 你问：「{args.question}」\n\n库里没找到相关内容。可以试试用更具体的关键词，或者这条还没被收录。",
        }, ensure_ascii=False))
        return

    # 拼上下文，去读 md 文件抽精华段（信息更丰富）
    from pathlib import Path as _P
    import re as _re
    contexts = []
    sources = []
    for r in results:
        # 读 md，抽精华段
        try:
            conn = connect()
            row = conn.execute(
                "SELECT file_path FROM items WHERE id=?",
                (r["item_id"],),
            ).fetchone()
            conn.close()
            if row and row["file_path"]:
                md = _P(row["file_path"]).read_text(encoding="utf-8", errors="ignore")
                m = _re.search(
                    r"^## 精华\s*\n(.*?)(?=^## |\Z)", md,
                    _re.MULTILINE | _re.DOTALL,
                )
                essence = m.group(1).strip() if m else r["snippet"]
            else:
                essence = r["snippet"]
        except Exception:
            essence = r["snippet"]

        contexts.append(
            f"[来源 id={r['item_id']} 《{r['title']}》"
            f"（相似度 {r['score']:.2f}）]\n{essence[:1500]}"
        )
        sources.append({
            "item_id": r["item_id"],
            "title": r["title"],
            "score": r["score"],
        })

    context_text = "\n\n---\n\n".join(contexts)

    SYSTEM = (
        "你是一个个人知识库问答助手。基于主人的知识库内容回答他的问题。"
        "规则：\n"
        "1. 只用提供的内容回答，不要编造\n"
        "2. 回答里标注信息来源（用 id=X 引用）\n"
        "3. 如果库里内容不足以回答，坦诚说\n"
        "4. 简洁直接，用主人的语气\n"
    )

    PROMPT = f"""主人问：「{args.question}」

库里相关内容（按相似度排序）：

{context_text}

请基于上述内容回答。回答末尾用 `参考：id=X, id=Y` 标注用到的来源。"""

    try:
        answer = pipeline_io.call_deepseek(
            PROMPT, system=SYSTEM, temperature=0.3,
            max_tokens=1500, json_mode=False,
        ).strip()
    except Exception as e:
        die(f"DeepSeek 调用失败: {e}")

    print(json.dumps({
        "ok": True,
        "question": args.question,
        "answer": answer,
        "sources": sources,
        "summary_for_agent": f"❓ {args.question}\n\n{answer}",
    }, ensure_ascii=False, default=str))


def cmd_reindex(args):
    """全量重建向量库（首次跑 / DB 重建用）"""
    try:
        eng = _load_rag()
    except Exception as e:
        die(f"RAG 引擎加载失败: {e}")

    result = eng.reindex_from_db(verbose=False)
    if result.get("ok"):
        result["summary_for_agent"] = (
            f"✅ 向量库重建完成：索引 {result.get('indexed', 0)} 条，"
            f"跳过 {result.get('skipped', 0)} 条。"
        )
    else:
        result["summary_for_agent"] = f"❌ 重建失败：{result.get('error', '?')}"
    print(json.dumps(result, ensure_ascii=False, default=str))


# ─────────────────────────────────────────────────────────
# main
# ─────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd")

    sp.add_parser("pending-review")

    p_review = sp.add_parser("review")
    p_review.add_argument("id", type=int)
    p_review.add_argument("quality", type=int, choices=[1, 2, 3, 4, 5])

    p_due = sp.add_parser("due")
    p_due.add_argument("--limit", type=int, default=5)

    p_list = sp.add_parser("list")
    p_list.add_argument("--limit", type=int, default=20)

    sp.add_parser("stats")

    p_tag = sp.add_parser("by-tag")
    p_tag.add_argument("tag")

    p_cat = sp.add_parser("by-cat")
    p_cat.add_argument("category")
    p_cat.add_argument("--min-affinity", type=int, default=50)

    p_find = sp.add_parser("find")
    p_find.add_argument("fingerprint")

    p_promote = sp.add_parser("promote", help="提级（默认推到 A 启动复习）")
    p_promote.add_argument("id", type=int)
    p_promote.add_argument("--tier", choices=["A", "B", "C"])
    p_promote.add_argument("--weight", type=int)

    p_demote = sp.add_parser("demote", help="降级（默认推到 C 停止推送）")
    p_demote.add_argument("id", type=int)
    p_demote.add_argument("--tier", choices=["A", "B", "C"])
    p_demote.add_argument("--weight", type=int)
    p_demote.add_argument("--archive", action="store_true", help="彻底归档（archived=1）")

    p_view = sp.add_parser("view", help="看一条 item 的全部字段 + tags/categories")
    p_view.add_argument("id", type=int)

    p_impact = sp.add_parser("impact", help="场景化检索：'我现在要做 X，库里有什么能用？'")
    p_impact.add_argument("scenario", help="自然语言场景描述")

    # ───── RAG 检索类 ─────
    p_search = sp.add_parser("search", help="语义检索：模糊回忆型 '我之前看过 X'")
    p_search.add_argument("query", help="检索关键词或描述")
    p_search.add_argument("--limit", type=int, default=5)

    p_similar = sp.add_parser("similar", help="找跟 id=X 最相似的 N 条")
    p_similar.add_argument("id", type=int)
    p_similar.add_argument("--limit", type=int, default=5)

    p_ask = sp.add_parser("ask", help="RAG 问答：检索 + LLM 合成回答")
    p_ask.add_argument("question", help="具体问题")
    p_ask.add_argument("--limit", type=int, default=5, help="检索 Top K（默认 5）")

    p_reindex = sp.add_parser("reindex", help="全量重建向量库")

    args = ap.parse_args()
    if not args.cmd:
        ap.print_help()
        sys.exit(1)

    handlers = {
        "pending-review": cmd_pending_review,
        "review": cmd_review,
        "due": cmd_due,
        "list": cmd_list,
        "stats": cmd_stats,
        "by-tag": cmd_by_tag,
        "by-cat": cmd_by_cat,
        "find": cmd_find,
        "promote": cmd_promote,
        "demote": cmd_demote,
        "view": cmd_view,
        "impact": cmd_impact,
        # RAG
        "search": cmd_search,
        "similar": cmd_similar,
        "ask": cmd_ask,
        "reindex": cmd_reindex,
    }
    handlers[args.cmd](args)


if __name__ == "__main__":
    main()
