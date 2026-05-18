---
title: 知识助手项目主页
created: 2026-05-12
updated: 2026-05-14
tags: [meta, index]
---

# 知识助手 · Mnemo

一个驻扎在飞书里的私人学习陪伴 AI。

**核心痛点解决**：
- 只收藏不消化 → AI 自动评分 + 主动推送复习
- 重复收藏 → 模糊指纹查重（jaccard ≥ 0.8）
- 评分标准漂移 → 五维评分 prompt 固化在脚本里
- 工具选择不稳 → Pipeline `_next_action` 强约束

---

## 项目状态（2026-05-14）

| 模块 | 状态 | 备注 |
|------|------|------|
| 5 步 Ingest Pipeline | ✅ | fetch → score → classify → essence → save |
| 北京时间统一 | ✅ | `+08:00` ISO 格式 |
| 模糊指纹查重 | ✅ | Jaccard ≥ 0.8 |
| 五维评分 + tier 分级 + 权重 | ✅ | DeepSeek 调用，prompt 固化 |
| 动态精华字数（原文 18%，300-1800 字）| ✅ | 2026-05-13 改造：目标"省去看视频" |
| md 文件保留原件 + frontmatter 结构化 | ✅ | |
| 多分类（affinity）+ 多标签（双表）| ✅ | |
| 自动复习推送（cron 9/13/21 北京时间）| ✅ | |
| 复习反馈回写（SM-2）| ✅ | `pending-review → review` |
| 手动 tier 调整（promote/demote/archive）| ✅ | 2026-05-13 新增 |
| 飞书 API 直推 | ✅ | 绕开 Hermes gateway |
| 抖音抓取 + 转录 | ✅ | 2026-05-12 突破（[[meta/2026-05-12-抖音突破\|详见]]） |
| 周报 cron（每周日 22:00）| ✅ | 2026-05-13 上线，含 AI 观察 |
| HERMES_MAX_ITERATIONS=180 | ✅ | 2026-05-13 调高，防长视频超时 |
| **🆕 项目档案（projects + 7 问引导）** | ✅ | **2026-05-14 上线**：`project new/show/update/link/archive` |
| **🆕 入库自动评估项目关联** | ✅ | **classify 后多调一次 LLM 评 0-100 关联度** |
| **🆕 建项目时自动反扫老库存** | ✅ | **新建项目 → 扫已有 items → relevance≥60 自动 link** |
| **🆕 场景化检索（digest impact）** | ✅ | **主人卡住 → 翻库找相关 → 返回 Top 8** |
| **🆕 心跳推送（没 due 早 9 点也通知）** | ✅ | **cron 不再无声** |
| RAG 检索 | ❌ TODO | 库存攒到 30+ 条再做（`digest impact` 已覆盖 80%）|
| Obsidian 双向同步 | ❌ TODO | 服务器 md → Mac 本地 git pull |
| 对接用户代码 | ❌ Phase 6 | grep `/root/quant_strategies/` 等，精确定位代码段 |
| 行为感知动态调度 | ❌ TODO | 学习高活时段 |

---

## 🌟 当前形态：从"被动仓库"进化成"主动连接"

```
你正在做事
   ↓
助理读过你的 projects 档案，知道你在干嘛
   ↓
你刷到内容 → 助理评估"这条跟你的 quant/llm-agent 项目相关度"
   ↓
飞书消息直接显示 🎯 跟 quant 项目关联 85，命中卡点 X
   ↓
你卡住时说"我在做 X" → 助理调 digest impact 翻库 → 找出库里能用的
   ↓
"想不起以前收过相关的"问题彻底解决
```

详见 [[meta/2026-05-14-从仓库到助理]] 和 [[docs/12-项目档案与关联]]。

---

## 文档地图

```
docs/
├── 00-架构总览.md          ← 看整体先看这个
├── 01-数据存储设计.md
├── 02-Ingest-Pipeline.md
├── 03-评分与分级.md
├── 04-智能分类系统.md
├── 05-查重机制.md
├── 06-复习系统.md
├── 07-Hermes-Skill链路.md
├── 08-命令速查.md
├── 09-时区与时间约定.md
├── 10-飞书集成.md
└── 11-已知问题与TODO.md
```

| 文档 | 一句话简介 |
|------|----------|
| [[docs/00-架构总览]] | 三大流（收录 / 推送 / 反馈）+ 组件位置 |
| [[docs/01-数据存储设计]] | `/www/knowledge/` 目录、SQLite schema、md frontmatter |
| [[docs/02-Ingest-Pipeline]] | 5 步 chain：fetch → score → classify → essence → save |
| [[docs/03-评分与分级]] | 五维评分维度、tier 阈值、tier-aware 精华字数 |
| [[docs/04-智能分类系统]] | categories vs tags、affinity 关联度、动态新增 |
| [[docs/05-查重机制]] | 精确 + 模糊（token-set Jaccard） |
| [[docs/06-复习系统]] | SM-2 算法、推送模式、反馈回写 |
| [[docs/07-Hermes-Skill链路]] | SOUL.md / SKILL.md / `_next_action` 强制路由 |
| [[docs/08-命令速查]] | 路径、命令、systemd、crontab 全清单 |
| [[docs/09-时区与时间约定]] | 一律北京时间，UTC 服务器的 cron 换算 |
| [[docs/10-飞书集成]] | Bot 配置、open_id 识别、推送 API |
| [[docs/11-已知问题与TODO]] | 阻塞项、待做项 |
| [[docs/12-项目档案与关联]] | **🆕** 项目档案 + 入库自动关联 + 场景化检索 |

---

## 仓库布局

```
superagent/                                # Obsidian vault 根 + Git 项目根
├── README.md                              ← 你正在看
├── docs/                                  ← 架构文档（13 篇 + 1 个新增）
├── source/                                ← 完整源码 + 部署脚本（GitHub 项目主体）
│   ├── SOUL.md
│   ├── skills/                            ← Hermes skills 全套
│   ├── experiments/                       ← OCR / stash / detect 等辅助工具
│   └── deploy/                            ← install.sh + .env.example + crontab + systemd
├── items/                                 ← 自动收录的内容 md（gitignored）
├── meta/                                  ← 设计思考、changelog
└── assets/                                ← 图片
```

**这是个双重身份的仓库**：既是 Obsidian vault（你在本地查 docs/、写笔记），也是 GitHub 项目（别人 git clone 后能完整复现）。

部署到新服务器只需：
```bash
git clone https://github.com/<you>/superagent.git
cd superagent/source
sudo bash deploy/install.sh
```

详见 [[source/README]]。

---

## 设计哲学（5 条铁律）

1. **底层数据是真理来源**。md 文件保留原文 + 结构化元数据，SQLite 只做索引加速。删了 SQLite 也能从 md 重建。
2. **流程链确定性 > Agent 灵活性**。Pipeline 5 步用 `_next_action` 字段串起来强约束，不靠 Agent "记得调下一步"。
3. **语义判断在脚本里调 LLM**。评分、分类、精华都在脚本内部，prompt 固化，不随 agent 心情漂移。
4. **Cron + 飞书 API 直推**。主动推送不依赖 Agent 主动找你。
5. **失败诚实**。脚本失败明确说错误，禁止 Agent 看封面图脑补。

---

## 快速上手

**主人侧（飞书）**：
- 刷到好东西 → 在「知识学习助手」对话框粘链接
- 等 30-60 秒，看 5 步 pipeline 跑完 → 评分+精华+反思 3 问
- 每天 9 / 13 / 21（北京时间）会主动推一条复习题
- 回数字 1-5 或文字答复 → 自动更新下次复习间隔

**运维侧（服务器 154.9.232.37）**：
```bash
# 看实时日志
journalctl --user -u hermes-gateway-knowledge -f

# 看库存
digest stats
digest list --limit 10

# 手动推一条复习
review-push

# 看推送历史
tail /var/log/review_pusher.log

# 看 cron
crontab -l | grep review-push
```

详见 [[docs/08-命令速查]]。

---

## 关键路径速查（最常用）

| 用途 | 路径 |
|------|------|
| 知识库根 | `/www/knowledge/` |
| SQLite 索引 | `/www/knowledge/index.db` |
| md 文件（按日期）| `/www/knowledge/items/YYYY-MM-DD/<id>_<指纹>.md` |
| Hermes profile | `/root/.hermes/profiles/knowledge/` |
| Skills | `/root/.hermes/profiles/knowledge/skills/` |
| Pipeline 命令 | `/usr/local/bin/{xhs,video,score,classify,essence,save,digest,review-push}` |
| 复习推送 cron 日志 | `/var/log/review_pusher.log` |
| 服务器 SSH | `ssh -i ~/.ssh/id_ed25519_codex_server root@154.9.232.37` |
