---
name: content-fetcher
description: 当主人发来含 URL 的消息时（小红书/B站/抖音/快手/YouTube 链接），调用此 skill 抓取真实内容（视频转录 + 元数据）。这是 ingest pipeline 的第 1 步。
version: 2.2.0
metadata:
  hermes:
    tags: [content, video, transcription, pipeline, step1]
    related_skills: [score, classify, essence, save]
---

# Content Fetcher (Pipeline Step 1/5)

## 触发条件

**消息含 URL** 时立即调用，特别是：
- `xhslink.com` / `xiaohongshu.com` → 调 `xhs`
- `bilibili.com` / `b23.tv` → 调 `video`
- `douyin.com` / `v.douyin.com` → 调 `video`（需 cookies，可能失败）
- `youtube.com` / `youtu.be` / `kuaishou.com` → 调 `video`

**不要**用 browser_navigate / execute_code / curl 自己抓——优先用这两个命令。若 `video` 失败且标题含「图文」或报错「无法解析 video_id」，走下方失败处理中的「抖音图文帖 fallback」。

## 调用方法

```bash
# 小红书
xhs "<URL>" --transcribe

# 视频平台（默认 60s 超时）
video "<URL>"
```

### ⏱️ 抖音超时重试规则

抖音链接 `video` 命令**容易超时**（60s 不够），按以下流程：

1. **首次**：`video "<URL>"`，timeout=60
2. **若 60s 超时（无 ok/false 返回值、只有 timeout error）**：**立即重试** `video "<URL>"`，timeout=120
3. **只有 120s 也超时或返回 ok:false 时**，才走下方失败处理（cookies/browser fallback）
4. **不要**在首次超时后就跳到 browser——抖音视频页 browser 也能抓到内容，但优先用 video 命令（有转录 + mp4 下载）

## 输出

返回 JSON，含 `_next_action` 字段：

```json
{
  "ok": true,
  "title": "...",
  "content": "完整转录或文字内容",
  "step_file": "/tmp/ingest/<run_id>_step1.json",
  "_next_action": {
    "command": "score --in /tmp/ingest/<run_id>_step1.json",
    "reason": "已抓到原料，下一步：score 进行五维评分"
  }
}
```

## 拿到结果后必须做的事

**立即执行 `_next_action.command`**——不要回主人，不要思考"是否需要评分"。一定要按 chain 走完 5 步再回主人。

## 抖音超时重试策略（重要）

抖音 `video` 命令不稳定，可能 60-120s 超时。**不要一次失败就放弃**：

1. 首次：`video "<URL>"` timeout=120
2. 超时 → 重试 timeout=300（经验表明更长时间常能成功）
3. 仍超时 → 尝试 `browser_navigate` 然后 `browser_snapshot(full=true)` 提取页面内容（抖音 PC 端页面有时会加载出完整的视频文案和章节要点）
4. 如果浏览器也超时（60s）→ 再给 `video` 一次机会 timeout=300
5. 全部失败 → 告诉主人「抖音暂时抓不到，可能是网络波动或反爬，稍后再试」

**注意**：browser fallback 抓到的内容质量不如 `video` 命令（含转录文本），但至少能拿到标题、章节要点和标签。有 browser 内容时仍需手动构造 step1 JSON 走 pipeline。

## 失败处理

返回 `"ok": false` 时：
- 抖音 `Fresh cookies needed` → 告诉主人需要导出 cookies，**不要换工具自己抓**
- **抖音图文帖（`无法从 URL 解析 video_id`）** → 这是图片+文字帖，非视频。走以下 fallback：
  1. 用 `browser_navigate` 打开 URL
  2. 调 `browser_snapshot(full=true)` 提取页面文字
  3. 从 snapshot 中提取正文内容（通常在 `<paragraph>` 或 text 节点中）
  4. 用 `execute_code` 手动构造 step1 JSON 写入 `/tmp/ingest/<run_id>_step1.json`
  5. 然后按正常 pipeline 继续：`score --in ...` → `classify --in ...` → `essence --in ...` → `save --in ...`
  6. step1 JSON 需包含字段：`ok, platform, url, title, uploader, tags_seed, publish_time, stats, content, run_id, step, step_num, step_file, _next_action`
- ⚠️ **已知问题**：`douyin.com/note/...` 页面反爬较严，browser fallback 可能也返回空页（`element_count: 0`）。遇到时告诉主人「抖音图文帖暂时抓不到，已记录待优化」，不要反复重试。
- 其他失败 → 原文转述错误给主人

## 不要做的事

- ❌ 看封面图编内容
- ❌ 拿到结果不调下一步（信息丢失）
- ❌ 自己 curl / execute_code（视频/小红书 URL 场景）
- ⚠️ browser_navigate 仅用于抖音图文帖 fallback，视频/小红书不要用
