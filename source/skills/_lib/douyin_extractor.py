"""
douyin_extractor: 用 iesdouyin.com 老分享接口拿无水印 mp4 URL + 元数据。

绕过 yt-dlp / a_bogus 签名校验。原理：
  v.douyin.com/xxx       (短链)
    → redirect →
  iesdouyin.com/share/video/<id>/?...   (老接口，只需 iPhone UA)
    → 解析 HTML 里嵌入的 window._ROUTER_DATA JSON
    → 拿到 play_addr.url_list[0]，把 "playwm" 替换 "play" 拿到无水印

调用入口：extract_douyin(url) → dict 或 raise
"""
from __future__ import annotations

import json
import re
from typing import Optional

import httpx


MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) "
    "Version/17.2 Mobile/15E148 Safari/604.1"
)

HEADERS = {
    "User-Agent": MOBILE_UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

# 超时统一调一处（跟 fetch_video.py 对齐）
TIMEOUT_HTTPX = 90


def _extract_video_id(url: str) -> Optional[str]:
    """从抖音 URL 提取 video id。支持短链、长链。"""
    # 直接命中长链
    m = re.search(r"/video/(\d+)", url)
    if m:
        return m.group(1)
    # 短链或其他形态：跟随重定向
    with httpx.Client(headers=HEADERS, follow_redirects=True, timeout=TIMEOUT_HTTPX) as c:
        r = c.get(url)
    m = re.search(r"/video/(\d+)", str(r.url))
    if m:
        return m.group(1)
    # 兜底：share/video/<id>/ 形态
    m = re.search(r"/share/video/(\d+)", str(r.url))
    if m:
        return m.group(1)
    return None


def _fetch_router_data(video_id: str) -> dict:
    """请求 iesdouyin 老分享页，抽 window._ROUTER_DATA。"""
    share_url = f"https://www.iesdouyin.com/share/video/{video_id}/"
    with httpx.Client(headers=HEADERS, follow_redirects=True, timeout=TIMEOUT_HTTPX) as c:
        r = c.get(share_url)
    if r.status_code != 200:
        raise RuntimeError(f"iesdouyin 返回 {r.status_code}")

    m = re.search(r"window\._ROUTER_DATA\s*=\s*(.*?)</script>", r.text, re.DOTALL)
    if not m:
        raise RuntimeError("没在 HTML 里找到 _ROUTER_DATA（页面结构变了？）")

    raw = m.group(1).strip().rstrip(";")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"_ROUTER_DATA JSON 解析失败: {e}")


def _locate_video_info(router_data: dict) -> dict:
    """在 loaderData 里找含 videoInfoRes.item_list[0] 的那一份。"""
    loader = router_data.get("loaderData", {})
    if not isinstance(loader, dict):
        raise RuntimeError(f"loaderData 不是 dict: {type(loader)}")

    for k, v in loader.items():
        if not isinstance(v, dict):
            continue
        if "videoInfoRes" not in v:
            continue
        items = v["videoInfoRes"].get("item_list", [])
        if items:
            return items[0]

    raise RuntimeError(f"没找到 videoInfoRes.item_list。loaderData keys: {list(loader.keys())}")


def extract_douyin(url: str) -> dict:
    """
    主入口。给定抖音 URL（短链或长链），返回：
      {
        "aweme_id": str,
        "title": str,        # 抖音的 desc 字段，第一行
        "description": str,  # 完整 desc
        "author": str,
        "duration_s": float,
        "video_url": str,            # 去水印 mp4
        "video_url_watermark": str,  # 原始带水印
        "cover_url": str,
        "tags": list[str],   # 从 desc 抽 # 话题
      }

    失败 raise RuntimeError，调用方 catch 转 JSON error。
    """
    video_id = _extract_video_id(url)
    if not video_id:
        raise RuntimeError(f"无法从 URL 解析 video_id: {url}")

    router_data = _fetch_router_data(video_id)
    item = _locate_video_info(router_data)

    desc = item.get("desc", "") or ""
    play_addr = item.get("video", {}).get("play_addr", {})
    url_list = play_addr.get("url_list", [])
    if not url_list:
        raise RuntimeError("video.play_addr.url_list 为空")

    raw_url = url_list[0]
    no_wm = raw_url.replace("playwm", "play")

    # 抽 # 话题
    tags = re.findall(r"#([^\s#]+)", desc)

    # 标题取第一行（去掉话题），fallback 整个 desc
    title_text = re.sub(r"#[^\s#]+", "", desc).strip()
    title = (title_text.split("\n", 1)[0] or desc).strip()[:80]

    cover = item.get("video", {}).get("cover", {}).get("url_list", [None])[0] or \
            item.get("video", {}).get("origin_cover", {}).get("url_list", [None])[0]

    return {
        "aweme_id": item.get("aweme_id") or video_id,
        "title": title or "（无标题）",
        "description": desc,
        "author": item.get("author", {}).get("nickname", ""),
        "author_id": item.get("author", {}).get("uid") or item.get("author", {}).get("sec_uid", ""),
        "duration_s": (item.get("duration") or 0) / 1000.0,
        "video_url": no_wm,
        "video_url_watermark": raw_url,
        "cover_url": cover,
        "tags": tags,
        "create_time": item.get("create_time"),
        "stats": {
            "digg": item.get("statistics", {}).get("digg_count"),     # 点赞
            "comment": item.get("statistics", {}).get("comment_count"),
            "share": item.get("statistics", {}).get("share_count"),
            "play": item.get("statistics", {}).get("play_count"),
        },
    }


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("用法: python douyin_extractor.py <抖音URL>")
        sys.exit(1)
    try:
        result = extract_douyin(sys.argv[1])
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except Exception as e:
        print(json.dumps({"error": str(e)}, ensure_ascii=False))
        sys.exit(1)
