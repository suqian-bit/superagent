---
title: Ingest Pipeline（5 步收录链）
created: 2026-05-12
updated: 2026-05-14
tags: [pipeline, ingest, chain]
---

# Ingest Pipeline

5 步流水线，把"飞书里发的链接"变成"打分+分类+精华+落盘"的库存内容。每步独立脚本 + 独立 SKILL.md，用 `_next_action` 协议串起来。

```
xhs / video "URL"     ← Step 1 fetch
   ↓
score --in ...        ← Step 2 score
   ↓
classify --in ...     ← Step 3 classify
   ↓
essence --in ...      ← Step 4 essence
   ↓
save --in ...         ← Step 5 save
   ↓
Agent 回主人 summary_for_agent
```

---

## 协议：`_next_action` 强约束

每个工具输出 JSON 必含两个字段：

```json
{
  "ok": true,
  "step_file": "/tmp/ingest/<run_id>_stepN.json",
  "_next_action": {
    "command": "score --in /tmp/ingest/<run_id>_step1.json",
    "reason": "已抓到原料，下一步：score 进行五维评分",
    "input_file": "/tmp/ingest/<run_id>_step1.json"
  }
}
```

**Agent 收到 `_next_action.command` 后必须立即执行**——`SOUL.md` 把这条写成 HARD RULE。最后一步 save 的 `_next_action = null`，pipeline 结束，Agent 才回复主人。

实现在 [[08-命令速查#pipeline_io]] 的 `pipeline_io.py`：

```python
NEXT_COMMAND = {
    "fetch":    "score --in {step_file}",
    "score":    "classify --in {step_file}",
    "classify": "essence --in {step_file}",
    "essence":  "save --in {step_file}",
    "save":     None,  # 末步
}
```

---

## Step 1: fetch（抓原料）

**入口命令**：`xhs <URL> --transcribe` / `video <URL>` / `text --title "..." --content "..."`

| 主人发了什么 | 命令 |
|-------------|------|
| 小红书 URL（`xhslink.com` / `xiaohongshu.com`）| `xhs` |
| B站 URL（`bilibili.com` / `b23.tv`） | `video` |
| 抖音视频 URL（`v.douyin.com` 短链等） | `video`（用老接口绕过反爬，见 [[../meta/2026-05-12-抖音突破]]） |
| 快手 / YouTube | `video` |
| **纯文本**（公众号 / 知乎 / 主人笔记） | **`text`**（**2026-05-17 新增**，无 URL 也能入库） |
| **抖音长按复制口令**（没 URL，含 `︽︽xxxǚǚ`） | `detect-share` → 区分视频/图文 → 教主人下一步 |
| **图片**（抖音图文截图 / 公众号截图） | **`ocr`** → `text`（**2026-05-17 新增 RapidOCR**，准确率 90%+） |

### `xhs`（小红书）

- 用 `XHS-Downloader`（git clone 在 `/www/XHS-Downloader`）
- 笔记类型：图文 / 视频
- 视频笔记 + `--transcribe`：自动下载 mp4 → 抽 mp3 → faster-whisper 转录
- 输出 fields：title, uploader, tags_seed, content（正文 or 转录）, media

### `video`（yt-dlp + faster-whisper）

- 第一步：`yt-dlp --skip-download --print-json`（拿元数据）
- 第二步：`yt-dlp -f ba --extract-audio --audio-format mp3`（下音频）
- 第三步：faster-whisper（base 模型，CPU int8，简体中文 prompt）
- 输出同上

### `ocr`（图片转文字，**2026-05-17 新增**）

抖音图文 / 公众号截图 / PPT 截图。**RapidOCR (ONNX)**，准确率 90%+。

```bash
ocr <image_path>                   # 单张图，输出 JSON {text, lines, avg_confidence, elapsed_ms}
ocr img1.png img2.png img3.png     # 多张按顺序合并（截长文场景）
ocr <path> --text-only             # 只输出纯文本（适合管道）
```

**典型耗时**：单张图 1-3 秒（首次加载模型 +5 秒）。

### `ocr-stash`（多张截图聚合，**2026-05-18 新增**）

**问题**：飞书一次只能发一张图，主人发文章截图通常连发 3-10 张。
**方案**：每收一张就 `ocr-stash add` 到当前批次，主人说「存」时一次性 commit 入库。

```bash
ocr-stash add <image_path> [--source-hint douyin]   # 加图 + 立刻 OCR，返回 intent_hint
ocr-stash status                                      # 看当前批次（图片数 / 字数 / 剩余 expire 时间）
ocr-stash preview [--limit-chars 800]                # 看累积文本
ocr-stash commit --title "..." --source douyin       # 合并入库 → 自动接 text 命令走 pipeline
ocr-stash clear                                       # 弃
ocr-stash expire-check                                # cron 用，5 分钟没动作自动 commit / clear
```

**Stash 数据**：`/tmp/ocr_stash/` 全局一个（单用户场景）。

**自动过期**（cron 每分钟跑）：
- 5 分钟没新 `add` → 检查累积字数
- ≥ 100 字 → 自动 commit（标题用 "未命名笔记 - YYYYMMDD_HHMM"，并通过飞书通知主人）
- < 100 字 → 直接清空 + 通知

完整对话示例 + Agent 行为规则见 [[../README#主人发图片]] 或 SOUL.md 章节。

---

### `text`（纯文本入库，**2026-05-17 新增**）

适用：抖音图文（只能复制正文）、公众号文章、知乎、主人手打笔记。

```bash
# 直接传 content
text --title "标题" --content "正文" --source douyin --uploader "作者"

# 长文用 stdin（推荐）
echo "正文" | text --title "..." --source douyin --from-stdin
```

`--source` 可选值：`douyin` / `wechat` / `zhihu` / `note` / `text`。

输出 step1.json 格式跟 xhs/video 完全一致，无缝接入后续 score → classify → essence → save。

详见 [[09-命令速查#text 纯文本入库]]。

### 共同输出（step1.json）

```json
{
  "ok": true,
  "platform": "xiaohongshu",
  "title": "Hermes 多智能体实战教程",
  "uploader": "麦冬AI实验室",
  "url": "...",
  "content": "（完整转录文本）",
  "content_chars": 2200,
  "tags_seed": ["Hermes", "Profiles", ...],  // 作者打的话题
  "media": {...},
  "run_id": "...",
  "step": "fetch",
  "step_num": 1,
  "step_file": "/tmp/ingest/..._step1.json",
  "_next_action": {"command": "score --in ...", ...}
}
```

### 失败处理

- 抖音 cookies 缺失 → `error: "需要 cookies 文件..."`，**Agent 必须原文转告主人**，禁止换 browser 兜底
- 小红书 xsec_token 失效 → `error: "XHS 返回空..."`，提示主人重新分享拿新链接
- 网络/超时 → 原始错误转述

---

## Step 2: score（五维评分）

**命令**：`score --in <step1.json>`

读 step1.json，调 DeepSeek API（temperature=0, json_mode）做五维评分，prompt 固化在脚本里（不让 Agent 自由发挥）。详见 [[03-评分与分级]]。

### 输出新增字段

```json
{
  ...原 step1 内容...,
  "scores": {
    "density": 18, "actionable": 22, "uniqueness": 15,
    "reliability": 20, "reusability": 15
  },
  "score_total": 90,
  "tier": "A",
  "weight": 90,
  "score_rationale": {
    "strength": "提供了具体可操作的命令...",
    "weakness": "缺乏权威来源..."
  }
}
```

---

## Step 3: classify（分类 + 标签 + 指纹）

**命令**：`classify --in <step2.json>`

调 DeepSeek 完成 3 件事：
1. 选 1-3 个大类（先查 `categories` 表里已有的，能复用就复用；不合适就建新的）
2. 出 3-8 个细标签
3. 生成 fingerprint（主题指纹，详见 [[05-查重机制]]）

### 关键技巧：复用已有大类

脚本启动时 `SELECT name FROM categories ORDER BY count DESC LIMIT 50`，把已有大类塞进 prompt：

```
已有的大类（如果合适请复用，不合适请新增）：
AI、投资、生产力、教程、心理、健康
```

避免每次 LLM 都生造新类。

### 输出新增字段

```json
{
  ...,
  "categories": [
    {"name": "AI", "affinity": 95, "reason": "..."},
    {"name": "生产力", "affinity": 50, "reason": "..."}
  ],
  "tags": ["HermesAgent", "多实例", ...],
  "fingerprint": "hermes-profiles-multi-instance-tutorial",
  "summary_one_line": "Hermes Profiles 实战：多实例独立配置/记忆/Skills",

  // === 2026-05-14 新增：Project relevance（融合 project-link step） ===
  "project_relevance": [
    {"project": "quant", "relevance": 85, "reason": "命中卡点：训练/回测时间分离"},
    {"project": "llm-agent", "relevance": 60, "reason": "多 Agent 协作思路"}
  ]
}
```

### Project Relevance 评估（**2026-05-14 新增**）

classify 跑完分类后，**额外调一次 DeepSeek** 评估这条内容跟所有现有 `projects/<name>.md` 的关联度。

```python
# classify.py 末尾
out["project_relevance"] = evaluate_project_relevance(out)
```

只保留 `relevance >= 40` 的进 step file。库里没建任何项目时，pass through（空数组）。

详见 [[12-项目档案与关联#自动关联机制]] 和 [[04-智能分类系统]]。

---

## Step 4: essence（长度自适应精华）— **2026-05-13 改造**

**命令**：`essence --in <step3.json>`

调 DeepSeek 按**原文长度**出精华（不再按 tier），目标"读完省去再看原视频"。

### 字数策略（动态）

```python
def calc_target_chars(content_chars: int) -> int:
    """精华目标字数 = 内容 × 18%，下限 300，上限 1800。"""
    raw = int(content_chars * 0.18)
    return max(300, min(1800, raw))
```

| 原文字数 | 精华目标 | 实际范围 |
|---------|---------|---------|
| < 1700 字 | 300 字 | 下限兜底 |
| 1700-10000 字 | 18% | 自由 |
| > 10000 字 | 1800 字 | 上限封顶 |

实际验证（id=5，3168 字量化视频）：

```
target: 570 字
actual: 981 字（LLM 觉得内容有料，多写 70%）
```

详见 [[03-评分与分级#精华字数]]。

### Prompt 写作目标

```
读者读完你的精华后，应该获得 80% 看完原视频的价值，不需要再回去看视频。

如果内容真水（重复、口语化、没具体方法），不要凑字数——直接写
"作者大多在反复讲 X 观点，没有给出具体方法。要点：A、B" 就够了。
```

### Prompt 强制结构（Markdown）

```
## 🎯 核心论点           1-2 句作者中心观点
## 📚 关键论证 / 步骤 / 例子   主体，分点详细
## ⚙️ 具体方法 / 工具 / 代码  原样保留命令、参数、工具名
## ✨ 金句 / 强调点        作者反复提及的
## ⚠️ 局限 / 弱点          1-2 句让主人判断
```

### 反思问题三类

- **概念回忆**："X 是什么？为什么 X 比 Y 好？"
- **应用**："如果要做 Z，怎么用作者的方法？"
- **辨析**："X 跟 Y 区别？什么时候不应该用 X？"

### 输出新增字段

```json
{
  ...,
  "essence": "## 🎯 核心论点\n作者分享了一套基于AI的量化交易策略...\n\n## 📚 关键论证...",
  "essence_target_chars": 570,
  "essence_actual_chars": 981,
  "questions": [
    "作者为什么强调'宁可不交易'？这如何影响手续费？",
    "如何确保训练数据和测试数据完全分离？为什么重要？",
    ...
  ]
}
```

---

## Step 5: save（落盘 + 查重）

**命令**：`save --in <step4.json>`

### 查重两步走

```python
1. SELECT id FROM items WHERE fingerprint = ?              -- 精确
   若命中 → 返回 action="duplicate", match_type="exact"
   
2. SELECT id, fingerprint FROM items WHERE archived=0      -- 模糊
   for each: jaccard = |new_tokens ∩ old_tokens| / |union|
   if jaccard >= 0.8 → action="duplicate", match_type="fuzzy"
```

详见 [[05-查重机制]]。

### 落盘流程

1. INSERT items（占位拿 id）
2. 用 id 命名 md 文件，写到 `/www/knowledge/items/<date>/<id>_<fp>.md`
3. UPDATE items SET file_path
4. INSERT questions × N
5. UPSERT tags + INSERT item_tags
6. UPSERT categories + INSERT item_categories
7. **INSERT item_projects（含 relevance + reason）** ← 2026-05-14 新增

### tier A 自动启用复习

```python
if score_total >= 90:
    review_enabled = 1
    next_review_at = now + 1 day  # 北京时间
```

详见 [[06-复习系统#初始化间隔]]。

### 输出

```json
{
  "ok": true,
  "action": "inserted",       // or "duplicate"
  "id": 3,
  "file_path": "/www/knowledge/items/2026-05-12/0003_xxx.md",
  "tier": "A",
  "score_total": 95,
  "review_enabled": true,
  "next_review_at": "2026-05-13T14:07:52+08:00",
  "summary_for_agent": "📌 ...\n🏷 ...\n📊 ...\n🧠 反思 3 问：...\n🔁 决策：..."
}
```

`summary_for_agent` 是**给主人看的最终回复**，Agent 应原样转发，**不再加工**。

---

## 超时配置（**2026-05-14 集中化**）

之前 timeout 散落在脚本各处，长视频经常 60s/120s/300s 超时。现在统一在脚本顶部：

### `fetch_video.py`

```python
TIMEOUT_METADATA = 180     # yt-dlp 拿元数据
TIMEOUT_DOWNLOAD = 900     # yt-dlp 下载音频/视频（15 分钟，应付长视频）
TIMEOUT_HTTPX    = 90      # httpx 抓页面（如抖音 SPA）
TIMEOUT_FFMPEG   = 600     # ffmpeg 抽音频（长视频也够）
```

### `fetch_xhs.py`

```python
TIMEOUT_XHS_NET   = 30     # XHS-Downloader 网络请求
TIMEOUT_VIDEO_DL  = 900    # yt-dlp 下载小红书视频
TIMEOUT_FFMPEG    = 600    # ffmpeg 抽音频
```

### `douyin_extractor.py`

```python
TIMEOUT_HTTPX = 90         # 抓 iesdouyin 老分享接口
```

### 调整指南

| 现象 | 改哪 |
|------|------|
| 长视频（>20 分钟）下载超时 | `TIMEOUT_DOWNLOAD` 调到 1200+ |
| 抖音抓元数据超时 | `TIMEOUT_HTTPX` 调到 120+ |
| ffmpeg 转码超时（很少见）| `TIMEOUT_FFMPEG` 调到 900 |

所有 timeout **写在脚本顶部**，改完直接生效（不用重启 hermes，因为脚本每次 shell 调用都启新进程）。

---

## 重试 / 调试

每步 step file 都落盘了，链路任意一步失败可以单步重试：

```bash
# 假如 essence 挂了，看上一步 step3 内容
cat /tmp/ingest/<run_id>_step3.json | jq .

# 修复后单步重跑
essence --in /tmp/ingest/<run_id>_step3.json

# 然后接着 save
save --in /tmp/ingest/<run_id>_step4.json
```

或者直接 trace 整条 pipeline：

```bash
xhs "URL" --transcribe > /tmp/s1.json
STEP1=$(jq -r .step_file /tmp/s1.json)
score --in "$STEP1" > /tmp/s2.json
STEP2=$(jq -r .step_file /tmp/s2.json)
# ... 一直到 save
```

---

## DeepSeek 调用统计

每条新内容（非 duplicate）会调 DeepSeek **4 次**（score / classify / essence；再加 fetch 步骤的 0 次 LLM 调用）。

| Step | tokens | 大约费用 |
|------|--------|---------|
| score | 内容 + 600 token 输出 | ~$0.001 |
| classify | 内容 + 800 token 输出 | ~$0.002 |
| essence | 内容 + 1500 token 输出 | ~$0.003 |
| **每条总成本** | | **约 ¥0.04** |

（按 DeepSeek 公开定价估算）

---

## 关联文档

- [[03-评分与分级]] — Step 2 score 的维度规则
- [[04-智能分类系统]] — Step 3 classify 的分类逻辑
- [[05-查重机制]] — Step 5 save 的查重算法
- [[06-复习系统]] — Step 5 落盘时启动的复习调度
- [[07-Hermes-Skill链路]] — Pipeline 怎么在 Agent 里被串起来执行
- [[08-命令速查]] — 所有命令路径
