#!/usr/bin/env python3
"""
实验脚本：尝试把抖音"长按复制口令"解析成可用 URL。

主人发来的格式示例：
  8:/ 01/28 d@a.NW :1pm 【能否使用RAG技术来解决大模型的长期记忆问题？】
  长按复制打开抖音，即可阅读文章 ︽︽nGiLbXgKgr69ǚǚ

观察：
  - 包含中文乱码标记 ︽︽xxxǚǚ 或类似
  - 中间的 nGiLbXgKgr69 看起来是 11-12 位字母数字
  - 跟 v.douyin.com/<code> 的 code 格式一致

假设：
  - 这段口令里的 token = v.douyin.com 短链的 code
  - 直接拼成 https://v.douyin.com/<token>/ 应该能跳转

策略：
  1. 用多个正则 + 启发式从乱七八糟的文本里抽出 token
  2. 用我们已经验证过的 douyin_extractor 接力（短链 → video_id → iesdouyin → mp4）

不动现有 fetch_video.py / fetch_xhs.py / douyin_extractor.py。
"""
from __future__ import annotations

import json
import re
import sys

import httpx


MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) "
    "Version/17.2 Mobile/15E148 Safari/604.1"
)


def extract_tokens(text: str) -> list[str]:
    """
    从分享口令文本里抽 token。尝试多种规则：
      ① 完整 URL（如果有）
      ② 全角包围符里的字母数字: ︽︽xxx︽︽ / ︽︽xxxǚǚ / ︽︽xxx::
      ③ 纯字母数字 6-15 位（去掉日期/时间这种数字串）
    """
    candidates: list[str] = []

    # ① 完整 URL
    for m in re.finditer(r"https?://[^\s]+", text):
        candidates.append(m.group(0))

    # ② 全角包围的 token（︽︽xxx 或 ︽︽xxxǚǚ 之类）
    # 抖音常用包围字符：︽ ǚ % :: 等
    fence_chars = "︽ǚ%:〝〞⊿"
    fence_pattern = rf"[{fence_chars}]+([A-Za-z0-9]{{6,20}})[{fence_chars}]+"
    for m in re.finditer(fence_pattern, text):
        candidates.append(m.group(1))

    # ③ 字母+数字混合，6-15 位（既要字母也要数字，避免纯数字日期）
    for m in re.finditer(r"\b([A-Za-z][A-Za-z0-9]{5,18})\b", text):
        token = m.group(1)
        if any(c.isalpha() for c in token) and any(c.isdigit() for c in token):
            candidates.append(token)

    # 去重保序
    seen = set()
    out = []
    for c in candidates:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out


def try_resolve(token_or_url: str) -> dict | None:
    """
    给一个 token 或 URL，尝试解析成 video_id。
    成功返回 {token, final_url, video_id}，失败返回 None。
    """
    # 如果是 URL，直接用
    if token_or_url.startswith("http"):
        urls_to_try = [token_or_url]
    else:
        # 否则拼几种可能的格式
        urls_to_try = [
            f"https://v.douyin.com/{token_or_url}/",
            f"https://v.douyin.com/{token_or_url}",
            f"https://www.iesdouyin.com/share/video/{token_or_url}/",
        ]

    headers = {
        "User-Agent": MOBILE_UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }

    for url in urls_to_try:
        try:
            with httpx.Client(headers=headers, follow_redirects=True, timeout=15) as c:
                r = c.get(url)
            final = str(r.url)
            # 尝试抽 video_id 或 note_id
            m = re.search(r"/video/(\d+)", final)
            if m:
                return {
                    "token": token_or_url,
                    "tried_url": url,
                    "final_url": final,
                    "video_id": m.group(1),
                    "kind": "video",
                    "status": r.status_code,
                }
            # 图文/笔记可能是 /note/<id>
            m = re.search(r"/note/(\d+)", final)
            if m:
                return {
                    "token": token_or_url,
                    "tried_url": url,
                    "final_url": final,
                    "video_id": m.group(1),
                    "kind": "note",
                    "status": r.status_code,
                }
            # 其他形态：share/video, share/note, share/article
            m = re.search(r"/share/(\w+)/(\d+)", final)
            if m:
                return {
                    "token": token_or_url,
                    "tried_url": url,
                    "final_url": final,
                    "video_id": m.group(2),
                    "kind": m.group(1),
                    "status": r.status_code,
                }
            # 没匹配到 id，但状态 200，记下来调试
            if r.status_code == 200 and "douyin.com" in final:
                print(f"  [debug] {url} → {final} (200 但没抽到 id)", file=sys.stderr)
        except Exception as e:
            print(f"  [error] {url}: {e}", file=sys.stderr)
            continue

    return None


def main():
    if len(sys.argv) < 2:
        print("用法: exp_parse_douyin_token.py '<分享口令文本>'")
        sys.exit(1)

    text = sys.argv[1]
    print(f"=== 输入文本 ===")
    print(text)
    print()

    tokens = extract_tokens(text)
    print(f"=== 抽出的候选 token ({len(tokens)} 个) ===")
    for t in tokens:
        print(f"  - {t}")
    print()

    if not tokens:
        print(json.dumps({"ok": False, "error": "没从文本里抽出 token"}, ensure_ascii=False))
        return

    # 优先级：URL > 全角包围里的 > 短字母数字
    print(f"=== 尝试解析 ===")
    for token in tokens:
        print(f"\n→ 试 {token}")
        result = try_resolve(token)
        if result:
            print(f"  ✅ 命中: kind={result['kind']}, video_id={result['video_id']}")
            print(f"     final_url={result['final_url']}")
            print()
            print(json.dumps({"ok": True, **result}, ensure_ascii=False))
            return

    print()
    print(json.dumps({"ok": False, "error": "所有 token 都解析失败",
                      "tried": tokens}, ensure_ascii=False))


if __name__ == "__main__":
    main()
