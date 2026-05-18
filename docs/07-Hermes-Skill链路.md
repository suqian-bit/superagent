---
title: Hermes Skill 链路
created: 2026-05-12
tags: [hermes, skill, agent, prompt]
---

# Hermes Skill 链路

知识助手跑在 [Hermes Agent](https://github.com/NousResearch/hermes-agent) 框架上。这份文档讲清楚：Agent 怎么"被引导"去调我们写的脚本，而不是自己用 `browser_navigate` 瞎抓。

**核心教训**：Hermes 的 SOUL.md 是**软约束**，不能完全依赖。需要配合 **`_next_action` 协议**做硬约束。

---

## Profile 隔离

服务器上两个独立 Profile：

| Profile | HERMES_HOME | systemd | 飞书 App ID | 用途 |
|---------|-------------|---------|------------|------|
| `default` | `/root/.hermes/` | `hermes-gateway.service` | `cli_a83...` | 量化助手（旧）|
| `knowledge` | `/root/.hermes/profiles/knowledge/` | `hermes-gateway-knowledge.service` | `cli_aa8a79adf9b8dccc` | **本项目** |

各自独立：配置 / 记忆 / 会话 / SOUL.md / skills / 飞书 Bot / 用户 open_id。

```bash
# 同一个命令，加 -p 切换 profile
hermes -p knowledge skills list
hermes -p knowledge cron list
hermes -p knowledge gateway start
```

---

## SOUL.md（人格 + HARD RULE）

路径：`/root/.hermes/profiles/knowledge/SOUL.md`

整个文件被 Hermes 注入到每次 Agent 调用的 system prompt。**Agent 一直看着这份。**

### 顶部：HARD RULE

```
# ⛔ HARD RULE — 意图分流 + Pipeline 链

## Step 0：判断意图（先做）

| 用户消息特征 | 走哪 |
|------------|------|
| 含 URL（http/https）| 走 Ingest Pipeline |
| 明确说"存下来/记一下/收藏"+有可保留内容 | 走 Ingest Pipeline |
| 以"我之前/帮我找/查一下/有没有"开头 | 走查询 |
| 以问号结尾的短问句 | 正常对话，不调任何收录命令 |
| 闲聊/反思/<20字 | 正常对话 |
| 是对你上一条复习推送的回答 | 走 digest review |
| 判断不准（长文本无 URL）| 反问主人 |

## Step 1-5: Ingest Pipeline
（详见 [[02-Ingest-Pipeline]]）

## 处理复习应答
（详见 [[06-复习系统#反馈回写]]）

## 绝对禁止
- 收到 URL 用 browser_navigate / execute_code / curl 自己抓
- pipeline 中间跳步
- 不调 save 就回复主人
- 编造没听到的内容
```

### 底部：原始人格定义

人格、说话风格、不该说的话——这些是"软"指引，Agent 大致遵守。

---

## SKILLs（procedural memory + 工具描述）

路径：`/root/.hermes/profiles/knowledge/skills/`

每个 skill 一个目录：

```
skills/
├── content-fetcher/        ← Step 1
│   ├── SKILL.md            ← description 决定 Agent 何时"想到"用它
│   └── scripts/
│       ├── fetch_xhs.py
│       └── fetch_video.py
├── score/SKILL.md + score.py
├── classify/SKILL.md + classify.py
├── essence/SKILL.md + essence.py
├── save/SKILL.md + save.py
├── knowledge-digest/SKILL.md + digest.py
└── _lib/                   ← 不是真 skill，是共享代码
    ├── pipeline_io.py      ← _next_action 协议、北京时间、DeepSeek 调用
    └── review_pusher.py    ← cron 推送脚本
```

### SKILL.md 格式

```markdown
---
name: score
description: Pipeline Step 2/5 — 拿 fetch 抓到的内容做五维评分...
version: 1.0.0
metadata:
  hermes:
    tags: [pipeline, scoring, step2]
    related_skills: [content-fetcher, classify]
---

# Score (Pipeline Step 2/5)

## 触发条件
fetch 的输出里有 `_next_action: {command: "score --in ..."}`，照着执行。

## 调用方法
```bash
score --in /tmp/ingest/<run_id>_step1.json
```

## 输出后必须做的
立即执行 `_next_action.command`（指向 classify）。
```

Hermes 在 Agent 启动时把所有 SKILL.md 的 description 塞进上下文，Agent 看到 description 就**有可能**主动用——但不一定。所以才需要下面的强约束。

---

## `_next_action` 协议（硬约束）

每个 pipeline 工具的输出 JSON 必含 `_next_action` 字段：

```json
{
  "ok": true,
  "step_file": "/tmp/ingest/<run_id>_step1.json",
  "_next_action": {
    "command": "score --in /tmp/ingest/<run_id>_step1.json",
    "reason": "已抓到原料，下一步：score 进行五维评分",
    "input_file": "/tmp/ingest/<run_id>_step1.json"
  }
}
```

SOUL.md 把这写成 HARD RULE：

> 每个工具的输出里都有 `_next_action.command` 字段，**照那个字段执行下一步**，不要自己创造命令。

实现在 `pipeline_io.emit_output()`，详见 [[08-命令速查#pipeline_io]]。

### 为什么这样比 SOUL.md 强引导有效

| 引导方式 | Agent 行为 |
|---------|----------|
| ❌ "记住要按 5 步走" | 经常跳步 / 改用 browser_navigate |
| ❌ "URL 进来时调 xhs" | 容易忘 / 凭"印象"挑工具 |
| ✅ 工具刚返回的 JSON 里写明"下一步执行 X" | **Agent 几乎不可能漏掉**（数据就在眼前）|

类似 LangChain 的 agent chain 模式，比 prompt 工程稳一个量级。

---

## 已注册的 6 个 Skill

```bash
hermes -p knowledge skills list
```

| Skill | 类型 | 入口命令 | 详见 |
|-------|------|---------|------|
| `content-fetcher` | pipeline Step 1 | `xhs` / `video` | [[02-Ingest-Pipeline#Step 1]] |
| `score` | pipeline Step 2 | `score --in` | [[02-Ingest-Pipeline#Step 2]] |
| `classify` | pipeline Step 3 | `classify --in` | [[02-Ingest-Pipeline#Step 3]] |
| `essence` | pipeline Step 4 | `essence --in` | [[02-Ingest-Pipeline#Step 4]] |
| `save` | pipeline Step 5 | `save --in` | [[02-Ingest-Pipeline#Step 5]] |
| `knowledge-digest` | 查询 + 反馈 | `digest pending-review` / `digest review` / `digest list` 等 | [[06-复习系统]] |

---

## 失败教训史

### Bug 1: SOUL.md 写了"必须调 xhs"，agent 还是用 browser

**现象**：SOUL.md 顶部 HARD RULE 写了规则，agent 看到 URL 还是调 `browser_navigate` 抓封面图。

**原因**：SOUL.md 太长，规则在第几行 Agent 注意不到 / 觉得"软规则"。

**修复**：
1. 命令简化为 `xhs URL --transcribe`（之前是 `/www/content_fetcher_venv/bin/python /root/.hermes/profiles/knowledge/skills/content-fetcher/scripts/fetch_xhs.py URL --transcribe` 这种长 path）。
2. HARD RULE 顶在第 1 行，列明反例（"上次你犯过的错"）。

仍然不稳。

### Bug 2: SOUL 改强了，agent 改用 curl 自己抓

**现象**：SOUL.md 明令禁止 browser，agent 改用 `terminal: curl + grep` 自己抓 HTML。

**原因**：SOUL.md 只禁了 browser/execute_code，没禁 curl。Agent "钻空子"。

**根本问题**：依赖 SOUL.md 的"禁止清单"永远禁不完。

**修复方向**：转 `_next_action` 强约束 + 命令简化 + 工具列表里有 `xhs/video/digest` 这种**主动名词**让 Agent 第一选项就是它。

### 终极方案

- ✅ 命令简化（短而强力的命令名）
- ✅ `_next_action` 串起 pipeline
- ✅ SOUL.md HARD RULE 保留"意图分流"（少量规则）
- ✅ 工具脚本输出包含 `summary_for_agent`（agent 直接转发不要再加工）

最终效果：**实测 5 步 pipeline 在飞书里 agent 自然按顺序跑完**，没用 browser 或 curl 兜底。

---

## 系统命令

```bash
# 重启 knowledge gateway（改了 SOUL/SKILL 后生效）
systemctl --user restart hermes-gateway-knowledge

# 看是否活着
systemctl --user is-active hermes-gateway-knowledge

# 看实时日志
journalctl --user -u hermes-gateway-knowledge -f

# 查看 skill 列表
hermes -p knowledge skills list
```

详见 [[08-命令速查]]。

---

## 关联文档

- [[02-Ingest-Pipeline]] — Pipeline 的 5 步细节
- [[06-复习系统#反馈回写]] — Agent 处理复习应答的流程
- [[08-命令速查#hermes]] — Hermes 相关命令清单
- [[11-已知问题与TODO#hermes 升级风险]] — `hermes update` 会覆盖 SOUL.md 吗？
