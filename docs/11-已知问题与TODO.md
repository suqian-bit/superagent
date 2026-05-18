---
title: 已知问题与 TODO
created: 2026-05-12
updated: 2026-05-14
tags: [todo, issues, roadmap]
---

# 已知问题与 TODO

把所有遗留问题、待做项、长期规划集中在这。按"阻塞 / 高优 / 中优 / 低优"分级。

---

## ⚠️ 阻塞项（影响主线，需尽快解决）

_当前无阻塞项。抖音问题已解决，详见 [[#已修复 抖音抓取]]。_

---

## 🔥 高优（影响完整性）

### Drop 模式触发条件细化

**当前**：`review_count >= 3 且 mastery > 50` 时切换到 "drop"（询问要不要 archive）。

**问题**：mastery 100 才"完全掌握"，50 偏低。可能太早问。

**改进**：
- 条件改成 `review_count >= 5 且 mastery >= 75`
- 或者用 ease 指标：`ease >= 2.9`（多次答对积累的高 ease）

**预期工作量**：10 分钟改 `review_pusher.py`。

---

## 📋 中优（增强能力）

### 项目档案系统的下一步（**2026-05-14 主线已建好，剩补强**）

当前已经做完 Phase 1-5，剩余迭代：

- **Phase 6：对接代码** — 给 agent grep 用户代码权限（`/root/quant_strategies/` 等），收新内容时能精确说"跟你 momentum.py:42 那段逻辑有关"。工作量：1 天+。
- **Phase 7：项目档案的"过时提醒"** — `updated_at > 7 天` 的项目档案，agent 主动问"距离上次更新过了 X 天了，要不要顺便更新一下卡点和进度？"
- **Phase 8：周报里加项目段** — 本周收的内容里 80% 都跟 quant 相关 → 周报里突出"这周你重心在 quant 上"。

详见 [[12-项目档案与关联]]。

### RAG / 语义检索

**目标**：让"我之前看过 X 吗？"这种查询能跨条召回。

> **注意**：[[12-项目档案与关联#场景化检索]] 的 `digest impact` 已经覆盖了"卡住时翻库"场景。RAG 更适合"模糊回忆"的场景（"我之前看过一个讲 RAG 的视频"）。

**思路**：
1. 用 `BAAI/bge-small-zh-v1.5`（已有的本地 embedding 模型）给每条 item 算向量
2. 存入 ChromaDB
3. 加命令：
   ```bash
   digest ask "<问题>"           # RAG 问答
   digest similar <id>          # 找最相似的 5 条
   ```

**为什么没急做**：
- 库里现在 6 条，攒到 50+ 条 RAG 才有意义
- bge-small 是 80% 解决方案，未来可以换 OpenAI / Zhipu embedding
- `digest impact` 已经覆盖了 80% 检索需求

**预期工作量**：3-4 小时（迁移之前 Mac 本地的 RAG 代码）。

详见 [[05-查重机制#3 合并而非拒绝]]。

---

### Obsidian 同步

**目标**：服务器 `/www/knowledge/items/` → Mac 上的本地 Obsidian vault。

**方案对比**：

| 方案 | 优点 | 缺点 |
|------|------|------|
| Git push/pull | 简单，可控 | 手动触发 |
| Syncthing | 实时双向 | 引入额外服务 |
| iCloud / OneDrive | 跨设备方便 | 服务器 → 云盘 → Mac 不直接 |

**当前打算**：Git。服务器每天 23:00 cron 提交一次到私有 repo，Mac 自动 pull。

**预期工作量**：1 小时（建私有 repo + cron + 本地 git pull alias）。

---

### Category MOC 自动生成

**目标**：每个 category 在 Obsidian vault 里有一个汇总页（Map of Content）。

```markdown
---
title: AI
---

# AI 大类

```dataview
TABLE tier, score_total AS 分, file.cday AS 收录日
FROM "items"
WHERE contains(categories, "AI")
SORT score_total DESC
\```
```

主人在 Obsidian 里点 AI 分类页就能看到所有 AI 内容。

**前置条件**：Obsidian 同步完成。

---

### Weight 动态调整

**当前**：`weight = score_total`，永不变。

**未来**：
- 主人手动 pin/unpin → `weight ± 20`
- 被引用 / 被检索点击 → `weight + 5`
- 长期未碰 → 衰减

**预期工作量**：1 小时（加事件 hook 到 digest 命令）。

---

### Tags 标签云 / 主题聚类

```bash
digest tag-cloud --limit 30
# 返回 [{name, count}] 按频次排
```

可以让 Agent 在周报时识别"本周哪些标签突然升温"。

---

## 🌱 低优（锦上添花）

### 抖音/小红书「长按复制口令」格式（**2026-05-17 改用纯文本变通方案**）

**问题原貌**：飞书有一种纯文本分享格式，没有 URL，只有口令：

```
8:/ 01/28 d@a.NW :1pm 【能否使用RAG技术来解决大模型的长期记忆问题？】
长按复制打开抖音，即可阅读文章 ︽︽nGiLbXgKgr69ǚǚ
```

**实测过的死路**（2026-05-14）：
- ❌ 拼 `v.douyin.com/nGiLbXgKgr69/` → 302 重定向到首页
- ❌ 拼 `iesdouyin.com/share/video/nGiLbXgKgr69/` → 404
- ❌ 调 `api.amemv.com/aweme/v1/check/share/in/?keyword=...` → "Url does not match"

这个 API 需要输入里有真 URL 才能解码，纯 token 不行（in-app 反编码 API）。

**2026-05-17 区分视频 vs 图文，给三种解法**：

| 类型 | 方案 |
|------|------|
| **抖音视频口令** | 让主人在 APP 点「分享 → 复制链接」（不是「复制口令」）→ 拿到 `v.douyin.com/xxx/` → 走 `video` |
| **抖音图文口令 + 能复制文字** | 让主人长按正文「全选 → 复制」→ 粘贴回飞书 → agent 调 **`text --source douyin`** 走纯文本入库 |
| **抖音图文口令 + 不让复制（最常见）** | 主人在 APP 截图（多张可以连截）→ 发图给飞书 → agent 调 **`ocr`** → `text` → 走 pipeline |

`detect-share` 现在区分 `douyin_video_token` / `douyin_article_token` 两个 kind，给不同的 suggestion。

**核心洞察**：
- 图文文章的好处是**视觉上就是文字**，OCR 准确率 90%+ 完全够用
- 不需要破解任何链接 / API / 签名，纯本地 OCR 解决
- 跟链接抓取走同样的 score → classify → essence → save 5 步，效果完全一样

**未来可能的破解方向**（备查，不打算做）：
1. 抓抖音 APP 流量看 `check/share/in/` 的真实签名格式
2. 用 Playwright + iPhone UA 模拟剪贴板触发抖音 H5 解码
3. 接入第三方付费 API（TikHub 等）

详见 [[02-Ingest-Pipeline#Step 1 fetch]] 和 [[08-命令速查#text]]。

---

### Hermes 升级风险

`/root/.hermes/hermes-agent/hermes_cli/model_normalize.py` 有本地补丁（让 `deepseek-v4-pro` 不被强行重映射为 `deepseek-chat`）。

**hermes update 会覆盖这个补丁。**

补救脚本：`/root/.hermes/patch_deepseek_v4.sh`

每次 update 后：

```bash
hermes update
/root/.hermes/patch_deepseek_v4.sh
systemctl --user restart hermes-gateway hermes-gateway-knowledge
```

详见 [[08-命令速查#hermes 框架]]。

---

### SQLite reindex 命令

如果 `index.db` 损坏或丢失，需要从 md 文件重建。

**未实现**：`digest reindex`

```bash
digest reindex                # 扫描所有 md，重建 SQLite
```

预期工作量：1-2 小时。

详见 [[01-数据存储设计#重建 SQLite 的兜底方案]]。

---

### C 级不保留完整原文

**当前**：所有 tier 都保留 `## 完整原文`。

**最初设计**：C 级"酌情有效沉淀"，不保留完整原文，只存精华。

**保留全部**的好处：以后改主意能复盘。每条 5-15KB，1000 条才 15MB，空间不是问题。

**结论**：暂不改。除非空间真的吃紧。

---

### URL 直接查重（兜底）

**当前**：只查 fingerprint（精确 + 模糊）。

**漏洞**：同一个 URL 被收两次时，如果 LLM 给的指纹不同（例如分类心情不一样），会重复入库。

**修复**：

```python
# Step 0: URL 完全匹配查重
SELECT id FROM items WHERE source_url = ?
```

预期工作量：5 分钟。

---

### 行为感知调度（动态 cron）

**目标**：不固定 9/13/21，按用户活跃时段自动调整。

**前置**：需要积累 1-2 周的 `push_history.response_at` 数据，做时间段分析。

**目前**：不急。当前固定时段够用。

详见我们之前对话里探讨过的"方向 C"。

---

## 🐛 已知小问题

### 1. 一些旧数据用了 UTC 时间

数据库里某些早期写入的字段（id=3 之前的）的 `next_review_at` 用了 UTC 格式 `+00:00`。

**影响**：字符串比较和数值比较都正确（ISO 标准能 fromisoformat），用户感知不到。

**修复**（可选）：写迁移脚本统一转 +08:00。详见 [[09-时区与时间约定#历史遗留数据]]。

### 2. push_history 一天可推多条不同 item

cron 一天跑 3 次，每次推 1 条 item。**不同 item 一天可以推多次**（这是预期行为）。

如果只想"每天最多推 1 条"，改 cron：

```cron
# 只保留早上 9 点
0 1 * * * /usr/local/bin/review-push >> /var/log/review_pusher.log 2>&1
```

### 3. 旧 digest.db 已废弃但文件还在

路径：`/www/content_fetcher/digest/digest.db`

这是早期版本用的，现在 `digest` 命令完全切换到 `/www/knowledge/index.db`。

**清理**（可选）：

```bash
rm -rf /www/content_fetcher/digest/
```

---

## 📜 长期规划（季度级别）

### 多智能体协作

让 `knowledge` profile 在某些场景下调 `default` profile（量化助手）：

> 主人："这条 RAG 文章里有提到一个套利策略，你帮我让量化助手算一下"
> 知识助手 → `hermes -p default chat -q "..."` → 拿到量化分析 → 整合回复

可以用 Hermes 自带的 `--Q` quiet 模式实现。

### iOS Shortcut / 微信集成

如果用户想脱离飞书，可以加：
- iOS 快捷指令分享 → 服务器 HTTP API → 走 ingest pipeline
- 微信公众号 / 个人号

### 知识图谱可视化

把 categories + tags + item 的关联导出成图，用 D3.js / Obsidian Graph 可视化。

---

## 历史已修

| 问题 | 修复时间 | 方案 |
|------|---------|------|
| **库存跟主人当前项目脱节** | **2026-05-14** | **建立 project-profile 系统：projects + item_projects 双表 + 7 问引导对话 + 自动反扫老库存 + 入库时 LLM 评估关联 + 飞书消息显示 🎯 跟项目。详见 [[12-项目档案与关联]] 和 [[../meta/2026-05-14-从仓库到助理]]** |
| **场景化检索缺失** | **2026-05-14** | **`digest impact "<场景>"`：LLM 拆关键词 + 项目识别 + 综合排序返回 Top 8** |
| **没 due 时 cron 完全无声** | **2026-05-14** | **早上 9 点没 due 推心跳消息（积压/本周新增/下一条复习时间），主人不再担心系统挂了** |
| **精华字数偏少（C 级才 100 字）** | 2026-05-13 | **改成原文 18% 动态字数，下限 300 上限 1800。prompt 改为"省去看视频"目标，结构化输出 5 个小节。详见 [[03-评分与分级#精华字数]]** |
| **summary_for_agent 不含 essence** | **2026-05-13** | **save.py 把完整精华嵌入飞书消息，主人不开 md 也能消化** |
| **HERMES_MAX_ITERATIONS=90 长视频超时** | **2026-05-13** | **调到 180，加 EnvironmentFile 让 systemd 双保险注入** |
| **Archive 命令缺失** | **2026-05-13** | **`digest demote <id> --archive` + promote/demote 全套** |
| **周报 cron** | **2026-05-13** | **`weekly_report.py` + AI 观察 + 同周去重，crontab 周日 22:00** |
| **抖音抓取（全链路）** | 2026-05-12 | `iesdouyin.com/share/video/` 老接口 + iPhone UA，无 cookies。详见 [[#已修复 抖音抓取]] |
| Agent 用 browser_navigate 自己抓抖音 | 2026-05-11 | SOUL.md HARD RULE + 命令简化 |
| Agent 用 curl + grep 自己抓小红书 | 2026-05-11 | `_next_action` 强约束 |
| 词序不同的 fingerprint 没撞重（id=1/2）| 2026-05-12 | token-set Jaccard 模糊匹配 |
| 时区混乱（部分 UTC 部分本地）| 2026-05-12 | 统一 `pipeline_io.now_iso()` 用 +08:00 |
| 评分维度漂移 | 2026-05-12 | 把评分 prompt 固化在 score.py |
| Hermes 把 deepseek-v4-pro 强行映射成 deepseek-chat | 2026-05-10 | 本地补丁 + update 后重打脚本 |
| failed minimax cron 污染日志 | 2026-05-10 | 暂停 jobs.json 里的 minimax 任务 |

---

## 已修复：抖音抓取

> 2026-05-12 突破。从"阻塞 1 周"到"15 秒完整跑通"。

### 关键发现

抖音有**新旧两套接口**：

| 接口 | 反爬 |
|------|------|
| `www.douyin.com/video/<id>` | 强（a_bogus/X-Bogus/msToken 三重签名）|
| **`www.iesdouyin.com/share/video/<id>/`** | **弱**（只要 iPhone UA）|

后者是早年的"分享给好友"页面，至今仍保留。代码源自 [yzfly/douyin-mcp-server](https://github.com/yzfly/douyin-mcp-server) 的解析逻辑。

### 流程

```
v.douyin.com/xxx                      短链
   ↓ httpx GET follow_redirects=True
www.douyin.com/video/<id>?...         长链（含 query string，用于抽 id）
   ↓ regex 抽 video_id
www.iesdouyin.com/share/video/<id>/   老分享页（HTTP 200，无需 cookies）
   ↓ regex 抽 window._ROUTER_DATA
JSON: loaderData['video_(id)/page'].videoInfoRes.item_list[0]
   ↓ video.play_addr.url_list[0]
带水印 mp4 URL（https://aweme.snssdk.com/aweme/v1/playwm/?...）
   ↓ str.replace("playwm", "play")
**无水印 mp4 URL**
   ↓ httpx 直接下载（iPhone UA，无需 cookies）
mp4 文件 → ffmpeg → mp3 → faster-whisper → 转录
```

### 实测耗时（73 秒视频）

| 步骤 | 耗时 |
|------|------|
| 拿 mp4 URL | 1 秒 |
| 下载 mp4（4.1MB）| 3 秒 |
| ffmpeg 抽 mp3 | < 1 秒 |
| Whisper base 转录 | 15 秒 |
| **合计** | **~20 秒** |

### 实现位置

- `/root/.hermes/profiles/knowledge/skills/_lib/douyin_extractor.py` — 提取核心逻辑
- `/root/.hermes/profiles/knowledge/skills/content-fetcher/scripts/fetch_video.py` 里的 `handle_douyin()` — Pipeline 集成
- 命令 `video "https://v.douyin.com/xxx/"` 自动派发到这条路径

### 已知限制

1. **`duration` 字段为 0**：iesdouyin 接口返回的 duration 字段缺失。不影响转录（whisper 自己探测时长），但 frontmatter 里会显示 0。可以从 whisper 的 `info.duration` 兜底（待加）。
2. **统计数据可能缺失**：digg/comment/share/play 等 stats 字段有时为 null。
3. **抖音可能某天关闭老接口**：iesdouyin 是历史遗留，没有官方承诺。备选方案：MediaCrawler / TikHub。

### 验证用例

```bash
video "https://v.douyin.com/2zAFl8tbI14/"
# {
#   "platform": "douyin",
#   "title": "懒人炒股，数据分析交给AI",
#   "uploader": "书童",
#   "content_chars": 285,
#   "content": "各位保存好,我的量化数据看板又进化了...",
#   "_next_action": {"command": "score --in ..."}
# }
```

---

## 关联文档

- [[00-架构总览]] — 项目状态总览
- [[07-Hermes-Skill链路#失败教训史]] — 完整失败案例
- [[06-复习系统]] — 复习相关 TODO
- [[10-飞书集成]] — 飞书相关 TODO
