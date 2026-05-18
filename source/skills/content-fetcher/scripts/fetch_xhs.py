#!/usr/bin/env python3
"""
小红书内容抓取：标题、正文、图片URL、视频URL、作者
对视频笔记可选自动转录（--transcribe）

用法:
  fetch_xhs.py <URL>                  # 拿数据，不下载，不转录
  fetch_xhs.py <URL> --transcribe     # 拿数据 + 下载视频 + 转录文案
  fetch_xhs.py <URL> --download       # 拿数据 + 下载图片/视频
输出: JSON 到 stdout
"""
import argparse
import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, "/root/.hermes/profiles/knowledge/skills/_lib")
import pipeline_io

# XHS-Downloader 在 /www/XHS-Downloader 是 clone 来的，需要把它加到 path
sys.path.insert(0, "/www/XHS-Downloader")

# ──────────────────────────────────────────────
# 超时配置（统一调一处，跟 fetch_video.py 对齐）
# ──────────────────────────────────────────────
TIMEOUT_XHS_NET     = 30      # XHS-Downloader 网络请求
TIMEOUT_VIDEO_DL    = 900     # yt-dlp 下载小红书视频
TIMEOUT_FFMPEG      = 600     # ffmpeg 抽音频

WORK = Path("/www/content_fetcher/xhs")
WORK.mkdir(parents=True, exist_ok=True)

# 小红书 cookies 默认位置（可选，影响视频清晰度）
XHS_COOKIE_FILE = Path("/www/content_fetcher/cookies/xhs_cookie.txt")


def err(msg, code=1):
    print(json.dumps({"ok": False, "error": msg}, ensure_ascii=False))
    sys.exit(code)


async def main_async():
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("--no-download", action="store_true", help="只取数据不下载文件")
    ap.add_argument("--transcribe", action="store_true", help="视频笔记自动转录（默认 base 模型）")
    ap.add_argument("--whisper-model", default="base", help="whisper 模型大小")
    ap.add_argument("--raw", action="store_true", help="旧 raw 输出模式（不走 pipeline）")
    args = ap.parse_args()

    url = args.url.strip()

    # ---- URL 格式预检 ----
    # 有效形态：
    #   1) xhslink.com/xxx 短链（小红书 APP 分享，带 token 参数）
    #   2) www.xiaohongshu.com/explore/xxx?xsec_token=xxx（完整 URL）
    #   3) www.xiaohongshu.com/discovery/item/xxx?xsec_token=xxx
    is_short = "xhslink.com" in url
    is_explore = "xiaohongshu.com/explore" in url or "xiaohongshu.com/discovery" in url
    has_token = "xsec_token=" in url

    if not is_short and not is_explore:
        err(
            f"URL 看起来不像小红书链接：{url}\n"
            "支持的格式：\n"
            "  - http://xhslink.com/xxx（手机 APP 分享出来的短链）\n"
            "  - https://www.xiaohongshu.com/explore/xxx?xsec_token=xxx（带 token 的完整链接）"
        )

    if is_explore and not has_token:
        err(
            f"小红书 explore 链接缺少 xsec_token 参数：{url}\n"
            "原因：小红书 2024 年起的反爬策略，纯 ID 的 URL 拿不到数据。\n"
            "解决：从手机小红书 APP 点'分享 → 复制链接'，应该会给你带 xsec_token 的完整 URL，"
            "或者直接用 xhslink.com 短链。"
        )

    try:
        from source import XHS
    except ImportError as e:
        err(f"XHS 模块导入失败: {e}")

    cookie = ""
    if XHS_COOKIE_FILE.exists():
        cookie = XHS_COOKIE_FILE.read_text(encoding="utf-8").strip()

    try:
        async with XHS(
            work_path=str(WORK),
            folder_name="downloads",
            cookie=cookie,
            timeout=TIMEOUT_XHS_NET,
            max_retry=3,
            download_record=False,
            image_download=not args.no_download,
            video_download=not args.no_download,
            language="zh_CN",
        ) as xhs:
            result = await xhs.extract(url, download=not args.no_download)
    except Exception as e:
        err(f"XHS 抓取异常: {e}")

    # XHS-Downloader 返回的是 list of dict (即使一个作品也是)
    if isinstance(result, list):
        if not result:
            err(
                "XHS 返回空。可能原因：\n"
                "  1) 链接已删除/作者设私密\n"
                "  2) xsec_token 失效（小红书 token 有时效性，通常几小时）\n"
                "  3) IP 被风控（短期内访问太多）\n"
                "建议：从手机 APP 重新分享一次拿新 token，或换条链接试试。"
            )
        item = result[0]
    elif isinstance(result, dict):
        item = result
    else:
        err(f"未知返回类型: {type(result)}")

    if not item:
        err(
            "XHS 返回空 dict。多半是 xsec_token 失效或链接被风控，"
            "从手机 APP 重新分享一次拿到新链接再试。"
        )

    # 抽取关键字段（XHS-Downloader 字段名是中文）
    out = {
        "ok": True,
        "platform": "xiaohongshu",
        "title": item.get("作品标题") or item.get("title", ""),
        "content": item.get("作品描述") or item.get("description", ""),
        "type": item.get("作品类型") or item.get("type", ""),  # 视频/图文
        "uploader": item.get("作者昵称") or item.get("nickname", ""),
        "uploader_id": item.get("作者ID") or item.get("user_id", ""),
        "tags": item.get("作品标签") or item.get("tags", []),
        "publish_time": item.get("发布时间") or item.get("publish_time", ""),
        "like_count": item.get("点赞数量") or 0,
        "collect_count": item.get("收藏数量") or 0,
        "comment_count": item.get("评论数量") or 0,
        "url": args.url,
    }

    # 视频 URL（如果是视频笔记）
    video_url = item.get("下载地址") or item.get("video_url")
    if video_url:
        if isinstance(video_url, list):
            out["video_urls"] = video_url
            out["video_url"] = video_url[0] if video_url else None
        else:
            out["video_url"] = video_url

    # 图片 URL
    image_urls = item.get("图片下载地址") or item.get("image_urls", [])
    if image_urls:
        out["image_urls"] = image_urls if isinstance(image_urls, list) else [image_urls]
        out["image_count"] = len(out["image_urls"])

    # 把整个原始 dict 也保留（防止有用字段没抽出来）
    out["_raw"] = {k: v for k, v in item.items() if k != "作品文件"}

    # ---- 视频笔记自动转录 ----
    if args.transcribe and out.get("video_url") and "视频" in out.get("type", ""):
        try:
            video_id = item.get("作品ID", "xhs_video")
            mp4_path = Path("/www/content_fetcher/downloads") / f"xhs_{video_id}.mp4"
            mp3_path = Path("/www/content_fetcher/downloads") / f"xhs_{video_id}.mp3"
            txt_path = Path("/www/content_fetcher/transcripts") / f"xhs_{video_id}.txt"
            mp4_path.parent.mkdir(parents=True, exist_ok=True)
            txt_path.parent.mkdir(parents=True, exist_ok=True)

            if not txt_path.exists():
                # 下载 mp4
                if not mp4_path.exists():
                    subprocess.run(
                        ["/www/content_fetcher_venv/bin/yt-dlp", "-o", str(mp4_path),
                         "--no-warnings", out["video_url"]],
                        check=True, capture_output=True, timeout=TIMEOUT_VIDEO_DL,
                    )
                # 抽音频
                if not mp3_path.exists():
                    subprocess.run(
                        ["ffmpeg", "-y", "-i", str(mp4_path), "-vn",
                         "-acodec", "libmp3lame", "-q:a", "5", str(mp3_path)],
                        check=True, capture_output=True, timeout=TIMEOUT_FFMPEG,
                    )
                # 转录
                from faster_whisper import WhisperModel
                model = WhisperModel(
                    args.whisper_model, device="cpu", compute_type="int8",
                    download_root="/www/whisper_models",
                )
                segs, _ = model.transcribe(
                    str(mp3_path), language="zh", beam_size=1,
                    initial_prompt="以下是普通话简体中文的转录内容。",
                )
                text = "".join(s.text for s in segs).strip()
                txt_path.write_text(text, encoding="utf-8")

            full_text = txt_path.read_text(encoding="utf-8")
            out["transcript_path"] = str(txt_path)
            out["transcript_chars"] = len(full_text)
            out["transcript_full"] = full_text
        except Exception as e:
            out["transcript_error"] = str(e)

    # 输出给 agent：要么走 pipeline（默认），要么 raw 模式（旧脚本兼容）
    if args.raw:
        print(json.dumps(out, ensure_ascii=False, default=str))
    else:
        run_id = pipeline_io.new_run_id()
        # raw_content 是给后续 score/classify/essence 用的"内容主体"
        raw_content = out.get("transcript_full") or out.get("content") or ""
        payload = {
            "ok": True,
            "platform": "xiaohongshu",
            "url": out.get("url"),
            "title": out.get("title", ""),
            "uploader": out.get("uploader", ""),
            "tags_seed": out.get("tags", "").split() if isinstance(out.get("tags"), str) else out.get("tags", []),
            "publish_time": out.get("publish_time"),
            "stats": {
                "like": out.get("like_count"),
                "collect": out.get("collect_count"),
                "comment": out.get("comment_count"),
            },
            "content": raw_content,
            "content_chars": len(raw_content),
            "media": {
                "video_url": out.get("video_url"),
                "image_urls": out.get("image_urls", []),
                "image_count": out.get("image_count", 0),
                "transcript_path": out.get("transcript_path"),
            },
        }
        pipeline_io.emit_output("fetch", run_id, payload, step_num=1)


def main():
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
