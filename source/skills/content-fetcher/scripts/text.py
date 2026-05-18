#!/usr/bin/env python3
"""
text (Pipeline Step 1 替代入口): 纯文本入库。

适用场景：
- 抖音图文/长文章（只有"复制口令"没有"复制链接"），主人复制正文文字粘贴过来
- 微信公众号文章（用户复制全文）
- 主人自己手打的笔记/反思
- 其他没 URL 但有内容的素材

用法：
  text --title "标题" --content "正文" [--uploader "作者"] [--source "douyin"] [--url "可选URL"]
  text --title "..." --from-stdin   # 正文从 stdin 读（推荐：避免命令行长字符串）

输出: step1.json + _next_action: score --in ...

跟 xhs/video 输出格式完全一致，无缝接入现有 pipeline。
"""
from __future__ import annotations

import argparse
import sys

sys.path.insert(0, "/root/.hermes/profiles/knowledge/skills/_lib")
import pipeline_io


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--title", required=True, help="内容标题（必填）")
    ap.add_argument("--content", default=None, help="正文文本（如长则用 --from-stdin）")
    ap.add_argument("--from-stdin", action="store_true", help="正文从 stdin 读")
    ap.add_argument("--source", default="text",
                    help="来源平台，例：douyin/wechat/zhihu/text，默认 text")
    ap.add_argument("--url", default="", help="可选：原始 URL（如果有）")
    ap.add_argument("--uploader", default="", help="可选：作者")
    ap.add_argument("--tags-seed", default="", help="可选：作者标签，逗号分隔")
    args = ap.parse_args()

    # 拿正文
    if args.from_stdin:
        content = sys.stdin.read().strip()
    elif args.content:
        content = args.content
    else:
        pipeline_io.die("缺少正文：用 --content 或 --from-stdin")

    if len(content) < 10:
        pipeline_io.die(f"正文过短（{len(content)} 字），可能没复制全。至少 10 字。")

    title = args.title.strip()
    if not title:
        pipeline_io.die("--title 不能为空")

    tags_seed = [t.strip() for t in args.tags_seed.split(",") if t.strip()]

    run_id = pipeline_io.new_run_id()
    payload = {
        "ok": True,
        "platform": args.source,
        "url": args.url,
        "title": title,
        "uploader": args.uploader,
        "tags_seed": tags_seed,
        "publish_time": None,
        "stats": {},
        "content": content,
        "content_chars": len(content),
        "media": {
            "input_method": "text_paste",
        },
    }
    pipeline_io.emit_output("fetch", run_id, payload, step_num=1)


if __name__ == "__main__":
    main()
