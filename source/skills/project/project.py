#!/usr/bin/env python3
"""
project: 项目档案管理（对话驱动）。

让主人不用坐下来写 md，而是被 agent 问答引导着建立 / 更新项目档案。

子命令:
  project list                     看所有项目
  project show <name>              看某个项目的 md 全文 + 关联 item 数
  project new <name> <answers_json>  从问答 JSON 建新档案（agent 收集到答案后调）
  project update <name> <patch>    增量更新项目某些字段
  project link <item_id> <name> <relevance> [reason]   手动关联
  project unlink <item_id> <name>
  project relevance <item_id>      看一条 item 跟所有 project 的关联
  project archive <name>           项目结束 / 暂停（archived=1）
  project questions                返回引导问题 JSON（给 agent 看）
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, "/root/.hermes/profiles/knowledge/skills/_lib")
import pipeline_io


KB_ROOT = Path("/www/knowledge")
PROJECTS_DIR = KB_ROOT / "projects"
INDEX_DB = KB_ROOT / "index.db"

PROJECTS_DIR.mkdir(parents=True, exist_ok=True)


DDL = """
CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    display_name TEXT,
    file_path TEXT NOT NULL,
    status TEXT DEFAULT 'in-progress',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    archived INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS item_projects (
    item_id INTEGER NOT NULL,
    project_id INTEGER NOT NULL,
    relevance INTEGER NOT NULL,
    reason TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY (item_id, project_id),
    FOREIGN KEY (item_id) REFERENCES items(id),
    FOREIGN KEY (project_id) REFERENCES projects(id)
);
CREATE INDEX IF NOT EXISTS idx_item_projects_proj ON item_projects(project_id, relevance DESC);
CREATE INDEX IF NOT EXISTS idx_item_projects_item ON item_projects(item_id);
"""


# ──────────────────────────────────────────────────────
# 引导问题清单（agent 用，建新档案时按顺序问）
# ──────────────────────────────────────────────────────

QUESTIONS = [
    {
        "key": "name_and_pitch",
        "ask": "这个项目叫什么？给个英文小写短名（如 quant / llm-agent），加一句话描述。",
        "example": "quant — 用 AI/强化学习做美股+加密的量化策略",
    },
    {
        "key": "goal",
        "ask": "项目目标是什么？做完后**什么样算成功**？（具体的产出 / 能力）",
        "example": "能跑实盘的看板系统，6 个月内连续 3 个月正收益",
    },
    {
        "key": "progress",
        "ask": "现在做到哪步了？分三段告诉我：✅ 已做完 / 🚧 在做 / ⏳ 还没开始。",
        "example": "✅ Freqtrade 部署、回测框架\n🚧 实时数据接入\n⏳ 多时间周期信号融合",
    },
    {
        "key": "pain_points",
        "ask": "目前最大的卡点 / 想搞懂的事？最多 3 个，没有就说没。",
        "example": "1. 训练/回测时间切分怎么严格\n2. 多 Agent 协作做不同任务\n3. 实时舆情怎么用",
    },
    {
        "key": "code_location",
        "ask": "项目相关的代码 / 文档在哪？（本机或服务器的路径）",
        "example": "/root/quant_strategies/ + Notion 笔记",
    },
    {
        "key": "tags",
        "ask": "这个项目相关的技能 / 兴趣标签？（用于打通老库存）",
        "example": "量化、强化学习、Python、Freqtrade、ccxt",
    },
    {
        "key": "cadence",
        "ask": "你打算用什么节奏推进？",
        "example": "每天晚上 1 小时 / 周末整块时间 / 不定期",
    },
]


def die(msg: str):
    print(json.dumps({"ok": False, "error": msg}, ensure_ascii=False))
    sys.exit(1)


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(INDEX_DB)
    conn.row_factory = sqlite3.Row
    conn.executescript(DDL)
    return conn


def now() -> str:
    return pipeline_io.now_iso()


# ──────────────────────────────────────────────────────
# 命令实现
# ──────────────────────────────────────────────────────

def cmd_questions(args):
    """返回引导问题清单。Agent 调一次拿到所有问题，分批问主人。"""
    print(json.dumps({
        "ok": True,
        "questions": QUESTIONS,
        "instructions": (
            "按顺序问主人，**一次问 1-2 个，不要列表轰炸**。"
            "收集到 7 个答案后，调 `project new <name> <json>` 创建档案。"
            "中途主人不想答的字段，用空字符串占位。"
        ),
    }, ensure_ascii=False))


def cmd_new(args):
    """建新项目。需要 7 个回答的 JSON。建完后自动反扫老库存关联。"""
    name = args.name.strip().lower()
    if not name.replace("-", "").replace("_", "").isalnum():
        die("项目名只能是英文小写字母 / 数字 / 横线 / 下划线")

    # 读取答案 JSON
    answers_raw = args.answers
    if answers_raw == "-":
        answers_raw = sys.stdin.read()
    try:
        answers = json.loads(answers_raw)
    except Exception as e:
        die(f"answers JSON 解析失败: {e}")

    conn = connect()

    # 撞名检查
    if conn.execute("SELECT id FROM projects WHERE name=?", (name,)).fetchone():
        die(f"项目 {name} 已存在，用 `project update {name}` 来改")

    pitch = answers.get("name_and_pitch", "")
    display_name = pitch.split("—")[-1].strip() if "—" in pitch else name

    # 渲染 md
    md = render_project_md(name, display_name, answers)
    md_path = PROJECTS_DIR / f"{name}.md"
    md_path.write_text(md, encoding="utf-8")

    cur = conn.execute("""
        INSERT INTO projects (name, display_name, file_path, status, created_at, updated_at)
        VALUES (?, ?, ?, 'in-progress', ?, ?)
    """, (name, display_name, str(md_path), now(), now()))
    project_id = cur.lastrowid
    conn.commit()

    # ── Phase 4: 反扫老库存 ──
    backscan_summary = ""
    if not args.no_backscan:
        backscan = backscan_old_items(conn, project_id, name, md_path)
        backscan_summary = backscan.get("summary", "")

    conn.close()

    print(json.dumps({
        "ok": True,
        "action": "created",
        "project_id": project_id,
        "name": name,
        "file_path": str(md_path),
        "summary_for_agent": (
            f"✅ 已建立项目档案 `{name}`：{display_name}\n"
            f"📄 文件：{md_path}\n"
            + (f"\n{backscan_summary}\n" if backscan_summary else "")
            + f"\n接下来收录新内容时会自动评估跟这个项目的关联度。"
        ),
    }, ensure_ascii=False))


def backscan_old_items(conn: sqlite3.Connection, project_id: int,
                        project_name: str, md_path: Path) -> dict:
    """
    反扫老库存：用 LLM 评估每条老 item 跟新项目的关联度。
    relevance >= 60 的自动 link。
    返回 summary 字典给主人看。
    """
    items = conn.execute("""
        SELECT id, title, summary_one_line, tier, score_total
        FROM items WHERE archived=0
        ORDER BY weight DESC, id DESC
        LIMIT 50
    """).fetchall()

    if not items:
        return {"linked": 0, "summary": "（库里还没有内容，所以没什么可关联的）"}

    # 读项目档案
    md = md_path.read_text(encoding="utf-8") if md_path.exists() else ""
    if md.startswith("---"):
        parts = md.split("---", 2)
        body = parts[2] if len(parts) >= 3 else md
    else:
        body = md
    project_body = body.strip()[:2500]

    # 让 LLM 批量评估
    items_section = "\n".join(
        f"id={r['id']} | tier={r['tier']} | {r['title']} — {r['summary_one_line']}"
        for r in items
    )

    SYSTEM = "你是知识库关联评估员。你只输出 JSON。"
    prompt = f"""主人刚建立了一个新项目档案，请评估库里**已有的内容**跟这个项目的关联度。

# 项目档案

## 项目 `{project_name}`

{project_body}

# 库里已有内容（{len(items)} 条）

{items_section}

# 评分规则（参考）

- 90+: 直接命中项目卡点
- 70-89: 主题强相关
- 60-69: 部分相关有启发
- < 60: 不相关，**不要为了挂而挂**

# 返回 JSON

只列出 relevance >= 60 的：

{{
  "links": [
    {{"item_id": 3, "relevance": 85, "reason": "..."}},
    {{"item_id": 5, "relevance": 70, "reason": "..."}}
  ]
}}
"""

    try:
        reply = pipeline_io.call_deepseek(prompt, system=SYSTEM,
                                           temperature=0.0, max_tokens=1500)
        parsed = pipeline_io.parse_json_reply(reply)
    except Exception as e:
        return {"linked": 0, "summary": f"（反扫失败：{e}，可以稍后手动 project link 关联）"}

    links = parsed.get("links", [])
    now_ts = now()
    linked_titles = []

    for link in links:
        item_id = link.get("item_id")
        relevance = int(link.get("relevance", 0))
        reason = link.get("reason", "")
        if relevance < 60:
            continue
        # 查 item 是否存在
        row = conn.execute("SELECT id, title FROM items WHERE id=?", (item_id,)).fetchone()
        if not row:
            continue
        conn.execute("""
            INSERT OR REPLACE INTO item_projects (item_id, project_id, relevance, reason, created_at)
            VALUES (?, ?, ?, ?, ?)
        """, (item_id, project_id, relevance, reason, now_ts))
        linked_titles.append(f"  [{relevance}] id={item_id} {row['title'][:40]}")

    conn.commit()

    if linked_titles:
        summary = (
            f"🔁 反扫老库存：在 {len(items)} 条已收录里发现 {len(linked_titles)} 条跟 `{project_name}` 相关：\n"
            + "\n".join(linked_titles[:5])
            + (f"\n  ... 还有 {len(linked_titles) - 5} 条" if len(linked_titles) > 5 else "")
        )
    else:
        summary = f"🔁 反扫老库存：{len(items)} 条已收录里没有跟 `{project_name}` 强相关的。"

    return {"linked": len(linked_titles), "summary": summary}


def render_project_md(name: str, display_name: str, a: dict) -> str:
    """从答案 dict 渲染 markdown 项目档案。"""
    tags = a.get("tags", "")
    if isinstance(tags, str):
        tags_list = [t.strip() for t in tags.replace("、", ",").split(",") if t.strip()]
    else:
        tags_list = tags

    return f"""---
project: {name}
display_name: {display_name}
status: in-progress
created: {now()[:10]}
updated: {now()[:10]}
tags: {json.dumps(tags_list, ensure_ascii=False)}
---

# {display_name}

> {a.get('name_and_pitch', '').split('—', 1)[-1].strip() if '—' in a.get('name_and_pitch', '') else a.get('name_and_pitch', '')}

## 🎯 目标
{a.get('goal', '_(未填)_')}

## 📊 当前进度
{a.get('progress', '_(未填)_')}

## ⚠️ 当前卡点 / 想搞懂的事
{a.get('pain_points', '_(未填)_')}

## 📂 代码 / 文档位置
{a.get('code_location', '_(未填)_')}

## 🏷 相关技能 / 兴趣
{', '.join(tags_list) if tags_list else '_(未填)_'}

## 🕒 推进节奏
{a.get('cadence', '_(未填)_')}

---

## 📜 关联记录

<!-- 这一节由系统自动维护，每条收录的内容如果关联度 >= 60 会自动加进来 -->
<!-- 也可以手动用 `project link <item_id> {name} <relevance>` 关联 -->

## 📌 备注

<!-- 自由记录 -->
"""


def cmd_list(args):
    conn = connect()
    rows = conn.execute("""
        SELECT p.id, p.name, p.display_name, p.status, p.created_at, p.updated_at,
               p.archived,
               (SELECT COUNT(*) FROM item_projects ip WHERE ip.project_id=p.id) AS item_count,
               (SELECT COUNT(*) FROM item_projects ip
                JOIN items i ON i.id=ip.item_id
                WHERE ip.project_id=p.id AND ip.relevance>=80 AND i.archived=0) AS high_rel_count
        FROM projects p
        WHERE archived=0 OR ?=1
        ORDER BY updated_at DESC
    """, (1 if args.all else 0,)).fetchall()
    print(json.dumps({"ok": True, "count": len(rows),
                      "projects": [dict(r) for r in rows]},
                     ensure_ascii=False, default=str))


def cmd_show(args):
    conn = connect()
    row = conn.execute("SELECT * FROM projects WHERE name=?", (args.name,)).fetchone()
    if not row:
        die(f"项目 {args.name} 不存在")

    md_text = Path(row["file_path"]).read_text(encoding="utf-8") if Path(row["file_path"]).exists() else "(md 文件不存在)"

    items = conn.execute("""
        SELECT i.id, i.title, i.tier, i.score_total, ip.relevance, ip.reason, ip.created_at
        FROM item_projects ip
        JOIN items i ON i.id=ip.item_id
        WHERE ip.project_id=? AND i.archived=0
        ORDER BY ip.relevance DESC, ip.created_at DESC
        LIMIT 20
    """, (row["id"],)).fetchall()

    print(json.dumps({
        "ok": True,
        "project": dict(row),
        "markdown": md_text,
        "related_items": [dict(r) for r in items],
    }, ensure_ascii=False, default=str))


def cmd_link(args):
    """手动关联一条 item 到 project（用于补救自动关联漏掉的）"""
    conn = connect()
    proj = conn.execute("SELECT id, name FROM projects WHERE name=?", (args.project,)).fetchone()
    if not proj:
        die(f"项目 {args.project} 不存在")
    item = conn.execute("SELECT id, title FROM items WHERE id=?", (args.item_id,)).fetchone()
    if not item:
        die(f"item id={args.item_id} 不存在")

    conn.execute("""
        INSERT OR REPLACE INTO item_projects (item_id, project_id, relevance, reason, created_at)
        VALUES (?, ?, ?, ?, ?)
    """, (item["id"], proj["id"], args.relevance, args.reason or "", now()))
    conn.execute("UPDATE projects SET updated_at=? WHERE id=?", (now(), proj["id"]))
    conn.commit()
    conn.close()
    print(json.dumps({
        "ok": True,
        "summary_for_agent": (
            f"🔗 已关联：id={item['id']}《{item['title']}》 → {proj['name']}，"
            f"关联度 {args.relevance}{'，理由：' + args.reason if args.reason else ''}"
        ),
    }, ensure_ascii=False))


def cmd_unlink(args):
    conn = connect()
    proj = conn.execute("SELECT id FROM projects WHERE name=?", (args.project,)).fetchone()
    if not proj:
        die(f"项目 {args.project} 不存在")
    n = conn.execute("DELETE FROM item_projects WHERE item_id=? AND project_id=?",
                     (args.item_id, proj["id"])).rowcount
    conn.commit()
    print(json.dumps({"ok": True, "deleted_rows": n,
                      "summary_for_agent": f"🔓 已解除关联：item={args.item_id} ↔ {args.project}"},
                     ensure_ascii=False))


def cmd_relevance(args):
    """查一条 item 关联了哪些 project"""
    conn = connect()
    rows = conn.execute("""
        SELECT p.name, p.display_name, ip.relevance, ip.reason
        FROM item_projects ip
        JOIN projects p ON p.id=ip.project_id
        WHERE ip.item_id=? AND p.archived=0
        ORDER BY ip.relevance DESC
    """, (args.item_id,)).fetchall()
    print(json.dumps({"ok": True, "item_id": args.item_id,
                      "projects": [dict(r) for r in rows]},
                     ensure_ascii=False, default=str))


def cmd_update(args):
    """增量更新某个字段（追加一段笔记 / 改 status / 改卡点）"""
    conn = connect()
    proj = conn.execute("SELECT * FROM projects WHERE name=?", (args.name,)).fetchone()
    if not proj:
        die(f"项目 {args.name} 不存在")

    md_path = Path(proj["file_path"])
    if not md_path.exists():
        die(f"md 文件不存在：{md_path}")

    md = md_path.read_text(encoding="utf-8")

    # 简单实现：在指定段落追加内容（让 agent 决定怎么改，传 patch markdown 进来）
    section = args.section
    text = args.text

    section_headers = {
        "goal": "## 🎯 目标",
        "progress": "## 📊 当前进度",
        "pain_points": "## ⚠️ 当前卡点 / 想搞懂的事",
        "code_location": "## 📂 代码 / 文档位置",
        "tags": "## 🏷 相关技能 / 兴趣",
        "cadence": "## 🕒 推进节奏",
        "notes": "## 📌 备注",
    }
    header = section_headers.get(section)
    if not header:
        die(f"未知段落：{section}。可用：{', '.join(section_headers.keys())}")

    # 在 header 下追加一段
    if header not in md:
        die(f"md 文件里没有段落 {header}")

    if args.mode == "append":
        # 在 header 后插一行
        md = md.replace(header, f"{header}\n\n{text}", 1)
    elif args.mode == "replace":
        # 替换 header 下到下一个 ## 之前的内容
        import re
        pattern = re.compile(rf"({re.escape(header)}\n).*?(?=\n## |\n---|\Z)", re.DOTALL)
        md = pattern.sub(f"\\1\n{text}\n", md, count=1)
    else:
        die(f"未知 mode：{args.mode}（append / replace）")

    # 更新 updated 字段（frontmatter）
    import re
    md = re.sub(r"^updated: \d{4}-\d{2}-\d{2}", f"updated: {now()[:10]}", md, flags=re.MULTILINE)

    md_path.write_text(md, encoding="utf-8")
    conn.execute("UPDATE projects SET updated_at=? WHERE id=?", (now(), proj["id"]))
    conn.commit()

    print(json.dumps({
        "ok": True,
        "summary_for_agent": f"✏️ 已更新 `{args.name}` 的 [{section}] 段落（mode={args.mode}）"
    }, ensure_ascii=False))


def cmd_archive(args):
    conn = connect()
    proj = conn.execute("SELECT id FROM projects WHERE name=?", (args.name,)).fetchone()
    if not proj:
        die(f"项目 {args.name} 不存在")
    status = args.status or "done"
    conn.execute("UPDATE projects SET archived=1, status=?, updated_at=? WHERE id=?",
                 (status, now(), proj["id"]))
    conn.commit()
    print(json.dumps({"ok": True,
                      "summary_for_agent": f"📦 项目 `{args.name}` 已归档（status={status}）"},
                     ensure_ascii=False))


# ──────────────────────────────────────────────────────
# main
# ──────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd")

    sp.add_parser("questions", help="返回引导问题清单（agent 用）")

    p_list = sp.add_parser("list", help="看所有项目")
    p_list.add_argument("--all", action="store_true", help="含 archived 的")

    p_show = sp.add_parser("show", help="看某个项目档案")
    p_show.add_argument("name")

    p_new = sp.add_parser("new", help="建新项目（传 answers JSON）")
    p_new.add_argument("name")
    p_new.add_argument("answers", help="JSON 字符串，或 '-' 从 stdin 读")
    p_new.add_argument("--no-backscan", action="store_true", help="跳过自动反扫老库存")

    p_upd = sp.add_parser("update", help="增量更新某段")
    p_upd.add_argument("name")
    p_upd.add_argument("--section", required=True,
                       help="goal/progress/pain_points/code_location/tags/cadence/notes")
    p_upd.add_argument("--text", required=True)
    p_upd.add_argument("--mode", default="append", choices=["append", "replace"])

    p_link = sp.add_parser("link")
    p_link.add_argument("item_id", type=int)
    p_link.add_argument("project")
    p_link.add_argument("relevance", type=int)
    p_link.add_argument("--reason", default="")

    p_unlink = sp.add_parser("unlink")
    p_unlink.add_argument("item_id", type=int)
    p_unlink.add_argument("project")

    p_rel = sp.add_parser("relevance", help="看一条 item 关联了哪些 project")
    p_rel.add_argument("item_id", type=int)

    p_arc = sp.add_parser("archive")
    p_arc.add_argument("name")
    p_arc.add_argument("--status", default="done", choices=["done", "paused"])

    args = ap.parse_args()
    if not args.cmd:
        ap.print_help()
        sys.exit(1)

    handlers = {
        "questions": cmd_questions,
        "list": cmd_list,
        "show": cmd_show,
        "new": cmd_new,
        "update": cmd_update,
        "link": cmd_link,
        "unlink": cmd_unlink,
        "relevance": cmd_relevance,
        "archive": cmd_archive,
    }
    handlers[args.cmd](args)


if __name__ == "__main__":
    main()
