#!/usr/bin/env python3
"""
classify: 拿 step2（已评分的）内容，调 DeepSeek 出大类分类 + 细标签 + 关联度。

用法: classify --in /tmp/ingest/<run_id>_step2.json

输出: step3.json，含 categories(每个带 affinity) / tags / fingerprint
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, "/root/.hermes/profiles/knowledge/skills/_lib")
import pipeline_io


INDEX_DB = Path("/www/knowledge/index.db")


SYSTEM = "你是一个知识库分类助手。你只输出 JSON，不输出其他任何东西。"


PROMPT_TEMPLATE = """对以下内容做：1) 大类分类（1-3 个，带关联度）；2) 细标签（3-8 个）；3) 主题指纹。

已有的大类（如果合适请复用，不合适请新增）：
{existing_categories}

规则：
1. 大类是粗粒度，少而稳定。例：投资 / AI / 美食 / 代码 / 政策 / 心理 / 生产力 / 健康 / 设计 / 摄影 / 教育 / 历史 / 哲学
   - 一条内容最多 3 个大类
   - 每个大类给 0-100 关联度（不是均分，要分主次）
   - 主大类 >=80，次大类 30-60
   - 实在不贴边的就别硬挂

2. 细标签是细粒度，多而灵活。例：RAG / Hermes / 多智能体 / profile / fine-tuning / 看板 / 期权 / 套利
   - 3-8 个，简洁
   - 优先复用作者已经打的话题（来自 tags_seed），合理的话不要改

3. 指纹（fingerprint）：3-6 个英文小写单词连字符。抓主题骨干，不是标题哈希。
   - 例：hermes-profiles-multi-instance-tutorial
   - 例：karpathy-rag-intro

返回 JSON：
{{
  "categories": [
    {{"name": "AI", "affinity": 95, "reason": "..."}},
    {{"name": "生产力", "affinity": 55, "reason": "..."}}
  ],
  "tags": ["Hermes", "profile", "多智能体", "--copy"],
  "fingerprint": "hermes-profiles-multi-instance-tutorial",
  "summary_one_line": "一句话核心（30 字内）"
}}

待分类内容：
标题：{title}
作者：{uploader}
作者标签（参考，未必好用）：{tags_seed}
评分：{tier} ({score_total}/100)
正文/转录（{content_chars} 字）：
<<<
{content}
>>>
"""


def load_existing_categories() -> list[str]:
    """从 index.db 取已有大类列表（首次运行时为空）。"""
    if not INDEX_DB.exists():
        return []
    try:
        conn = sqlite3.connect(INDEX_DB)
        rows = conn.execute("SELECT name FROM categories ORDER BY count DESC LIMIT 50").fetchall()
        conn.close()
        return [r[0] for r in rows]
    except sqlite3.OperationalError:
        return []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="input_file", required=True)
    args = ap.parse_args()

    data = pipeline_io.read_step_file(args.input_file)
    run_id = data.get("run_id")

    existing = load_existing_categories()
    existing_str = "（库里还没有，请你为这条新建大类）" if not existing else \
                   "、".join(existing)

    content = data.get("content", "")
    if len(content) > 6000:
        content = content[:6000] + "\n... (后续已截断)"

    prompt = PROMPT_TEMPLATE.format(
        existing_categories=existing_str,
        title=data.get("title", "无"),
        uploader=data.get("uploader", "无"),
        tags_seed=", ".join(data.get("tags_seed", [])) or "(无)",
        tier=data.get("tier", "C"),
        score_total=data.get("score_total", 0),
        content_chars=data.get("content_chars", 0),
        content=content or "(无文字内容)",
    )

    try:
        reply = pipeline_io.call_deepseek(prompt, system=SYSTEM, temperature=0.0, max_tokens=800)
        parsed = pipeline_io.parse_json_reply(reply)
    except Exception as e:
        pipeline_io.emit_error("classify", f"DeepSeek 分类失败: {e}", run_id=run_id,
                                recoverable=True)

    # 注入回 data
    out = dict(data)
    out["categories"] = parsed.get("categories", [])
    out["tags"] = parsed.get("tags", [])
    out["fingerprint"] = parsed.get("fingerprint", "")
    out["summary_one_line"] = parsed.get("summary_one_line", "")

    # ──── 顺便评估跟现有项目的关联度（融合 project-link） ────
    out["project_relevance"] = evaluate_project_relevance(out)

    pipeline_io.emit_output("classify", run_id, out, step_num=3)


# ───────────────────────────────────────────────
# Project relevance evaluation
# ───────────────────────────────────────────────

PROJECT_LINK_SYSTEM = (
    "你是一个知识库关联评估员。你只输出 JSON。"
    "判断一条新内容跟主人正在做的项目有多相关。"
)

PROJECT_LINK_PROMPT = """主人在维护几个项目档案。请评估下面这条新内容跟每个项目的**关联度**（0-100）。

# 评分规则

| 关联度 | 含义 |
|-------|------|
| 90-100 | 直接命中项目卡点 / 提供具体可用方法 |
| 70-89  | 主题强相关，能补充思路 |
| 40-69  | 部分相关，间接有启发 |
| < 40   | 基本不相关，**别为了挂而挂** |

# 项目档案

{projects_section}

# 待评估的新内容

标题：{title}
分类：{categories}
标签：{tags}
一句话核心：{summary_one_line}
精华预览：
<<<
{content_preview}
>>>

# 返回 JSON

{{
  "evaluations": [
    {{"project": "quant", "relevance": 85, "reason": "命中卡点：训练/回测时间分离"}},
    {{"project": "llm-agent", "relevance": 30, "reason": "只是顺带提了 AI Agent"}}
  ]
}}

只对 relevance >= 40 的写完整 reason，<40 的可以一句话带过。
"""


def evaluate_project_relevance(out_data: dict) -> list[dict]:
    """读 projects 表 → 调 LLM → 返回 [{project, relevance, reason}]，relevance >= 40 才入选。"""
    try:
        conn = sqlite3.connect(INDEX_DB)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT id, name, display_name, file_path FROM projects
            WHERE archived=0
        """).fetchall()
        conn.close()
    except sqlite3.OperationalError:
        return []

    if not rows:
        return []

    projects = []
    for r in rows:
        from pathlib import Path
        md_path = Path(r["file_path"])
        if not md_path.exists():
            continue
        md = md_path.read_text(encoding="utf-8")
        if md.startswith("---"):
            parts = md.split("---", 2)
            body = parts[2] if len(parts) >= 3 else md
        else:
            body = md
        projects.append({
            "name": r["name"],
            "display_name": r["display_name"],
            "body": body.strip()[:1500],
        })

    if not projects:
        return []

    projects_section = "\n".join(
        f"## 项目 `{p['name']}` ({p['display_name']})\n\n{p['body']}\n\n---"
        for p in projects
    )

    content = out_data.get("content", "")
    content_preview = content[:600] if content else ""
    cats = ", ".join(f"{c['name']}({c.get('affinity', '?')})"
                     for c in out_data.get("categories", []))
    tags = ", ".join(out_data.get("tags", []))

    prompt = PROJECT_LINK_PROMPT.format(
        projects_section=projects_section,
        title=out_data.get("title", ""),
        categories=cats or "(无)",
        tags=tags or "(无)",
        summary_one_line=out_data.get("summary_one_line", ""),
        content_preview=content_preview or "(无)",
    )

    try:
        reply = pipeline_io.call_deepseek(prompt, system=PROJECT_LINK_SYSTEM,
                                           temperature=0.0, max_tokens=600)
        parsed = pipeline_io.parse_json_reply(reply)
        evaluations = parsed.get("evaluations", [])
        # 只保留 relevance >= 40
        return [e for e in evaluations if e.get("relevance", 0) >= 40]
    except Exception:
        # 失败不阻塞 pipeline，降级为空
        return []


if __name__ == "__main__":
    main()
