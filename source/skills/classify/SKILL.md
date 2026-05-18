---
name: classify
description: Pipeline Step 3/5 — 智能分类，1-3 个大类（带关联度）+ 3-8 个细标签 + 主题指纹。从 score 的 _next_action 进入。
version: 1.0.0
metadata:
  hermes:
    tags: [pipeline, categorization, step3]
    related_skills: [score, essence]
---

# Classify (Pipeline Step 3/5)

## 触发条件

**score 的输出含 `_next_action: {command: "classify --in ..."}`**，照执行。

## 调用方法

```bash
classify --in /tmp/ingest/<run_id>_step2.json
```

## 行为

脚本内自动：
1. 读已有的 categories 列表（避免重复造词）
2. 调 DeepSeek 决定挂 1-3 个大类，每个 0-100 关联度
3. 出 3-8 个细标签
4. 生成 fingerprint（指纹，3-6 词连字符）
5. 生成一句话核心（30 字内）

## 输出后必须做的

**立即执行 `_next_action.command`**（指向 `essence --in ...`）。

## 不要做的事

- ❌ 跳过 essence 直接 save
- ❌ 自己改 fingerprint（除非脚本失败）
