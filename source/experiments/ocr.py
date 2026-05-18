#!/usr/bin/env python3
"""
ocr: 图片 → 文字（RapidOCR / PaddleOCR ONNX 版本）。

适用：
- 抖音图文截图（APP 不让复制，只能截图）
- 公众号文章截图、PPT 截图
- 任何含文字的图

用法：
  ocr <image_path>                  # 输出 JSON
  ocr <image_path> --text-only      # 只输出纯文本（适合管道）
  ocr img1.png img2.png ...         # 多张图按顺序合并文本

输出 JSON：
  {
    "ok": true,
    "text": "<合并后的完整文本>",
    "lines": [{"text": "...", "conf": 0.95, "box": [...]}, ...],
    "image_count": 1,
    "avg_confidence": 0.92,
    "elapsed_ms": 1234
  }
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path


_OCR_INSTANCE = None


def get_ocr():
    """单例：RapidOCR 加载模型要 1-2 秒，复用。"""
    global _OCR_INSTANCE
    if _OCR_INSTANCE is None:
        from rapidocr_onnxruntime import RapidOCR
        _OCR_INSTANCE = RapidOCR()
    return _OCR_INSTANCE


def ocr_one(image_path: str) -> dict:
    """对单张图做 OCR，返回 {lines, text, avg_confidence}。"""
    ocr = get_ocr()
    result, elapse = ocr(image_path)

    if not result:
        return {
            "image": image_path,
            "text": "",
            "lines": [],
            "avg_confidence": 0,
        }

    lines = []
    confs = []
    for item in result:
        # rapidocr 返回 [box, text, confidence]
        box = item[0]
        text = item[1]
        conf = float(item[2])
        lines.append({"text": text, "conf": round(conf, 3)})
        confs.append(conf)

    # 按行拼接文本（rapidocr 返回顺序按从上到下）
    text = "\n".join(line["text"] for line in lines)

    return {
        "image": image_path,
        "text": text,
        "lines": lines,
        "avg_confidence": round(sum(confs) / len(confs), 3) if confs else 0,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("images", nargs="+", help="图片路径（可多张，按顺序合并）")
    ap.add_argument("--text-only", action="store_true", help="只输出纯文本（适合管道）")
    args = ap.parse_args()

    # 验证文件存在
    for img in args.images:
        if not Path(img).exists():
            print(json.dumps({"ok": False, "error": f"图片不存在: {img}"},
                             ensure_ascii=False))
            sys.exit(1)

    t0 = time.time()

    per_image = [ocr_one(img) for img in args.images]
    all_text = "\n\n---\n\n".join(p["text"] for p in per_image if p["text"]) if len(per_image) > 1 else per_image[0]["text"]
    all_lines = [line for p in per_image for line in p["lines"]]
    all_confs = [line["conf"] for line in all_lines]

    elapsed_ms = int((time.time() - t0) * 1000)

    if args.text_only:
        print(all_text)
        return

    output = {
        "ok": True,
        "text": all_text,
        "lines": all_lines,
        "image_count": len(args.images),
        "avg_confidence": round(sum(all_confs) / len(all_confs), 3) if all_confs else 0,
        "elapsed_ms": elapsed_ms,
        "per_image": per_image if len(args.images) > 1 else None,
    }
    print(json.dumps(output, ensure_ascii=False))


if __name__ == "__main__":
    main()
