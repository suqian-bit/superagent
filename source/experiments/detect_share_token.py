#!/usr/bin/env python3
"""
detect_share_token: 识别抖音/小红书的"长按复制口令"格式，返回友好错误指引主人转换。

放在 /www/content_fetcher/experiments/，agent 在 SOUL.md 引导下，
看到没有 URL 但像分享口令的文本时调这个。

设计原则：不破解 / 不联网，纯本地检测 + 教主人怎么拿到正常链接。
"""
from __future__ import annotations

import json
import re
import sys


DOUYIN_TOKEN_PATTERNS = [
    # 全角包围: ︽︽xxx︽︽ / ︽︽xxxǚǚ
    re.compile(r"[︽ǚ%〝〞⊿]{1,3}([A-Za-z0-9]{8,18})[︽ǚ%〝〞⊿]{1,3}"),
    # "复制打开抖音" 关键词
    re.compile(r"复制(?:.{0,20})打开抖音", re.S),
    re.compile(r"长按复制(?:.{0,20})抖音", re.S),
]

XHS_TOKEN_PATTERNS = [
    # 小红书：4.13 复制本条信息 ...
    re.compile(r"复制本条信息", re.S),
    re.compile(r"小红书.{0,10}笔记", re.S),
]


def detect(text: str) -> dict:
    """返回 {kind, token, has_url, suggestion} 给 agent 用。"""
    # 先看有没有正常 URL（直接放过）
    url_match = re.search(r"https?://\S+", text)
    if url_match:
        return {
            "kind": "has_url",
            "url": url_match.group(0),
            "suggestion": "已经有完整 URL，直接用 video / xhs 命令处理。",
        }

    # 抖音口令
    for pat in DOUYIN_TOKEN_PATTERNS:
        m = pat.search(text)
        if m:
            # 试图抽 token + 标题
            token_match = re.search(r"[︽ǚ%〝〞⊿]{1,3}([A-Za-z0-9]{8,18})", text)
            token = token_match.group(1) if token_match else None

            # 尝试抽标题（通常在【】里）
            title_match = re.search(r"【(.+?)】", text)
            guessed_title = title_match.group(1) if title_match else None

            # 判断是视频还是图文（关键词暗示）
            is_article = any(kw in text for kw in ["阅读文章", "图文", "笔记", "文章"])
            is_video = any(kw in text for kw in ["视频", "看看"]) and not is_article

            if is_article:
                # 图文：抖音 APP 里没"复制链接"选项，主推复制正文
                return {
                    "kind": "douyin_article_token",
                    "token": token,
                    "guessed_title": guessed_title,
                    "has_url": False,
                    "suggestion": (
                        f"这是抖音**图文文章**的分享口令（标题：{guessed_title or '未识别'}）。\n"
                        "\n"
                        "**抖音图文只有「复制口令」没「复制链接」**，所以最方便的办法是\n"
                        "**直接复制正文文字发给我**：\n"
                        "\n"
                        "1. 在抖音 APP 里打开这篇文章\n"
                        "2. 长按正文任意位置，选「全选 → 复制」\n"
                        "3. 在飞书粘贴给我，告诉我「这是抖音文章，标题是 xxx」\n"
                        "\n"
                        "我会把它当纯文本入库，五维评分 / 精华提炼 / 分类 / 反思 全套走一遍，"
                        "效果跟有链接一样。"
                    ),
                    "agent_action_hint": (
                        "如果主人下次直接粘贴正文，调："
                        "echo '<正文>' | text --title '<标题>' --source douyin --from-stdin"
                    ),
                }

            # 视频：APP 里有「复制链接」选项可用
            return {
                "kind": "douyin_video_token",
                "token": token,
                "guessed_title": guessed_title,
                "has_url": False,
                "suggestion": (
                    f"这是抖音**视频**的分享口令（标题：{guessed_title or '未识别'}）。\n"
                    "\n"
                    "拿到可用链接的最快办法：\n"
                    "1. 在抖音 APP 里打开这条视频\n"
                    "2. 点右下角分享按钮\n"
                    "3. 选「复制链接」（不是「复制口令」）\n"
                    "4. 拿到 `https://v.douyin.com/xxxxx/` 后发我，我就能抓 + 转录\n"
                    "\n"
                    "或者：复制视频下方的标题描述发我，我当纯文本处理。"
                ),
                "agent_action_hint": "拿到短链后调 video <URL> 走 ingest pipeline",
            }

    # 小红书口令
    for pat in XHS_TOKEN_PATTERNS:
        if pat.search(text):
            return {
                "kind": "xhs_share_token",
                "has_url": False,
                "suggestion": (
                    "这是小红书的口令，需要从手机 APP 转换：\n"
                    "1. 在小红书 APP 打开这条内容\n"
                    "2. 点右上角分享 → 复制链接\n"
                    "3. 拿到 `http://xhslink.com/xxx` 后发我"
                ),
            }

    # 其他情况
    return {
        "kind": "unknown",
        "has_url": False,
        "suggestion": (
            "这段文本里没找到链接也不像已知分享口令。\n"
            "如果想存内容本身，告诉我「存这段」就当作纯文本入库。"
        ),
    }


def main():
    if len(sys.argv) < 2:
        print("用法: detect_share_token.py '<文本>'")
        sys.exit(1)
    text = sys.argv[1]
    result = detect(text)
    print(json.dumps({"ok": True, **result}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
