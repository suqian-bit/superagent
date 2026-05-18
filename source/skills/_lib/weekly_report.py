#!/usr/bin/env python3
"""
weekly_report: 每周日北京时间 22:00 自动跑，汇总本周知识动态推送到飞书。

汇总维度：
  - 本周收录数（按 tier 分布）
  - Top N 高 weight 内容
  - 关注主题（按 category 聚合）
  - 新冒出的标签（本周首次出现 / 高频）
  - 复习情况（推送量、应答率、平均掌握度）
  - AI 观察（LLM 看本周数据给一段总结）

去重：用 push_history.mode='weekly' 记录，避免同周多发。
"""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, "/root/.hermes/profiles/knowledge/skills/_lib")
import pipeline_io
import review_pusher  # 复用 send_to_feishu


INDEX_DB = Path("/www/knowledge/index.db")


def fetch_week_stats(conn: sqlite3.Connection, days: int = 7) -> dict:
    """汇总过去 N 天的数据。"""
    now = pipeline_io.now_dt()
    week_ago_iso = (now - timedelta(days=days)).isoformat(timespec="seconds")
    today_str = now.strftime("%Y-%m-%d")
    week_start_str = (now - timedelta(days=days)).strftime("%Y-%m-%d")

    s = {
        "period_start": week_start_str,
        "period_end": today_str,
        "now": now.isoformat(timespec="seconds"),
    }

    # 本周收录
    items_this_week = conn.execute("""
        SELECT id, title, uploader, platform, tier, score_total, weight,
               summary_one_line, fingerprint
        FROM items
        WHERE created_at >= ? AND archived=0
        ORDER BY weight DESC, score_total DESC
    """, (week_ago_iso,)).fetchall()
    s["new_items"] = [dict(r) for r in items_this_week]
    s["new_count"] = len(items_this_week)

    # tier 分布
    s["tier_dist"] = {"A": 0, "B": 0, "C": 0}
    for it in s["new_items"]:
        s["tier_dist"][it["tier"]] = s["tier_dist"].get(it["tier"], 0) + 1
    s["avg_score"] = round(
        sum(i["score_total"] for i in s["new_items"]) / len(s["new_items"]), 1
    ) if s["new_items"] else 0

    # Top 3 高 weight
    s["top"] = s["new_items"][:3]

    # 本周新增 / 高频分类
    cat_rows = conn.execute("""
        SELECT c.name, COUNT(*) AS cnt
        FROM categories c
        JOIN item_categories ic ON c.id = ic.category_id
        JOIN items i ON ic.item_id = i.id
        WHERE i.created_at >= ? AND i.archived = 0
        GROUP BY c.name
        ORDER BY cnt DESC
    """, (week_ago_iso,)).fetchall()
    s["top_categories"] = [{"name": r[0], "count": r[1]} for r in cat_rows]

    # 本周高频标签（top 8）
    tag_rows = conn.execute("""
        SELECT t.name, COUNT(*) AS cnt
        FROM tags t
        JOIN item_tags it ON t.id = it.tag_id
        JOIN items i ON it.item_id = i.id
        WHERE i.created_at >= ? AND i.archived = 0
        GROUP BY t.name
        ORDER BY cnt DESC
        LIMIT 8
    """, (week_ago_iso,)).fetchall()
    s["top_tags"] = [{"name": r[0], "count": r[1]} for r in tag_rows]

    # 复习情况
    push_rows = conn.execute("""
        SELECT id, item_id, response_quality
        FROM push_history
        WHERE pushed_at >= ? AND mode != 'weekly'
    """, (week_ago_iso,)).fetchall()
    push_total = len(push_rows)
    answered = [p for p in push_rows if p[2] is not None]
    answered_qualities = [int(p[2]) for p in answered]
    s["review"] = {
        "push_total": push_total,
        "answered": len(answered),
        "answer_rate": round(len(answered) / push_total * 100, 0) if push_total else 0,
        "avg_quality": round(sum(answered_qualities) / len(answered_qualities), 1)
                       if answered_qualities else None,
        "skipped": push_total - len(answered),
    }

    # 全库掌握度（A/B 级 items 的平均 mastery）
    avg_mastery = conn.execute("""
        SELECT AVG(mastery) FROM items
        WHERE review_enabled=1 AND archived=0
    """).fetchone()[0]
    s["avg_mastery"] = round(avg_mastery or 0, 1)

    # 全库总数
    s["total_items"] = conn.execute(
        "SELECT COUNT(*) FROM items WHERE archived=0"
    ).fetchone()[0]

    # 积压：当前过期未答的 due
    s["due_now"] = conn.execute("""
        SELECT COUNT(*) FROM items
        WHERE review_enabled=1 AND archived=0 AND next_review_at <= ?
    """, (now.isoformat(timespec="seconds"),)).fetchone()[0]

    return s


def get_ai_insight(stats: dict) -> str:
    """让 DeepSeek 看本周数据给一段观察。"""
    # 给 LLM 一份精简摘要
    summary_for_llm = {
        "period": f"{stats['period_start']} ~ {stats['period_end']}",
        "new_count": stats["new_count"],
        "tier_dist": stats["tier_dist"],
        "avg_score": stats["avg_score"],
        "top_categories": stats["top_categories"][:5],
        "top_tags": [t["name"] for t in stats["top_tags"]],
        "review": stats["review"],
        "due_now": stats["due_now"],
        "top_3_items": [
            {"title": it["title"][:60], "tier": it["tier"], "weight": it["weight"]}
            for it in stats["top"]
        ],
    }

    if stats["new_count"] == 0:
        return "本周没有新收录。可能在忙，要不要刷点东西看看？"

    SYSTEM = "你是一个温和直接的知识管理顾问。一次只输出 3-4 句话，给主人本周的关键观察和建议。"
    prompt = (
        "下面是主人这周的知识库动态摘要，请用 3-4 句话点评：\n"
        "1. 这周关注的主题方向\n"
        "2. 有没有积压问题（推送了没回答 / due 堆积）\n"
        "3. 1 个具体的下周建议（比如'重点消化 id=X'、'减少收藏频率'、'某主题该收敛了'）\n\n"
        f"数据：\n{json.dumps(summary_for_llm, ensure_ascii=False, indent=2)}\n\n"
        "直接输出文字，不要 JSON，不要标题，不要列表符号。"
    )

    try:
        text = pipeline_io.call_deepseek(prompt, system=SYSTEM, temperature=0.4,
                                          max_tokens=400, json_mode=False)
        return text.strip()
    except Exception as e:
        return f"(AI 观察生成失败：{e})"


def render_text(stats: dict, insight: str) -> str:
    """把 stats 拼成飞书文本消息。"""
    lines = []
    lines.append("📊 本周知识周报")
    lines.append(f"({stats['period_start']} ~ {stats['period_end']})")
    lines.append("━" * 18)

    # 收录概览
    new_n = stats["new_count"]
    td = stats["tier_dist"]
    if new_n == 0:
        lines.append("\n📥 本周收录：0 条（本周没刷到/没发过新内容）")
    else:
        lines.append(f"\n📥 本周收录 {new_n} 条 · 平均 {stats['avg_score']}/100")
        tier_parts = []
        if td.get("A", 0): tier_parts.append(f"A 级 {td['A']}")
        if td.get("B", 0): tier_parts.append(f"B 级 {td['B']}")
        if td.get("C", 0): tier_parts.append(f"C 级 {td['C']}")
        lines.append("  " + " / ".join(tier_parts))

    # Top 3
    if stats["top"]:
        lines.append("\n🏆 Top 高价值")
        for i, it in enumerate(stats["top"], 1):
            t = it["title"][:42]
            lines.append(f"  {i}. [{it['weight']}·{it['tier']}] {t} (id={it['id']})")

    # 分类
    if stats["top_categories"]:
        lines.append("\n📂 关注主题")
        cats = " / ".join(f"{c['name']}({c['count']})" for c in stats["top_categories"][:5])
        lines.append(f"  {cats}")

    # 标签
    if stats["top_tags"]:
        lines.append("\n🏷 高频标签")
        tags = "  ".join(f"#{t['name']}" for t in stats["top_tags"][:6])
        lines.append(f"  {tags}")

    # 复习
    r = stats["review"]
    lines.append("\n🧠 复习情况")
    if r["push_total"] == 0:
        lines.append("  本周没推送复习（库里没到期内容）")
    else:
        rate = r["answer_rate"]
        lines.append(f"  推送 {r['push_total']} 次 · 回答 {r['answered']} 次 ({rate}%)")
        if r["avg_quality"] is not None:
            lines.append(f"  平均质量 {r['avg_quality']}/5 · 库总掌握度 {stats['avg_mastery']}/100")
        if r["skipped"] > 0:
            lines.append(f"  ⚠️ {r['skipped']} 条没回答")
    if stats["due_now"] > 0:
        lines.append(f"  ⏰ 当前积压 {stats['due_now']} 条待复习")

    # AI 观察
    lines.append(f"\n💡 AI 观察")
    lines.append("  " + insight.replace("\n", "\n  "))

    lines.append("\n" + "━" * 18)
    lines.append(f"全库 {stats['total_items']} 条 · 在飞书发 \"看看积压\" 看待办")

    return "\n".join(lines)


def already_pushed_this_week(conn: sqlite3.Connection) -> bool:
    """看这周是否已经推过周报（避免人为再次触发重复发）。"""
    now = pipeline_io.now_dt()
    # 一周前（不是 7 天前 12:00，而是上周日的 22:00 之后到现在）
    week_ago = (now - timedelta(days=6)).isoformat(timespec="seconds")
    row = conn.execute("""
        SELECT id FROM push_history
        WHERE mode='weekly' AND pushed_at >= ?
        ORDER BY pushed_at DESC LIMIT 1
    """, (week_ago,)).fetchone()
    return row is not None


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="即使本周已发也强制再发一次")
    ap.add_argument("--dry-run", action="store_true", help="只输出文案不推送")
    ap.add_argument("--days", type=int, default=7, help="统计回看天数（默认 7）")
    args = ap.parse_args()

    if not INDEX_DB.exists():
        print(json.dumps({"ok": False, "reason": "no index.db"}, ensure_ascii=False))
        return 0

    conn = sqlite3.connect(INDEX_DB)
    conn.row_factory = sqlite3.Row

    if not args.force and not args.dry_run and already_pushed_this_week(conn):
        print(json.dumps({"ok": True, "skipped": True,
                          "reason": "本周已推过周报，用 --force 重发"}, ensure_ascii=False))
        return 0

    stats = fetch_week_stats(conn, days=args.days)
    insight = get_ai_insight(stats)
    text = render_text(stats, insight)

    if args.dry_run:
        print(text)
        print("\n---\n[dry-run, 未推送]")
        conn.close()
        return 0

    sent = review_pusher.send_to_feishu(text)

    if sent:
        # 用 item_id=0 占位（周报跟具体 item 无关）
        # 但表里 item_id 是 NOT NULL FK，我们用一个占位 item_id（先检查表约束）
        try:
            conn.execute("""
                INSERT INTO push_history (item_id, question, mode, pushed_at)
                VALUES (0, ?, 'weekly', ?)
            """, (text[:200], pipeline_io.now_iso()))
            conn.commit()
        except sqlite3.IntegrityError:
            # 如果 0 不允许（FK），用最新 item_id
            row = conn.execute("SELECT MAX(id) FROM items").fetchone()
            anchor = row[0] if row and row[0] else 1
            conn.execute("""
                INSERT INTO push_history (item_id, question, mode, pushed_at)
                VALUES (?, ?, 'weekly', ?)
            """, (anchor, text[:200], pipeline_io.now_iso()))
            conn.commit()

    print(json.dumps({
        "ok": sent,
        "sent": sent,
        "period": f"{stats['period_start']} ~ {stats['period_end']}",
        "new_count": stats["new_count"],
        "text_chars": len(text),
    }, ensure_ascii=False))
    conn.close()
    return 0 if sent else 1


if __name__ == "__main__":
    sys.exit(main() or 0)
