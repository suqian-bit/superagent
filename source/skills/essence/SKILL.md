---
name: essence
description: Pipeline Step 4/5 — 按 tier 精炼内容（A 详细/B 中等/C 简短）+ 生成 2-3 个反思问题。从 classify 的 _next_action 进入。
version: 1.0.0
metadata:
  hermes:
    tags: [pipeline, distillation, step4]
    related_skills: [classify, save]
---

# Essence (Pipeline Step 4/5)

## 触发条件

**classify 的输出含 `_next_action: {command: "essence --in ..."}`**，照执行。

## 调用方法

```bash
essence --in /tmp/ingest/<run_id>_step3.json
```

## 行为

- tier A：详细精华 ~400 字
- tier B：中等精华 ~200 字
- tier C：极短精华 ~100 字

附带 2-3 个反思问题（概念回忆 / 应用 / 辨析）。

## 输出后必须做的

**立即执行 `_next_action.command`**（指向 `save --in ...`）—— pipeline 最后一步。
