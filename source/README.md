# 知识助手 · 源码与部署

> 这个目录是知识助手的**完整运行代码**。把它部署到任何 Ubuntu 22+ / Debian 12+ 服务器上就能复现整套系统。

---

## 这是什么

一个基于 [Hermes Agent](https://github.com/NousResearch/hermes-agent) 的飞书机器人，把刷到的视频/文章自动转录、评分、分类、提炼精华、推送复习题。

详细产品介绍 + 架构文档见上层目录的 [README](../README.md) 和 [docs/](../docs/)。

---

## 目录结构

```
source/
├── SOUL.md                              # Agent 人格 + HARD RULE（强制流程引导）
├── skills/                              # Hermes skills 完整代码
│   ├── _lib/                            # 共享库（不是 hermes skill，是 skills 间共用）
│   │   ├── pipeline_io.py               # 5-step chain 协议 + DeepSeek 调用 + 北京时间
│   │   ├── douyin_extractor.py          # 抖音 iesdouyin 老接口提取
│   │   ├── review_pusher.py             # 复习推送 cron 脚本（飞书 API 直推）
│   │   ├── weekly_report.py             # 周报 cron 脚本
│   │   ├── rag.py                       # ChromaDB RAG 引擎（语义检索）
│   │   └── dashboard.py                 # Streamlit 仪表盘
│   ├── content-fetcher/                 # Step 1: 抓内容
│   │   ├── SKILL.md
│   │   └── scripts/
│   │       ├── fetch_xhs.py             # 小红书（XHS-Downloader + 转录）
│   │       ├── fetch_video.py           # B站/抖音/YouTube/快手（yt-dlp + faster-whisper）
│   │       └── text.py                  # 纯文本入库（无 URL 走 pipeline）
│   ├── score/                           # Step 2: 五维评分
│   ├── classify/                        # Step 3: 分类 + 标签 + 项目关联评估
│   ├── essence/                         # Step 4: 精华提炼（动态字数）
│   ├── save/                            # Step 5: 落盘 md + SQLite
│   ├── knowledge-digest/                # 查询 + 复习反馈 + 手动调级 + 场景检索
│   └── project/                         # 项目档案管理 + 7 问引导 + 反扫老库存
├── experiments/                         # 辅助工具
│   ├── detect_share_token.py            # 识别抖音长按复制口令格式
│   ├── ocr.py                           # RapidOCR 图片转文字
│   ├── ocr_stash.py                     # 多张截图聚合入库（飞书一次一张图场景）
│   ├── fetch_feishu_image.py            # 从飞书 API 下载图片
│   └── exp_parse_douyin_token.py        # 失败实验记录（保留学习用）
└── deploy/
    ├── install.sh                       # 一键部署脚本
    ├── requirements.txt                 # Python 依赖（清理版）
    ├── requirements-full-pip-freeze.txt # 完整 pip freeze（参考用，gitignored）
    ├── .env.example                     # 环境变量模板
    ├── crontab.example                  # cron 任务模板
    └── hermes-gateway-knowledge.service.template   # systemd unit 模板
```

---

## 快速部署

### 前置

- Ubuntu 22.04+ / Debian 12+ (Python 3.12)
- 已经按 [hermes-agent 官方文档](https://hermes-agent.nousresearch.com/docs/) 装好 Hermes
- 已经创建好 knowledge profile：`hermes profile create knowledge`
- 一个 [DeepSeek API key](https://platform.deepseek.com/)
- 一个飞书自建应用（拿到 App ID + Secret，开启机器人能力）

### 部署

```bash
# 1. clone 这个 repo
git clone https://github.com/<you>/superagent.git ~/superagent
cd ~/superagent/source

# 2. 一键部署
sudo bash deploy/install.sh

# 3. 编辑 .env 填入真值
vim ~/.hermes/profiles/knowledge/.env

# 4. 启动
systemctl --user start hermes-gateway-knowledge

# 5. 飞书加 Bot 测试
```

install.sh 会做：
- 装 ffmpeg / sqlite3 / 中文字体
- 建 `/www/content_fetcher/_venv` Python 环境
- 装所有 Python 依赖（rapidocr / chromadb / faster-whisper 等）
- clone XHS-Downloader（不在 PyPI）
- 把 skills + SOUL.md 拷到 hermes profile
- 在 `/usr/local/bin/` 建 16 个命令软链（`xhs` / `video` / `text` / `digest` / `project` / `ocr-stash` 等）
- 写 systemd unit + enable + linger
- 注入 cron 任务（复习推送 + 周报 + ocr 过期检查）

---

## 命令速查

| 命令 | 用途 |
|------|------|
| `xhs "<URL>" --transcribe` | 小红书抓取 + 转录 |
| `video "<URL>"` | B站/抖音/YouTube/快手 抓取 + 转录 |
| `text --title "..." --content "..." --source douyin` | 纯文本入库 |
| `score --in <step1.json>` | 五维评分 |
| `classify --in <step2.json>` | 分类 + 项目关联 |
| `essence --in <step3.json>` | 精华提炼 |
| `save --in <step4.json>` | 落盘 |
| `digest stats / list / due / impact / promote / demote / view` | 查询 + 管理 |
| `project list / show / new / update / link` | 项目档案 |
| `review-push` | 复习推送（cron 自动跑）|
| `weekly-report [--dry-run / --force]` | 周报 |
| `ocr <img>` | 单张图 OCR |
| `ocr-stash add/status/preview/commit/clear` | 多张截图聚合 |
| `detect-share "<分享口令文本>"` | 识别抖音/小红书口令格式 |

---

## 设计原则

详见上层目录 [docs/00-架构总览.md](../docs/00-架构总览.md)。摘要：

1. **流程链确定性 > Agent 灵活性**：5 步 pipeline 用 `_next_action` 强约束，不靠 agent "记得调下一步"
2. **底层数据是真理来源**：md 文件 + SQLite 双写，SQLite 是索引加速，删了能从 md 重建
3. **关键判断都在脚本里调 LLM**：评分/分类/精华都在脚本内部 prompt 固化，agent 不漂移
4. **时间一律北京时间**（`+08:00`）
5. **失败不替主人编**：脚本失败明确说错误

---

## 文档地图

- [上层 README](../README.md) — 项目主页
- [docs/00-架构总览.md](../docs/00-架构总览.md) — 三大流（收录/推送/反馈）
- [docs/01-数据存储设计.md](../docs/01-数据存储设计.md) — SQLite schema + md frontmatter
- [docs/02-Ingest-Pipeline.md](../docs/02-Ingest-Pipeline.md) — 5 步详细
- [docs/06-复习系统.md](../docs/06-复习系统.md) — SM-2 + 心跳 + 周报
- [docs/12-项目档案与关联.md](../docs/12-项目档案与关联.md) — 项目档案系统
- [docs/11-已知问题与TODO.md](../docs/11-已知问题与TODO.md) — 历史已修 + 待办
