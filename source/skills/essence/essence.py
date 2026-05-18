#!/usr/bin/env python3
"""
essence (v2): 拿 step3 分类后的内容，调 DeepSeek 写一份"省得你看原视频"的精华。

设计要点：
- 字数 = 原内容长度 × 18%，最少 300 最多 1800（无论 tier）
- prompt 强调"读者读完你的精华后，应该获得 80% 看原视频的收获"
- 结构化输出：核心论点 / 关键论证 / 具体方法工具 / 金句 / 局限
- 同步生成 3 个反思问题

用法: essence --in /tmp/ingest/<run_id>_step3.json
"""
from __future__ import annotations

import argparse
import sys

sys.path.insert(0, "/root/.hermes/profiles/knowledge/skills/_lib")
import pipeline_io


SYSTEM = (
    "你是一名认真的知识库笔记员。你的目标读者是'没看过原视频/文章的人'。"
    "他们读完你的精华后应该获得 80% 看完原内容的价值，不需要再回去看视频。"
    "你只输出 JSON。"
)


PROMPT_TEMPLATE = """请为下面的内容写一份精华笔记。

# 字数与质量

- **目标字数：约 {target_chars} 字**（参考原文 {content_chars} 字的 18%）
- **如果内容真的水**（重复、口语化、没具体方法），不要凑字数。直接写"作者大多在反复讲 X 观点，没有给出具体方法。要点：A、B"就够了。不要硬撑。
- **如果内容真的干**（密度高、有具体方法），就写到 1800 字上限把要点全包住。

# 写作目标

读者读完你的精华后：
1. **知道作者的中心观点**
2. **能复述出关键的方法/步骤/例子**（不是只知道"作者讲了 XX 话题"）
3. **拿到所有具体的命令、代码、参数、工具名**
4. **看清作者强调的重点和注意点**
5. **能判断这个观点的局限性**

# 输出结构（用 Markdown，可省略不适用的小节）

```
## 🎯 核心论点
（1-2 句话说清作者的中心观点）

## 📚 关键论证 / 步骤 / 例子
（分点列出。这是精华的主体，可以详细。每个要点 2-3 行。）
- 要点 1: ...
- 要点 2: ...
- 要点 3: ...
...

## ⚙️ 具体方法 / 工具 / 代码
（原样保留作者提到的命令、代码、工具名、参数、配置。这部分不要改写，主人会照着做。如果没有就省略此节。）

## ✨ 金句 / 强调点
（作者特别强调或反复提及的观点。如果没有就省略。）

## ⚠️ 局限 / 弱点
（内容的局限性、未覆盖的方面、需要主人补充判断的地方。简短 1-2 句。）
```

# 反思问题

同时生成 2-3 个反思问题，让主人复习时用：
- 概念回忆类（"X 是什么？为什么 X 比 Y 好？"）
- 应用类（"如果你要做 Z，怎么用作者的方法？"）
- 辨析类（"X 跟 Y 有什么区别？什么时候不应该用 X？"）

# 返回 JSON

{{
  "essence": "<完整精华，markdown 格式>",
  "questions": ["问题1", "问题2", "问题3"]
}}

---

# 待精炼内容

标题：{title}
作者：{uploader}
评分：{tier} ({score_total}/100)
亮点：{strength}
弱点：{weakness}
分类：{categories}
标签：{tags}

正文/转录（{content_chars} 字）：
<<<
{content}
>>>
"""


def calc_target_chars(content_chars: int) -> int:
    """精华目标字数 = 内容 × 18%，下限 300，上限 1800。"""
    raw = int(content_chars * 0.18)
    return max(300, min(1800, raw))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="input_file", required=True)
    args = ap.parse_args()

    data = pipeline_io.read_step_file(args.input_file)
    run_id = data.get("run_id")

    tier = data.get("tier", "C")
    content = data.get("content", "")
    content_chars = data.get("content_chars", len(content))
    target_chars = calc_target_chars(content_chars)

    # 内容太长就截，省 token（但 essence 写出来仍然能反映前 12k 字的核心）
    if len(content) > 12000:
        content = content[:12000] + "\n... (后续 {} 字已截断)".format(
            content_chars - 12000)

    cats = ", ".join(f"{c['name']}({c.get('affinity', '?')})"
                     for c in data.get("categories", []))
    tags = ", ".join(data.get("tags", []))
    rationale = data.get("score_rationale", {})

    prompt = PROMPT_TEMPLATE.format(
        target_chars=target_chars,
        title=data.get("title", "无"),
        uploader=data.get("uploader", "无"),
        tier=tier,
        score_total=data.get("score_total", 0),
        strength=rationale.get("strength", "无"),
        weakness=rationale.get("weakness", "无"),
        categories=cats or "(无)",
        tags=tags or "(无)",
        content_chars=content_chars,
        content=content or "(无内容)",
    )

    # 精华允许更多 token（max_tokens 大概是字符数的 1.5 倍粗略估）
    max_tokens = max(1500, target_chars * 2)

    try:
        reply = pipeline_io.call_deepseek(prompt, system=SYSTEM, temperature=0.3,
                                           max_tokens=max_tokens)
        parsed = pipeline_io.parse_json_reply(reply)
    except Exception as e:
        pipeline_io.emit_error("essence", f"DeepSeek 精炼失败: {e}", run_id=run_id,
                                recoverable=True)

    out = dict(data)
    out["essence"] = parsed.get("essence", "")
    out["essence_target_chars"] = target_chars
    out["essence_actual_chars"] = len(out["essence"])
    out["questions"] = parsed.get("questions", [])

    pipeline_io.emit_output("essence", run_id, out, step_num=4)


if __name__ == "__main__":
    main()
