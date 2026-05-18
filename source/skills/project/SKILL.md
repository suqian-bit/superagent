---
name: project
description: 项目档案管理（对话驱动）。主人说"我在研究 X / 在做 Y / 卡在 Z"时，引导建立或更新项目档案，让助理知道主人当前在做什么。
version: 1.0.0
metadata:
  hermes:
    tags: [project, profile, context, productivity]
---

# Project Profile

当主人提到"在研究/在做/想学 X"或"项目进度/卡点变化"时使用。

## 命令速查

```bash
project questions             # 拿引导问题清单（建新档案前调）
project list [--all]          # 看所有项目
project show <name>           # 看某个项目档案
project new <name> <json>     # 建新档案（answers JSON）
project update <name> --section <sec> --text "..." --mode append/replace
project link <item> <name> <relevance> [--reason "..."]
project unlink <item> <name>
project relevance <item>      # 看一条 item 关联了哪些 project
project archive <name> [--status done/paused]
```

详细引导规则在 SOUL.md 的 "## 项目档案引导" 章节。
