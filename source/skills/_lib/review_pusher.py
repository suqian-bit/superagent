#!/usr/bin/env python3
"""
review_pusher: cron 任务，扫描 due 的复习项，挑一条主动推送给主人飞书。

逻辑：
1. 拿当前北京时间，查 next_review_at <= now 的 items（review_enabled=1, archived=0）
2. 按优先级排序：最久没复习的 + tier A 优先
3. 挑最该问的那条
4. 用 4 种模式之一构造问题（灵魂提问 / 联想 / 应用 / 淘汰）
5. 通过 Hermes gateway 的 "Send to chat" 接口推到飞书

每天跑 3 次（早 9 / 午 13 / 晚 21）。
跑完留 push_history 记录，避免一天推多次同一条。
"""
from __future__ import annotations

import json
import os
import random
import sqlite3
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, "/root/.hermes/profiles/knowledge/skills/_lib")
import pipeline_io


INDEX_DB = Path("/www/knowledge/index.db")

# 主人飞书 open_id（用 knowledge profile 的 FEISHU_ALLOWED_USERS）
FEISHU_OPEN_ID = "ou_edf99c843d614af252238810fbbf0697"

# 推送历史表（避免一天重复推同一条）
DDL_PUSH_HISTORY = """
CREATE TABLE IF NOT EXISTS push_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id INTEGER NOT NULL,
    question TEXT NOT NULL,
    mode TEXT NOT NULL,
    pushed_at TEXT NOT NULL,
    response_at TEXT,
    response_quality INTEGER,
    FOREIGN KEY (item_id) REFERENCES items(id)
);
CREATE INDEX IF NOT EXISTS idx_push_item ON push_history(item_id, pushed_at);
"""


def pick_due_item(conn: sqlite3.Connection) -> dict | None:
    """挑一条最该复习的。"""
    now = pipeline_io.now_iso()
    today = pipeline_io.today_str()

    # 排除：今天已经推过的（避免重复打扰）
    rows = conn.execute("""
        SELECT i.id, i.title, i.tier, i.score_total, i.summary_one_line,
               i.uploader, i.next_review_at, i.review_count, i.mastery,
               i.file_path, i.source_url
        FROM items i
        WHERE i.review_enabled=1 AND i.archived=0 AND i.next_review_at<=?
          AND NOT EXISTS (
            SELECT 1 FROM push_history p
            WHERE p.item_id=i.id AND substr(p.pushed_at,1,10)=?
          )
        ORDER BY
          CASE i.tier WHEN 'A' THEN 0 WHEN 'B' THEN 1 ELSE 2 END,
          i.next_review_at ASC
        LIMIT 1
    """, (now, today)).fetchone()
    if not rows:
        return None
    return dict(rows)


def pick_question(conn: sqlite3.Connection, item_id: int) -> str:
    """从 questions 表里随机挑一个，避免每次问同一个。"""
    rows = conn.execute(
        "SELECT question FROM questions WHERE item_id=?", (item_id,)
    ).fetchall()
    if not rows:
        return "你还记得上次收的那条主要讲了什么吗？用 2 句话说一下。"
    return random.choice([r[0] for r in rows])


PUSH_TEMPLATES = {
    "soul": "🧠 复习一下：\n\n{question}\n\n（来自《{title}》，{days_ago} 天前收的；答完回我 1-5 分，1=完全忘 5=能举一反三）",
    "associate": "🔗 顺手考你一下：\n\n《{title}》里讲过的内容——{question}\n\n（{days_ago} 天前收的；30 秒回我 1-5 分就行）",
    "apply": "⚙️ 实战题：\n\n{question}\n\n（出自《{title}》；放空 1 分钟想想再回 1-5 分）",
    "drop": "❓{days_ago} 天前你收了《{title}》，从那以后再没碰过这个主题。\n\n要不要直接 archive？回「留」继续保留 + 重置间隔；回「删」彻底归档。",
}


def build_push_text(item: dict, question: str) -> tuple[str, str]:
    """构造推送文案 + 选模式。"""
    now = pipeline_io.now_dt()
    # 解析 next_review_at（北京时间或 UTC 均能 fromisoformat）
    from datetime import datetime
    try:
        nra = datetime.fromisoformat(item["next_review_at"])
        days_ago = (now - nra).days + 1
    except Exception:
        days_ago = 1

    # 简单选模式：复习次数 >= 3 且 mastery > 50 → drop（淘汰询问）；否则随机 soul/associate/apply
    if item.get("review_count", 0) >= 3 and item.get("mastery", 0) > 50:
        mode = "drop"
    else:
        mode = random.choice(["soul", "associate", "apply"])

    text = PUSH_TEMPLATES[mode].format(
        question=question,
        title=item["title"],
        days_ago=max(1, days_ago),
    )
    return text, mode


def read_feishu_creds() -> tuple[str, str]:
    """从 knowledge profile .env 读飞书 app_id/app_secret"""
    env_path = "/root/.hermes/profiles/knowledge/.env"
    app_id = app_secret = ""
    with open(env_path) as f:
        for line in f:
            if line.startswith("FEISHU_APP_ID="):
                app_id = line.split("=", 1)[1].strip().strip('"').strip("'")
            elif line.startswith("FEISHU_APP_SECRET="):
                app_secret = line.split("=", 1)[1].strip().strip('"').strip("'")
    return app_id, app_secret


def get_tenant_token(app_id: str, app_secret: str) -> str:
    """换 tenant_access_token（每 2 小时刷新一次，简单起见每次都刷）"""
    import httpx
    r = httpx.post(
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        json={"app_id": app_id, "app_secret": app_secret},
        timeout=15,
    )
    r.raise_for_status()
    data = r.json()
    if data.get("code") != 0:
        raise RuntimeError(f"飞书 token 获取失败: {data}")
    return data["tenant_access_token"]


def send_to_feishu(text: str) -> bool:
    """
    直接走飞书 OpenAPI：
      1. 拿 tenant_access_token
      2. POST /open-apis/im/v1/messages 发文本消息
    """
    try:
        import httpx
        app_id, app_secret = read_feishu_creds()
        if not app_id or not app_secret:
            print("[push] 飞书凭证未配置", file=sys.stderr)
            return False

        token = get_tenant_token(app_id, app_secret)

        # 发文本消息到 open_id
        r = httpx.post(
            "https://open.feishu.cn/open-apis/im/v1/messages",
            params={"receive_id_type": "open_id"},
            headers={"Authorization": f"Bearer {token}",
                     "Content-Type": "application/json; charset=utf-8"},
            json={
                "receive_id": FEISHU_OPEN_ID,
                "msg_type": "text",
                "content": json.dumps({"text": text}, ensure_ascii=False),
            },
            timeout=15,
        )
        if r.status_code != 200:
            print(f"[push] HTTP {r.status_code}: {r.text[:300]}", file=sys.stderr)
            return False
        body = r.json()
        if body.get("code") != 0:
            print(f"[push] 飞书 API 失败: {body}", file=sys.stderr)
            return False
        return True
    except Exception as e:
        print(f"[push] 异常: {e}", file=sys.stderr)
        return False


def build_heartbeat_text(conn: sqlite3.Connection) -> str | None:
    """没 due item 时的心跳消息：每天最多推一次（早上 9 点）。"""
    now = pipeline_io.now_dt()
    today = pipeline_io.today_str()

    # 只在早上 9 点那次推心跳（北京 UTC+8 → server UTC = 01:00）
    # cron 跑 1/5/13 三次，只让 1:00 触发心跳
    if now.hour != 9:
        return None

    # 同日去重：今天已经推过任何东西就不再推心跳
    row = conn.execute("""
        SELECT id FROM push_history WHERE substr(pushed_at, 1, 10) = ?
    """, (today,)).fetchone()
    if row:
        return None

    # 看积压（next_review 已过期但未答的）
    overdue_count = conn.execute("""
        SELECT COUNT(*) FROM items
        WHERE review_enabled=1 AND archived=0 AND next_review_at <= ?
    """, (pipeline_io.now_iso(),)).fetchone()[0]

    # 看本周新增
    week_ago = (now - timedelta(days=7)).isoformat(timespec="seconds")
    week_new = conn.execute("""
        SELECT COUNT(*) FROM items WHERE created_at >= ? AND archived=0
    """, (week_ago,)).fetchone()[0]

    # 看下一条该复习是哪条 + 时间
    next_row = conn.execute("""
        SELECT id, title, next_review_at FROM items
        WHERE review_enabled=1 AND archived=0 AND next_review_at > ?
        ORDER BY next_review_at ASC LIMIT 1
    """, (pipeline_io.now_iso(),)).fetchone()

    parts = ["☀️ 早上好"]
    parts.append("")

    if overdue_count > 0:
        parts.append(f"⏰ 积压：{overdue_count} 条该复习了")
    else:
        parts.append("📭 今天没该复习的")

    parts.append(f"📥 本周新增 {week_new} 条")

    if next_row:
        nra = next_row["next_review_at"]
        try:
            from datetime import datetime
            nra_dt = datetime.fromisoformat(nra)
            hours_until = (nra_dt - now).total_seconds() / 3600
            if hours_until < 24:
                when = f"{hours_until:.1f} 小时后"
            else:
                when = f"{hours_until/24:.1f} 天后"
        except Exception:
            when = nra[:16]
        title = next_row["title"][:30]
        parts.append(f"🔜 下一条复习：《{title}》（{when}）")

    parts.append("")
    parts.append("💬 回我「找点东西复习」可手动挑一条；说「我在研究 X」可建项目档案。")

    return "\n".join(parts)


def main():
    if not INDEX_DB.exists():
        print(json.dumps({"ok": False, "reason": "no index.db yet"}), file=sys.stderr)
        return 0

    conn = sqlite3.connect(INDEX_DB)
    conn.row_factory = sqlite3.Row
    conn.executescript(DDL_PUSH_HISTORY)

    item = pick_due_item(conn)
    if not item:
        # 没 due item，看是否需要发心跳
        heartbeat = build_heartbeat_text(conn)
        if heartbeat:
            sent = send_to_feishu(heartbeat)
            if sent:
                conn.execute(
                    "INSERT INTO push_history (item_id, question, mode, pushed_at) VALUES (?, ?, ?, ?)",
                    (0, heartbeat[:200], "heartbeat", pipeline_io.now_iso()),
                )
                conn.commit()
            print(json.dumps({"ok": sent, "mode": "heartbeat", "sent": sent,
                             "text": heartbeat}, ensure_ascii=False))
            conn.close()
            return 0 if sent else 1

        print(json.dumps({"ok": True, "skipped": True, "reason": "no due item, not heartbeat time"}, ensure_ascii=False))
        conn.close()
        return 0

    question = pick_question(conn, item["id"])
    text, mode = build_push_text(item, question)

    sent = send_to_feishu(text)

    if sent:
        conn.execute(
            "INSERT INTO push_history (item_id, question, mode, pushed_at) VALUES (?, ?, ?, ?)",
            (item["id"], question, mode, pipeline_io.now_iso()),
        )
        conn.commit()

    out = {
        "ok": sent,
        "item_id": item["id"],
        "title": item["title"],
        "tier": item["tier"],
        "mode": mode,
        "question": question,
        "text": text,
        "sent": sent,
        "pushed_at": pipeline_io.now_iso() if sent else None,
    }
    print(json.dumps(out, ensure_ascii=False, default=str))
    conn.close()
    return 0 if sent else 1


if __name__ == "__main__":
    sys.exit(main() or 0)
