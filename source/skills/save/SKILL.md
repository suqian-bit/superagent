---
name: save
description: Pipeline Step 5/5 (最后) — 把完整 ingest 链路结果落盘：md 文件 + SQLite 索引 + 分类/标签关联表。返回 summary_for_agent 给主人看。
version: 1.0.0
metadata:
  hermes:
    tags: [pipeline, persistence, step5, final]
    related_skills: [essence]
---

# Save (Pipeline Step 5/5, Final)

## 触发条件

**essence 的输出含 `_next_action: {command: "save --in ..."}`**，照执行。

## 调用方法

```bash
save --in /tmp/ingest/<run_id>_step4.json
```

## 行为

1. 按 fingerprint 查重（撞重就不再 insert，返回 `action: "duplicate"` 给主人看）
2. 写 md 文件到 `/www/knowledge/items/<日期>/<id>_<指纹>.md`，含 frontmatter + 精华 + 反思 + 原文
3. 写 SQLite `index.db`：items / tags / item_tags / categories / item_categories / questions
4. 返回 `summary_for_agent` 字段（包含核心 / 标签 / 分类 / 评分 / 反思 / 决策）

## 输出后必须做的

**`_next_action` 是 `null`，pipeline 结束**。把 `summary_for_agent` 内容**原样、不增不减**回复给主人。

如果 `action: "duplicate"`，告诉主人"你已经收过类似的（id=X, 标题, 收录日期）"，问要不要合并。
