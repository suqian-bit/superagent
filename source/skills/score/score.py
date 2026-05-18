#!/usr/bin/env python3
"""
score: 拿 step1 fetch 的内容，用 DeepSeek 五维打分。

用法: score --in /tmp/ingest/<run_id>_step1.json

输出: step2.json，含 scores / score_total / tier，并指示下一步 classify
"""
from __future__ import annotations

import argparse
import json
import sys

sys.path.insert(0, "/root/.hermes/profiles/knowledge/skills/_lib")
import pipeline_io


SYSTEM = """你是一个严格的知识评分员。你只输出 JSON，不输出其他任何东西。"""

USER_PROMPT_TEMPLATE = """请对以下内容做五维评分。每维 0-25 分（总分 0-100）。

五个维度：
1. density（信息密度）：单位篇幅信息量。25=全是干货，0=全是水话/凑字数
2. actionable（可操作性）：能不能照着做。25=明确告诉你下一步怎么动手，0=纯感想没法落地
3. uniqueness（观点独特性）：vs 烂大街。25=独家视角/新角度，0=到处都能看到的内容
4. reliability（可靠性）：作者权威 + 论据扎实。25=权威人士+扎实论据，0=自媒体复读/无来源
5. reusability（复用价值）：时效性。25=一年后回看还有用，0=3 天就过期

规则：
- 敢给低分，不要全 18-22
- 信息密度差的就给 5-10 分
- 给分前先思考亮点和弱点，再下笔
- 总分必须 = 五维之和

返回 JSON 格式（注意字段名是英文）：
{{
  "scores": {{"density": N, "actionable": N, "uniqueness": N, "reliability": N, "reusability": N}},
  "total": N,
  "tier": "A" | "B" | "C",
  "rationale": {{"strength": "一两句话总结亮点", "weakness": "一两句话总结弱点"}}
}}

tier 规则：>=90 是 A，80-89 是 B，<80 是 C。

待评内容：
标题：{title}
作者：{uploader}
平台：{platform}
正文/转录（{content_chars} 字）：
<<<
{content}
>>>
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="input_file", required=True, help="step1.json 路径")
    args = ap.parse_args()

    data = pipeline_io.read_step_file(args.input_file)
    run_id = data.get("run_id")

    content = data.get("content", "")
    if len(content) > 6000:  # 太长截掉，控成本
        content = content[:6000] + "\n... (后续已截断)"

    prompt = USER_PROMPT_TEMPLATE.format(
        title=data.get("title", "无"),
        uploader=data.get("uploader", "无"),
        platform=data.get("platform", "无"),
        content_chars=data.get("content_chars", 0),
        content=content or "(无文字内容)",
    )

    try:
        reply = pipeline_io.call_deepseek(prompt, system=SYSTEM, temperature=0.0, max_tokens=600)
        parsed = pipeline_io.parse_json_reply(reply)
    except Exception as e:
        pipeline_io.emit_error("score", f"DeepSeek 评分失败: {e}", run_id=run_id,
                                recoverable=True, suggestion="检查 DEEPSEEK_API_KEY 是否有效，或稍后重试")

    scores = parsed.get("scores", {})
    total = parsed.get("total")
    if total is None:
        total = sum(int(scores.get(k, 0)) for k in
                    ["density", "actionable", "uniqueness", "reliability", "reusability"])

    tier = parsed.get("tier")
    if tier not in ("A", "B", "C"):
        tier = "A" if total >= 90 else ("B" if total >= 80 else "C")

    # 把 scores 注入到原 data，往后传
    out = dict(data)
    out["scores"] = scores
    out["score_total"] = int(total)
    out["tier"] = tier
    out["weight"] = int(total)  # 初始 weight = 总分
    out["score_rationale"] = parsed.get("rationale", {})

    pipeline_io.emit_output("score", run_id, out, step_num=2)


if __name__ == "__main__":
    main()
