---
name: knowledge-digest
description: 收录内容时强制执行的"挤干水分 + 评分 + 沉淀 + 复习调度"工作流。每收到一条 content-fetcher 抓回的内容，必须按此 skill 入库到 digest.db，否则视为信息丢失。
version: 1.0.0
metadata:
  hermes:
    tags: [knowledge, digest, scoring, spaced-repetition, dedup]
    related_skills: [content-fetcher]
---

# Knowledge Digest: 收录 + 评分 + 复习调度

## 你必须遵守的硬规则

**任何一条用户发来的内容（抓回的视频/文章 / 用户手打文字），在做总结回复之前，必须先调 `digest` 命令入库。** 不入库 = 信息只活在当次对话上下文里，下次新会话就丢失，等于没收过。

## 工具速查（agent 复制粘贴用）

```bash
# 1) 查重（必做的第一步）
digest find "<fingerprint>"

# 2) 入库（带评分 + 反思问题）
echo '<json>' | digest save --from-stdin

# 3) 列出今天该复习的（cron / 主人主动问"今天复习啥"时用）
digest due --limit 5

# 4) 主人回答完一条复习问题，根据他的答得好不好打分（1-5）
digest review <item_id> <quality>

# 5) 看库存
digest list --limit 20
digest stats
```

## Step-by-step：拿到一条新内容时

### Step 1 — 生成指纹（fingerprint）

指纹 = 主题级别去重 key，**不是 URL 哈希**。

格式：3-6 个英文小写单词，连字符分隔，**抓主题骨干**。

举例：
- 「Hermes 多智能体实战教程」→ `hermes-profiles-multi-instance-tutorial`
- 「懒人炒股，数据分析交给AI」→ `lazy-quant-ai-agent-dashboard`
- 「Karpathy 讲 RAG 入门」→ `karpathy-rag-intro`

**目的**：主题相同的内容（不同人讲、不同语种、不同版本）应该撞到同一个指纹，触发"你已经收过类似主题"提示。

### Step 2 — 查重

```bash
digest find "hermes-profiles-multi-instance-tutorial"
```

返回 `{"found": true, ...}`：告诉主人："你 X 月 X 日已经收过《标题》，这次有什么新东西吗？要不要合并？"，**不要重复入库**。

返回 `{"found": false}`：继续 Step 3。

### Step 3 — 五维评分（每维 0-25 分）

| 维度 | 25 分含义 | 0 分含义 |
|------|---------|---------|
| **信息密度** (density) | 单位时长信息量极高，全是干货 | 全是水话/凑时长 |
| **可操作性** (actionable) | 看完明确知道下一步怎么动手 | 纯感想/没法落地 |
| **观点独特性** (uniqueness) | 角度新颖/独家视角 | 烂大街/到处都能看到 |
| **可靠性** (reliability) | 作者权威 + 论据扎实 | 自媒体复读/无来源 |
| **复用价值** (reusability) | 一年后回看还有用 | 时效性强、3 天就过期 |

**给分原则**：
- 不要全是 20-25，**敢给低分**（信息密度差就是 5 分）
- 总分 100，分布要有区分度
- 内心打完分后，再看总分是不是符合直觉，不一致就重新校准

### Step 4 — 生成反思问题（2-3 个）

帮主人真正消化，不是简单"懂了吗"。三类问题任选：

- **概念回忆类**："视频里讲的 X 是什么？用一句话复述"
- **应用类**："如果你要做 X，会怎么用文章里的 Y 方法？"
- **辨析类**："Y 跟 Z 有什么区别？什么时候用 Y 不用 Z？"

### Step 5 — 入库

```bash
echo '{
  "platform": "xiaohongshu",
  "title": "Hermes 多智能体实战教程",
  "uploader": "麦冬AI实验室",
  "source_url": "http://xhslink.com/o/8EhheeKbIko",
  "content": "<full transcript or text>",
  "summary": "<one-paragraph summary>",
  "tags": ["Hermes", "Profiles", "多实例", "AI Agent"],
  "fingerprint": "hermes-profiles-multi-instance-tutorial",
  "scores": {
    "density": 18,
    "actionable": 22,
    "uniqueness": 15,
    "reliability": 20,
    "reusability": 15
  },
  "questions": [
    "Hermes 的 profile 切换命令是什么？",
    "--copy 参数复制了哪些东西？哪些没复制？",
    "你现在想为自己做哪个独立 profile？"
  ]
}' | digest save --from-stdin
```

**入库逻辑（脚本自动）**：
- `score_total >= 90` → 启动间隔复习，next_review_at = now + 1 天
- `score_total < 90` → 归入知识库，不主动提醒（但 `digest list` 能搜到）

### Step 6 — 回给主人的格式

入库后，给主人一段精炼回复，包含：
1. 一句话核心
2. 五维分数（不用每维写理由，给个总分 + 一两句亮点 / 弱点）
3. 反思问题（让他当场过一遍）
4. 沉淀决策："已沉淀，下次复习时间：X 月 X 日" 或 "归入知识库，不主动催"
5. 去重提示（如果有）

## 复习流程：主人主动问"今天复习啥"或定时推送

```bash
digest due --limit 3
```

拿到 due item 后，挑**最久没碰**的那一条，按下面四种模式之一开问（每次随机一种，避免机械感）：

- **A. 灵魂提问**：用 30 字以内抛一个核心问题，不给提示
- **B. 联想题**：拿这条跟库里别的相关 item 关联（"这条跟你上次收的 X 是不是一回事？说说区别"）
- **C. 应用题**：构造一个场景让他用知识答题
- **D. 淘汰题**：如果 mastery>50 且复习超过 3 次，问"还需要继续记吗，要不要 archive"

收到主人回答后，**判断回忆质量 1-5**，调 `digest review <id> <q>`。

| q | 含义 |
|---|------|
| 1 | 完全答不上来 |
| 2 | 模糊记得有这个事，细节全错 |
| 3 | 大方向对，细节凑合 |
| 4 | 准确回忆，自己的话讲清楚 |
| 5 | 不光答对，还能举一反三 |

## 错误兜底

- **digest save 报错**：把 stderr 转告主人，不要假装入库成功
- **JSON 拼错**：先 `echo '{}' | digest save --from-stdin` 验证语法
- **想保留但又不确定值不值得 ≥90**：诚实给 80-89，归入知识库就行，反正能搜到

## 永远不要做的事

- ❌ 跳过 digest save 直接回复主人（信息会丢失）
- ❌ 不查重直接 save（重复堆积）
- ❌ 用对话上下文记打分规则（必须每次按此 SKILL.md 操作）
- ❌ 给主人"看起来很赞"的分数（虚高没意义）
