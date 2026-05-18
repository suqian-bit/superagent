---
name: score
description: Pipeline Step 2/5 — 拿 fetch 抓到的内容做五维评分（密度/可操作/独特/可靠/复用），决定 tier。不要主动调，由上一步 _next_action 引导。
version: 1.0.0
metadata:
  hermes:
    tags: [pipeline, scoring, step2]
    related_skills: [content-fetcher, classify]
---

# Score (Pipeline Step 2/5)

## 触发条件

**fetch 的输出里有 `_next_action: {command: "score --in ..."}`**，照着执行。

不要单独调用，永远从 pipeline 进入。

## 调用方法

```bash
score --in /tmp/ingest/<run_id>_step1.json
```

## 评分维度（脚本内置 prompt 固化）

| 维度 | 含义 |
|------|------|
| density (0-25) | 信息密度，干货 vs 水话 |
| actionable (0-25) | 可操作性，能不能动手 |
| uniqueness (0-25) | 观点独特性 |
| reliability (0-25) | 来源可靠性 |
| reusability (0-25) | 时效复用价值 |

总分 0-100：
- ≥90 → tier A（启动间隔复习）
- 80-89 → tier B（沉淀但不主动催）
- <80 → tier C（归档）

## 输出后必须做的

**立即执行 `_next_action.command`**（指向 `classify --in ...`）。

## 不要做的事

- ❌ 自己根据封面/感觉评分（脚本内调 DeepSeek 评，不是 agent 评）
- ❌ 跳过 classify 直接 save
