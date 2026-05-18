#!/usr/bin/env python3
"""
fetch-feishu-image: 从飞书消息下载图片到本地。

飞书消息里的图片只有 image_key（不是 URL），需要调飞书 API 拿二进制。

用法：
  fetch-feishu-image <message_id> <image_key> [--output /tmp/xxx.png]
  fetch-feishu-image <message_id> --all-images [--output-dir /tmp]

输出 JSON：{ok, paths: [...]}
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import httpx


def read_feishu_creds() -> tuple[str, str]:
    """从 knowledge profile .env 读飞书凭证"""
    env_path = "/root/.hermes/profiles/knowledge/.env"
    app_id = app_secret = ""
    with open(env_path) as f:
        for line in f:
            if line.startswith("FEISHU_APP_ID="):
                app_id = line.split("=", 1)[1].strip().strip('"').strip("'")
            elif line.startswith("FEISHU_APP_SECRET="):
                app_secret = line.split("=", 1)[1].strip().strip('"').strip("'")
    return app_id, app_secret


def get_tenant_token() -> str:
    app_id, app_secret = read_feishu_creds()
    if not (app_id and app_secret):
        raise RuntimeError("飞书凭证未配置")
    r = httpx.post(
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        json={"app_id": app_id, "app_secret": app_secret},
        timeout=15,
    )
    r.raise_for_status()
    data = r.json()
    if data.get("code") != 0:
        raise RuntimeError(f"token 失败: {data}")
    return data["tenant_access_token"]


def fetch_image(message_id: str, image_key: str, output: str) -> str:
    """
    GET /open-apis/im/v1/messages/:message_id/resources/:file_key?type=image
    """
    token = get_tenant_token()
    url = f"https://open.feishu.cn/open-apis/im/v1/messages/{message_id}/resources/{image_key}"
    headers = {"Authorization": f"Bearer {token}"}
    params = {"type": "image"}

    with httpx.Client(timeout=30) as c:
        r = c.get(url, headers=headers, params=params)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:300]}")

    Path(output).parent.mkdir(parents=True, exist_ok=True)
    with open(output, "wb") as f:
        f.write(r.content)
    return output


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("message_id")
    ap.add_argument("image_key", help="image_key 或 file_key")
    ap.add_argument("--output", default=None, help="输出路径（默认 /tmp/feishu_<ts>.png）")
    args = ap.parse_args()

    output = args.output or f"/tmp/feishu_{int(time.time())}_{args.image_key[:8]}.png"

    try:
        path = fetch_image(args.message_id, args.image_key, output)
    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False))
        sys.exit(1)

    size = Path(path).stat().st_size
    print(json.dumps({
        "ok": True,
        "path": path,
        "size_bytes": size,
        "size_kb": round(size / 1024, 1),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
