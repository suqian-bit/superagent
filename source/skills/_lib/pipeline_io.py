"""
pipeline_io: 五步 chain 的通信协议

每个 step 的工具脚本通过共用函数：
  - read_input(): 读上一步 step file 或新建运行
  - write_output(): 落盘 step file + 返回包含 _next_action 的 JSON 给 agent

运行 ID = 收录链的唯一标识，贯穿 5 步。
临时文件存 /tmp/ingest/<run_id>_step{N}.json
"""
from __future__ import annotations

import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Optional


# ---- 时区：统一用北京时间（UTC+8）----

CN_TZ = timezone(timedelta(hours=8))


def now_iso() -> str:
    """当前北京时间 ISO 格式，例：2026-05-12T14:07:52+08:00"""
    return datetime.now(CN_TZ).isoformat(timespec="seconds")


def now_dt() -> datetime:
    return datetime.now(CN_TZ)


def add_days(days: int) -> str:
    """从当前北京时间往后推 N 天，返回 ISO 字符串"""
    return (datetime.now(CN_TZ) + timedelta(days=days)).isoformat(timespec="seconds")


def today_str() -> str:
    """今天的日期，YYYY-MM-DD（北京时区）"""
    return datetime.now(CN_TZ).strftime("%Y-%m-%d")

INGEST_DIR = Path("/tmp/ingest")
INGEST_DIR.mkdir(parents=True, exist_ok=True)


# ---- 链路定义 ----

PIPELINE = ["fetch", "score", "classify", "essence", "save"]

NEXT_COMMAND = {
    "fetch":    "score --in {step_file}",
    "score":    "classify --in {step_file}",
    "classify": "essence --in {step_file}",
    "essence":  "save --in {step_file}",
    "save":     None,  # 最后一步，下一步是回复主人
}

NEXT_REASON = {
    "fetch":    "已抓到原料，下一步：score 进行五维评分",
    "score":    "评分完成，下一步：classify 智能分类+打标签",
    "classify": "分类完成，下一步：essence 提炼精华内容",
    "essence":  "精华提炼完成，下一步：save 落盘到知识库",
    "save":     None,
}


def new_run_id() -> str:
    """fetch 步骤生成新的运行 ID（北京时区时间戳）。"""
    return f"{datetime.now(CN_TZ).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"


def step_path(run_id: str, step_num: int) -> Path:
    return INGEST_DIR / f"{run_id}_step{step_num}.json"


def read_step_file(path: str) -> dict:
    """读上一步 step file，回返 dict。"""
    p = Path(path)
    if not p.exists():
        die(f"step file 不存在: {path}")
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        die(f"step file 解析失败 {path}: {e}")
    return {}  # unreachable


def make_next_action(current_step: str, run_id: str, current_step_num: int) -> Optional[dict]:
    """
    根据当前 step 名生成 _next_action 字段。
    current_step_num 是刚刚写完的那个 step 文件号——下一步要 --in 这个文件。
    """
    tmpl = NEXT_COMMAND.get(current_step)
    if tmpl is None:
        return None
    in_file = str(step_path(run_id, current_step_num))
    return {
        "command": tmpl.format(step_file=in_file),
        "reason": NEXT_REASON[current_step],
        "input_file": in_file,
    }


def emit_output(current_step: str, run_id: str, payload: dict, step_num: int) -> None:
    """
    把 payload 写到 step{N}.json，并 stdout JSON 给 agent。
    payload 不要包含 _next_action 和 step_file 字段，自动注入。

    注意：_next_action 里的 --in 路径指向**当前 step 的输出文件**
    （那就是下一步要读的输入）。
    """
    out_file = step_path(run_id, step_num)
    enriched = dict(payload)
    enriched["run_id"] = run_id
    enriched["step"] = current_step
    enriched["step_num"] = step_num

    # 落盘前先写一份不带 _next_action 的（避免下游误读）
    out_file.write_text(json.dumps(enriched, ensure_ascii=False, indent=2), encoding="utf-8")

    # stdout 输出给 agent 时附 _next_action 和 step_file
    enriched["step_file"] = str(out_file)
    # 下一步要读的就是我们刚写的这个文件
    next_action = make_next_action(current_step, run_id, step_num)
    if next_action is not None:
        enriched["_next_action"] = next_action

    print(json.dumps(enriched, ensure_ascii=False, default=str))


def emit_error(current_step: str, error_msg: str, run_id: Optional[str] = None,
               recoverable: bool = False, suggestion: str = "") -> None:
    """步骤失败时输出统一格式给 agent。"""
    out = {
        "ok": False,
        "step": current_step,
        "run_id": run_id,
        "error": error_msg,
        "recoverable": recoverable,
        "suggestion": suggestion,
    }
    print(json.dumps(out, ensure_ascii=False, default=str))
    sys.exit(1)


def die(msg: str) -> None:
    print(json.dumps({"ok": False, "error": msg}, ensure_ascii=False), file=sys.stdout)
    sys.exit(1)


# ---- DeepSeek API 调用 helper ----

def call_deepseek(prompt: str, system: str = "", temperature: float = 0.0,
                  max_tokens: int = 1500, json_mode: bool = True) -> str:
    """同步调 DeepSeek 聊天 API，复用 knowledge profile 的 key。"""
    from openai import OpenAI

    # 从 knowledge profile .env 读 key
    env_path = "/root/.hermes/profiles/knowledge/.env"
    api_key = ""
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                if line.startswith("DEEPSEEK_API_KEY="):
                    api_key = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break

    if not api_key:
        die("DEEPSEEK_API_KEY 未配置，请检查 /root/.hermes/profiles/knowledge/.env")

    client = OpenAI(api_key=api_key, base_url="https://api.deepseek.com/v1")

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    kwargs = {
        "model": "deepseek-chat",
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}

    resp = client.chat.completions.create(**kwargs)
    return resp.choices[0].message.content


def parse_json_reply(text: str) -> dict:
    """容错解析 DeepSeek 返回的 JSON。"""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        if text.startswith("json"):
            text = text[4:].strip()
    return json.loads(text)
