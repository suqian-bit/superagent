#!/usr/bin/env python3
"""
通用视频抓取脚本：B站 / 抖音 / 快手 / YouTube 等 yt-dlp 支持的平台
用法: fetch_video.py <URL> [--no-transcribe] [--cookies /path/to/cookies.txt] [--model base|small|medium]
输出: JSON 到 stdout，含元数据 + 转录文本路径
"""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, "/root/.hermes/profiles/knowledge/skills/_lib")
import pipeline_io

WORK = Path("/www/content_fetcher")
DOWNLOADS = WORK / "downloads"
TRANSCRIPTS = WORK / "transcripts"
WHISPER_CACHE = "/www/whisper_models"
YTDL = "/www/content_fetcher_venv/bin/yt-dlp"
PY = "/www/content_fetcher_venv/bin/python"

# 抖音 cookies 默认位置（用户可放这里，避免每次传 --cookies）
DOUYIN_COOKIES = Path("/www/content_fetcher/cookies/douyin.txt")

# ──────────────────────────────────────────────────────────
# 超时配置（统一调一处）
#   长视频 + 慢网 + whisper 转录时，60-300s 不够，全部给宽。
# ──────────────────────────────────────────────────────────
TIMEOUT_METADATA = 180     # yt-dlp 拿元数据
TIMEOUT_DOWNLOAD = 900     # yt-dlp 下载音频/视频（15 分钟，应付长视频）
TIMEOUT_HTTPX    = 90      # httpx 抓页面（如抖音 SPA）
TIMEOUT_FFMPEG   = 600     # ffmpeg 抽音频（长视频也够）

DOWNLOADS.mkdir(parents=True, exist_ok=True)
TRANSCRIPTS.mkdir(parents=True, exist_ok=True)


def err(msg, code=1):
    print(json.dumps({"ok": False, "error": msg}, ensure_ascii=False))
    sys.exit(code)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("--no-transcribe", action="store_true", help="只抓元数据+音频，不转录")
    ap.add_argument("--cookies", default=None, help="cookies.txt 路径")
    ap.add_argument("--model", default="base", choices=["tiny", "base", "small", "medium"])
    ap.add_argument("--lang", default="zh")
    ap.add_argument("--raw", action="store_true", help="旧 raw 输出模式（不走 pipeline）")
    args = ap.parse_args()

    url = args.url.strip()

    # ---- 抖音 URL 走专用 extractor（绕开 yt-dlp 反爬）----
    if "douyin.com" in url or "iesdouyin.com" in url:
        handle_douyin(url, args)
        return

    # 快手等仍走 yt-dlp + cookies
    cookies = args.cookies
    if not cookies and "kuaishou.com" in url:
        if DOUYIN_COOKIES.exists():
            cookies = str(DOUYIN_COOKIES)

    # ---- 第一步：抓元数据 ----
    cmd = [YTDL, "--skip-download", "--print-json", "--no-warnings"]
    if cookies:
        cmd += ["--cookies", cookies]
    cmd.append(url)

    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT_METADATA)
    except subprocess.TimeoutExpired:
        err(f"yt-dlp 元数据抓取超时（{TIMEOUT_METADATA}s）")

    if r.returncode != 0:
        msg = r.stderr.strip().split("\n")[-1] if r.stderr else "未知错误"
        if "Fresh cookies" in msg or "cookies" in msg.lower():
            err(f"需要 cookies 文件。请在浏览器导出 cookies 放到 {DOUYIN_COOKIES} (或用 --cookies 指定)。原始错误: {msg}")
        err(f"yt-dlp 失败: {msg}")

    try:
        meta = json.loads(r.stdout.split("\n")[0])
    except Exception as e:
        err(f"yt-dlp 元数据解析失败: {e}")

    info = {
        "ok": True,
        "platform": meta.get("extractor", "?"),
        "title": meta.get("title", ""),
        "uploader": meta.get("uploader", ""),
        "duration_s": int(meta.get("duration") or 0),
        "description": (meta.get("description") or "")[:500],
        "url": url,
        "video_id": meta.get("id", ""),
    }

    if args.no_transcribe:
        if args.raw:
            print(json.dumps(info, ensure_ascii=False))
        else:
            emit_pipeline_output(info, args, url, transcript_text="")
        return

    # ---- 第二步：下载音频 ----
    safe_id = info["video_id"].replace("/", "_")[:50] or "audio"
    audio_path = DOWNLOADS / f"{safe_id}.mp3"

    if not audio_path.exists():
        cmd = [
            YTDL, "-f", "ba/b",
            "--extract-audio", "--audio-format", "mp3", "--audio-quality", "9",
            "-o", str(DOWNLOADS / f"{safe_id}.%(ext)s"),
            "--no-warnings",
        ]
        if cookies:
            cmd += ["--cookies", cookies]
        cmd.append(url)

        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT_DOWNLOAD)
        except subprocess.TimeoutExpired:
            err(f"音频下载超时（{TIMEOUT_DOWNLOAD // 60} 分钟）")

        if r.returncode != 0:
            err(f"音频下载失败: {r.stderr.strip().split(chr(10))[-1] if r.stderr else '?'}")

        if not audio_path.exists():
            # 找一下别的扩展名
            cands = list(DOWNLOADS.glob(f"{safe_id}.*"))
            if cands:
                audio_path = cands[0]
            else:
                err("音频下载完成但找不到文件")

    info["audio_path"] = str(audio_path)
    info["audio_size_mb"] = round(audio_path.stat().st_size / 1024 / 1024, 2)

    # ---- 第三步：whisper 转录 ----
    transcript_path = TRANSCRIPTS / f"{safe_id}.txt"

    if not transcript_path.exists():
        try:
            from faster_whisper import WhisperModel
        except ImportError:
            err("faster-whisper 未安装")

        try:
            model = WhisperModel(args.model, device="cpu", compute_type="int8", download_root=WHISPER_CACHE)
            # 强制简体中文 prompt（解决繁体输出问题）
            initial_prompt = "以下是普通话简体中文的转录内容。" if args.lang == "zh" else None
            segments, det = model.transcribe(
                str(audio_path),
                language=args.lang,
                beam_size=1,
                initial_prompt=initial_prompt,
            )
            text = "".join(s.text for s in segments).strip()
            transcript_path.write_text(text, encoding="utf-8")
        except Exception as e:
            err(f"whisper 转录失败: {e}")

    text = transcript_path.read_text(encoding="utf-8")
    info["transcript_path"] = str(transcript_path)
    info["transcript_chars"] = len(text)
    info["transcript_preview"] = text[:300]
    info["transcript_full"] = text  # agent 可以直接用

    if args.raw:
        print(json.dumps(info, ensure_ascii=False))
    else:
        emit_pipeline_output(info, args, url, transcript_text=text)


def handle_douyin(url: str, args) -> None:
    """抖音专用：iesdouyin 老接口拿 mp4 URL + 直接下载 + faster-whisper 转录。"""
    sys.path.insert(0, "/root/.hermes/profiles/knowledge/skills/_lib")
    from douyin_extractor import extract_douyin, MOBILE_UA

    try:
        info = extract_douyin(url)
    except Exception as e:
        err(f"抖音解析失败: {e}")

    aweme_id = info["aweme_id"]
    safe_id = f"douyin_{aweme_id}"
    mp4_path = DOWNLOADS / f"{safe_id}.mp4"
    mp3_path = DOWNLOADS / f"{safe_id}.mp3"
    txt_path = TRANSCRIPTS / f"{safe_id}.txt"

    # 下载 mp4（用 httpx，带 iPhone UA）
    if not mp4_path.exists():
        import httpx
        try:
            with httpx.Client(headers={"User-Agent": MOBILE_UA},
                              follow_redirects=True, timeout=TIMEOUT_HTTPX) as c:
                with c.stream("GET", info["video_url"]) as r:
                    if r.status_code != 200:
                        err(f"抖音 mp4 下载失败: HTTP {r.status_code}")
                    with open(mp4_path, "wb") as f:
                        for chunk in r.iter_bytes(8192):
                            f.write(chunk)
        except Exception as e:
            err(f"抖音 mp4 下载异常: {e}")

    transcript_text = ""
    if not args.no_transcribe:
        # 抽 mp3
        if not mp3_path.exists():
            try:
                subprocess.run(
                    ["ffmpeg", "-y", "-i", str(mp4_path), "-vn",
                     "-acodec", "libmp3lame", "-q:a", "5", str(mp3_path)],
                    check=True, capture_output=True, timeout=TIMEOUT_FFMPEG,
                )
            except Exception as e:
                err(f"ffmpeg 抽音频失败: {e}")
        # whisper
        if not txt_path.exists():
            try:
                from faster_whisper import WhisperModel
                m = WhisperModel(args.model, device="cpu", compute_type="int8",
                                 download_root=WHISPER_CACHE)
                segs, _ = m.transcribe(
                    str(mp3_path), language=args.lang, beam_size=1,
                    initial_prompt="以下是普通话简体中文的转录内容。",
                )
                transcript_text = "".join(s.text for s in segs).strip()
                txt_path.write_text(transcript_text, encoding="utf-8")
            except Exception as e:
                err(f"whisper 转录失败: {e}")
        else:
            transcript_text = txt_path.read_text(encoding="utf-8")

    # 拼 pipeline payload
    run_id = pipeline_io.new_run_id()
    payload = {
        "ok": True,
        "platform": "douyin",
        "url": url,
        "title": info["title"],
        "uploader": info["author"],
        "tags_seed": info.get("tags", []),
        "publish_time": info.get("create_time"),
        "stats": info.get("stats", {}),
        "content": transcript_text or info.get("description", ""),
        "content_chars": len(transcript_text or info.get("description", "")),
        "media": {
            "video_id": aweme_id,
            "duration_s": info["duration_s"],
            "video_url": info["video_url"],
            "cover_url": info.get("cover_url"),
            "mp4_path": str(mp4_path),
            "audio_path": str(mp3_path) if mp3_path.exists() else None,
            "transcript_path": str(txt_path) if txt_path.exists() else None,
        },
    }

    if args.raw:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        pipeline_io.emit_output("fetch", run_id, payload, step_num=1)


def emit_pipeline_output(info: dict, args, url: str, transcript_text: str) -> None:
    """把 fetch_video 输出转成 pipeline 标准格式。"""
    run_id = pipeline_io.new_run_id()
    payload = {
        "ok": True,
        "platform": info.get("platform", "?"),
        "url": url,
        "title": info.get("title", ""),
        "uploader": info.get("uploader", ""),
        "tags_seed": [],
        "publish_time": None,
        "stats": {},
        "content": transcript_text or info.get("description", ""),
        "content_chars": len(transcript_text or info.get("description", "")),
        "media": {
            "video_id": info.get("video_id"),
            "duration_s": info.get("duration_s", 0),
            "audio_path": info.get("audio_path"),
            "transcript_path": info.get("transcript_path"),
        },
    }
    pipeline_io.emit_output("fetch", run_id, payload, step_num=1)


if __name__ == "__main__":
    main()
