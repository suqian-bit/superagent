#!/usr/bin/env python3
"""
ocr-stash: 多张截图的暂存批次 → 一次性入库。

场景：飞书一次只能发一张图，主人连发 5 张抖音图文截图时，
agent 每收一张就 add 到 stash，最后调 commit 合并入库。

子命令:
  ocr-stash add <image_path> [--source-hint <txt>]
      把图加入当前 stash + 立刻 OCR。返回累积状态 + 建议下一步。

  ocr-stash status
      看当前 stash（图片数、总字数、最早 add 时间、剩余 expire 时间）。

  ocr-stash preview [--limit-chars N]
      看当前累积文本（默认前 800 字）。

  ocr-stash commit --title "..." [--source douyin]
      把整个 stash 合成长文 → 调 text 命令走 ingest pipeline → 清空 stash。

  ocr-stash clear
      主人说「不存」 → 直接清空。

  ocr-stash expire-check
      cron 用：超过 EXPIRE_MIN 没新增就自动 commit 或清空（按数量判断）。

设计：单用户 + 全局一个 stash，目录 /tmp/ocr_stash/
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, "/root/.hermes/profiles/knowledge/skills/_lib")
import pipeline_io

STASH_DIR = Path("/tmp/ocr_stash")
EXPIRE_MINUTES = 5
META_FILE = STASH_DIR / "meta.json"
OCR_CMD = "/usr/local/bin/ocr"
TEXT_CMD = "/usr/local/bin/text"


def _now_ts() -> int:
    return int(time.time())


def _load_meta() -> dict:
    if not META_FILE.exists():
        return {
            "created_at": None,
            "last_add_at": None,
            "image_count": 0,
            "total_chars": 0,
            "source_hints": [],
            "images": [],   # [{idx, image_path, text, chars, conf, added_at}]
        }
    return json.loads(META_FILE.read_text(encoding="utf-8"))


def _save_meta(meta: dict):
    STASH_DIR.mkdir(parents=True, exist_ok=True)
    META_FILE.write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                          encoding="utf-8")


def _run_ocr(image_path: str) -> dict:
    """调 ocr 命令，返回 {text, lines, avg_confidence}"""
    r = subprocess.run([OCR_CMD, image_path],
                       capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise RuntimeError(f"OCR 失败: {r.stderr[:300]}")
    return json.loads(r.stdout)


# ──────────────────────────────────────────────────────────────
# add: 加图
# ──────────────────────────────────────────────────────────────

def cmd_add(args):
    src = Path(args.image_path)
    if not src.exists():
        print(json.dumps({"ok": False, "error": f"图不存在: {src}"},
                         ensure_ascii=False))
        sys.exit(1)

    STASH_DIR.mkdir(parents=True, exist_ok=True)
    meta = _load_meta()
    now = _now_ts()

    # 拷贝图到 stash（保留原名 + idx 防撞）
    idx = meta["image_count"] + 1
    dst = STASH_DIR / f"{idx:03d}_{src.name}"
    shutil.copy2(src, dst)

    # OCR
    try:
        ocr_result = _run_ocr(str(dst))
    except Exception as e:
        print(json.dumps({"ok": False, "error": f"OCR 失败: {e}"},
                         ensure_ascii=False))
        sys.exit(1)

    text = ocr_result.get("text", "")
    conf = ocr_result.get("avg_confidence", 0)

    # 更新 meta
    if meta["created_at"] is None:
        meta["created_at"] = now
    meta["last_add_at"] = now
    meta["image_count"] = idx
    meta["total_chars"] += len(text)
    if args.source_hint and args.source_hint not in meta["source_hints"]:
        meta["source_hints"].append(args.source_hint)
    meta["images"].append({
        "idx": idx,
        "image_path": str(dst),
        "text": text,
        "chars": len(text),
        "conf": conf,
        "added_at": now,
    })
    _save_meta(meta)

    # 构建给 agent 的"建议下一步"提示
    chars = len(text)
    if chars < 30:
        intent_hint = (
            f"这张图识别字数很少（{chars} 字，置信度 {conf}）。"
            f"问主人：「这张图想做什么？看上去文字不多」"
        )
    elif chars < 300:
        intent_hint = (
            f"识别 {chars} 字（置信度 {conf}）。"
            f"问主人：「识别到 {chars} 字，要存为笔记还是想问点什么？"
            f"还有更多截图要发吗？」"
        )
    else:
        intent_hint = (
            f"识别 {chars} 字（置信度 {conf}），看着像文章正文。"
            f"问主人：「识别到 {chars} 字，要存到知识库吗？"
            f"还有更多截图要发吗？回「存」入库，「再发一张」继续加，「不存」放弃」"
        )

    print(json.dumps({
        "ok": True,
        "action": "added",
        "image_idx": idx,
        "this_image_chars": chars,
        "this_image_conf": conf,
        "this_image_preview": text[:200],
        "total_images_in_stash": meta["image_count"],
        "total_chars_in_stash": meta["total_chars"],
        "expire_at_iso": datetime.fromtimestamp(now + EXPIRE_MINUTES * 60).isoformat(),
        "intent_hint_for_agent": intent_hint,
    }, ensure_ascii=False))


# ──────────────────────────────────────────────────────────────
# status
# ──────────────────────────────────────────────────────────────

def cmd_status(args):
    meta = _load_meta()
    if meta["image_count"] == 0:
        print(json.dumps({"ok": True, "active": False,
                          "message": "当前 stash 为空"}, ensure_ascii=False))
        return

    now = _now_ts()
    last = meta["last_add_at"] or 0
    minutes_idle = (now - last) / 60
    minutes_left = max(0, EXPIRE_MINUTES - minutes_idle)

    print(json.dumps({
        "ok": True,
        "active": True,
        "image_count": meta["image_count"],
        "total_chars": meta["total_chars"],
        "source_hints": meta["source_hints"],
        "created_at": datetime.fromtimestamp(meta["created_at"]).isoformat() if meta["created_at"] else None,
        "last_add_at": datetime.fromtimestamp(last).isoformat() if last else None,
        "minutes_idle": round(minutes_idle, 1),
        "minutes_left_before_auto_expire": round(minutes_left, 1),
    }, ensure_ascii=False))


# ──────────────────────────────────────────────────────────────
# preview
# ──────────────────────────────────────────────────────────────

def cmd_preview(args):
    meta = _load_meta()
    if meta["image_count"] == 0:
        print(json.dumps({"ok": True, "text": "", "message": "stash 为空"},
                         ensure_ascii=False))
        return

    parts = [img["text"] for img in meta["images"] if img.get("text")]
    full = "\n\n---\n\n".join(parts)
    preview = full[:args.limit_chars]
    truncated = len(full) > args.limit_chars

    print(json.dumps({
        "ok": True,
        "image_count": meta["image_count"],
        "total_chars": meta["total_chars"],
        "preview": preview,
        "truncated": truncated,
    }, ensure_ascii=False))


# ──────────────────────────────────────────────────────────────
# commit: 合并入库
# ──────────────────────────────────────────────────────────────

def cmd_commit(args):
    meta = _load_meta()
    if meta["image_count"] == 0:
        print(json.dumps({"ok": False, "error": "stash 为空，没东西可入库"},
                         ensure_ascii=False))
        sys.exit(1)

    # 合并文本
    parts = [img["text"] for img in meta["images"] if img.get("text")]
    full_text = "\n\n".join(parts)

    if len(full_text) < 10:
        print(json.dumps({"ok": False,
                          "error": f"合并后只有 {len(full_text)} 字，太短不入库"},
                         ensure_ascii=False))
        sys.exit(1)

    # 调 text 命令走 pipeline
    cmd = [TEXT_CMD,
           "--title", args.title,
           "--source", args.source,
           "--from-stdin"]
    if args.uploader:
        cmd += ["--uploader", args.uploader]
    if args.url:
        cmd += ["--url", args.url]

    proc = subprocess.run(cmd, input=full_text, capture_output=True,
                          text=True, timeout=60)
    if proc.returncode != 0:
        print(json.dumps({"ok": False,
                          "error": f"text 命令失败: {proc.stderr[:300]}"},
                         ensure_ascii=False))
        sys.exit(1)

    # 解析 text 输出
    try:
        text_result = json.loads(proc.stdout.strip().split("\n")[-1])
    except Exception:
        text_result = {"raw": proc.stdout[:500]}

    # 清空 stash
    if STASH_DIR.exists():
        shutil.rmtree(STASH_DIR)

    print(json.dumps({
        "ok": True,
        "committed_images": meta["image_count"],
        "committed_chars": len(full_text),
        "next_action": text_result.get("_next_action"),
        "step_file": text_result.get("step_file"),
        "summary_for_agent": (
            f"✅ 已把 {meta['image_count']} 张截图合并成 {len(full_text)} 字的文章，"
            f"开始走 ingest pipeline。下一步：{text_result.get('_next_action', {}).get('command', 'score')}"
        ),
    }, ensure_ascii=False))


# ──────────────────────────────────────────────────────────────
# clear
# ──────────────────────────────────────────────────────────────

def cmd_clear(args):
    if not STASH_DIR.exists():
        print(json.dumps({"ok": True, "message": "stash 本来就是空的"},
                         ensure_ascii=False))
        return
    meta = _load_meta()
    image_count = meta.get("image_count", 0)
    shutil.rmtree(STASH_DIR)
    print(json.dumps({"ok": True, "cleared_images": image_count,
                      "summary_for_agent": f"🗑 已清空 stash（{image_count} 张图）"},
                     ensure_ascii=False))


# ──────────────────────────────────────────────────────────────
# expire-check (cron)
# ──────────────────────────────────────────────────────────────

def cmd_expire_check(args):
    meta = _load_meta()
    if meta["image_count"] == 0:
        return
    now = _now_ts()
    last = meta["last_add_at"] or 0
    minutes_idle = (now - last) / 60
    if minutes_idle < EXPIRE_MINUTES:
        return  # 还没到期

    # 到期了，决定 auto-commit 还是直接清
    # 简单策略：如果累积 >= 100 字，auto-commit；否则清空
    if meta["total_chars"] >= 100:
        # auto-commit 用默认标题
        guess_title = "未命名笔记 - " + datetime.fromtimestamp(meta["created_at"] or now).strftime("%Y%m%d_%H%M")
        source = meta["source_hints"][0] if meta["source_hints"] else "text"
        # 模拟 args
        class Args:
            pass
        a = Args()
        a.title = guess_title
        a.source = source
        a.uploader = ""
        a.url = ""
        cmd_commit(a)
        # 也通过飞书推送一条通知
        try:
            sys.path.insert(0, "/root/.hermes/profiles/knowledge/skills/_lib")
            import review_pusher
            review_pusher.send_to_feishu(
                f"⏰ Stash 超过 {EXPIRE_MINUTES} 分钟没动作，已自动把 {meta['image_count']} 张截图入库（标题：{guess_title}）。"
                f"\n要改标题或归档：digest view <id> / digest demote <id> --archive"
            )
        except Exception:
            pass
    else:
        # 字数太少，直接清
        shutil.rmtree(STASH_DIR)
        try:
            sys.path.insert(0, "/root/.hermes/profiles/knowledge/skills/_lib")
            import review_pusher
            review_pusher.send_to_feishu(
                f"⏰ Stash 超时（{meta['image_count']} 张图 / {meta['total_chars']} 字），"
                f"字数太少自动清空。"
            )
        except Exception:
            pass


# ──────────────────────────────────────────────────────────────
# main
# ──────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd")

    p_add = sp.add_parser("add")
    p_add.add_argument("image_path")
    p_add.add_argument("--source-hint", default="")

    sp.add_parser("status")

    p_pre = sp.add_parser("preview")
    p_pre.add_argument("--limit-chars", type=int, default=800)

    p_com = sp.add_parser("commit")
    p_com.add_argument("--title", required=True)
    p_com.add_argument("--source", default="text")
    p_com.add_argument("--uploader", default="")
    p_com.add_argument("--url", default="")

    sp.add_parser("clear")
    sp.add_parser("expire-check")

    args = ap.parse_args()
    if not args.cmd:
        ap.print_help()
        sys.exit(1)

    {
        "add": cmd_add,
        "status": cmd_status,
        "preview": cmd_preview,
        "commit": cmd_commit,
        "clear": cmd_clear,
        "expire-check": cmd_expire_check,
    }[args.cmd](args)


if __name__ == "__main__":
    main()
