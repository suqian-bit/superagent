#!/usr/bin/env bash
# =================================================================
# 知识助手一键部署脚本
#
# 适用：Ubuntu 22.04+ / Debian 12+，已经装好 Hermes Agent 框架
# 前置：必须先按 https://github.com/NousResearch/hermes-agent 装好 hermes
#       并创建好 knowledge profile（`hermes profile create knowledge`）
#
# 使用：
#   sudo bash install.sh
# =================================================================
set -euo pipefail

# ─── 可调参数 ───
HERMES_PROFILE="${HERMES_PROFILE:-knowledge}"
DATA_DISK="${DATA_DISK:-/www}"            # 数据盘根（推荐大盘符）
KB_ROOT="$DATA_DISK/knowledge"
FETCHER_ROOT="$DATA_DISK/content_fetcher"
VENV_DIR="$FETCHER_ROOT/_venv"            # 独立 venv（避免污染 hermes venv）
WHISPER_CACHE="$DATA_DISK/whisper_models"
EMBED_CACHE="$DATA_DISK/embed_models"

# 项目源码目录（git clone 后的根，含 SOUL.md / skills/ / experiments/）
PROJECT_ROOT="${PROJECT_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"

HERMES_HOME="$HOME/.hermes/profiles/$HERMES_PROFILE"

log() { echo -e "\033[1;34m[install]\033[0m $*"; }
warn() { echo -e "\033[1;33m[warn]\033[0m $*"; }
die() { echo -e "\033[1;31m[error]\033[0m $*" >&2; exit 1; }

# ─── Step 1: 前置检查 ───
log "Step 1: 前置检查"

[ -d "$HERMES_HOME" ] || die "Hermes profile 不存在：$HERMES_HOME（请先 hermes profile create $HERMES_PROFILE）"
[ -f "$PROJECT_ROOT/SOUL.md" ] || die "PROJECT_ROOT 似乎不对，找不到 $PROJECT_ROOT/SOUL.md"

command -v ffmpeg >/dev/null 2>&1 || {
  warn "ffmpeg 未装，开始 apt 安装"
  DEBIAN_FRONTEND=noninteractive apt-get install -y ffmpeg
}
command -v sqlite3 >/dev/null 2>&1 || apt-get install -y sqlite3
command -v git >/dev/null 2>&1 || apt-get install -y git

# 中文字体（OCR 测试图生成用，真实截图不影响）
fc-list 2>/dev/null | grep -q "wqy" || {
  warn "中文字体（wqy）未装，apt 安装"
  DEBIAN_FRONTEND=noninteractive apt-get install -y fonts-wqy-microhei
}

# Python 3.12 venv 包
dpkg -l | grep -q python3.12-venv || apt-get install -y python3.12-venv python3-pip

# ─── Step 2: 建数据目录 ───
log "Step 2: 建数据目录"
mkdir -p "$KB_ROOT"/{items,essence,categories,attachments,projects}
mkdir -p "$FETCHER_ROOT"/{downloads,transcripts,cookies,xhs,experiments}
mkdir -p "$WHISPER_CACHE" "$EMBED_CACHE"
mkdir -p /tmp/ingest /tmp/ocr_stash

# ─── Step 3: 建独立 venv ───
log "Step 3: 建 venv ($VENV_DIR)"
if [ ! -f "$VENV_DIR/bin/python" ]; then
  python3 -m venv "$VENV_DIR"
fi
"$VENV_DIR/bin/pip" install --quiet --upgrade pip

# ─── Step 4: 装 Python 依赖 ───
log "Step 4: 装 Python 依赖（约 5-10 分钟，含 rapidocr 和 chromadb）"
"$VENV_DIR/bin/pip" install --quiet -r "$PROJECT_ROOT/deploy/requirements.txt"

# ─── Step 5: clone XHS-Downloader（不在 PyPI）───
log "Step 5: clone XHS-Downloader"
if [ ! -d "$DATA_DISK/XHS-Downloader" ]; then
  git clone --depth=1 https://github.com/JoeanAmier/XHS-Downloader.git "$DATA_DISK/XHS-Downloader"
  "$VENV_DIR/bin/pip" install --quiet -r "$DATA_DISK/XHS-Downloader/requirements.txt"
else
  log "  XHS-Downloader 已存在，跳过"
fi

# ─── Step 6: 部署 skills 到 hermes profile ───
log "Step 6: 部署 skills + SOUL.md"
cp "$PROJECT_ROOT/SOUL.md" "$HERMES_HOME/SOUL.md"
# skills/ 整个拷过去（_lib / content-fetcher / score / classify / essence / save / knowledge-digest / project）
cp -r "$PROJECT_ROOT/skills/"* "$HERMES_HOME/skills/"
# experiments/ 放到数据盘
cp "$PROJECT_ROOT/experiments/"*.py "$FETCHER_ROOT/experiments/"

# ─── Step 7: 建命令软链到 /usr/local/bin ───
log "Step 7: 建命令软链"
declare -A WRAPPERS=(
  [xhs]="$HERMES_HOME/skills/content-fetcher/scripts/fetch_xhs.py"
  [video]="$HERMES_HOME/skills/content-fetcher/scripts/fetch_video.py"
  [text]="$HERMES_HOME/skills/content-fetcher/scripts/text.py"
  [score]="$HERMES_HOME/skills/score/score.py"
  [classify]="$HERMES_HOME/skills/classify/classify.py"
  [essence]="$HERMES_HOME/skills/essence/essence.py"
  [save]="$HERMES_HOME/skills/save/save.py"
  [digest]="$HERMES_HOME/skills/knowledge-digest/digest.py"
  [project]="$HERMES_HOME/skills/project/project.py"
  [review-push]="$HERMES_HOME/skills/_lib/review_pusher.py"
  [weekly-report]="$HERMES_HOME/skills/_lib/weekly_report.py"
  [detect-share]="$FETCHER_ROOT/experiments/detect_share_token.py"
  [ocr]="$FETCHER_ROOT/experiments/ocr.py"
  [ocr-stash]="$FETCHER_ROOT/experiments/ocr_stash.py"
  [fetch-feishu-image]="$FETCHER_ROOT/experiments/fetch_feishu_image.py"
)
for name in "${!WRAPPERS[@]}"; do
  cat > "/usr/local/bin/$name" << WRAP
#!/bin/bash
exec $VENV_DIR/bin/python ${WRAPPERS[$name]} "\$@"
WRAP
  chmod +x "/usr/local/bin/$name"
done
log "  建好 ${#WRAPPERS[@]} 个命令"

# ─── Step 8: .env 配置提醒 ───
log "Step 8: 配置 .env"
if [ ! -f "$HERMES_HOME/.env" ]; then
  cp "$PROJECT_ROOT/deploy/.env.example" "$HERMES_HOME/.env"
  chmod 600 "$HERMES_HOME/.env"
  warn "  已生成模板 $HERMES_HOME/.env，请填入真值后重启 service"
else
  log "  $HERMES_HOME/.env 已存在，跳过"
fi

# ─── Step 9: systemd ───
log "Step 9: systemd unit"
mkdir -p "$HOME/.config/systemd/user"
UNIT_NAME="hermes-gateway-$HERMES_PROFILE.service"
TARGET_UNIT="$HOME/.config/systemd/user/$UNIT_NAME"
if [ ! -f "$TARGET_UNIT" ]; then
  sed "s|knowledge|$HERMES_PROFILE|g; s|/root/.hermes/profiles/knowledge|$HERMES_HOME|g" \
    "$PROJECT_ROOT/deploy/hermes-gateway-knowledge.service" > "$TARGET_UNIT"
  systemctl --user daemon-reload
  systemctl --user enable "$UNIT_NAME"
  log "  $UNIT_NAME 已启用，记得填完 .env 再 systemctl --user start $UNIT_NAME"
else
  log "  $UNIT_NAME 已存在"
fi
# enable user linger 让 service 在 logout 后继续跑
loginctl enable-linger "$USER" 2>/dev/null || true

# ─── Step 10: crontab ───
log "Step 10: crontab"
TMP_CRON=$(mktemp)
crontab -l 2>/dev/null > "$TMP_CRON" || true
NEEDS_UPDATE=0
for cmd in review-push weekly-report ocr-stash; do
  grep -q "/usr/local/bin/$cmd" "$TMP_CRON" || NEEDS_UPDATE=1
done
if [ $NEEDS_UPDATE -eq 1 ]; then
  echo "" >> "$TMP_CRON"
  echo "# === 知识助手自动任务 ===" >> "$TMP_CRON"
  cat "$PROJECT_ROOT/deploy/crontab.example" | grep -v "^#" | grep -v "^$" >> "$TMP_CRON"
  crontab "$TMP_CRON"
  log "  已加入 cron 任务（复习推送 + 周报 + OCR stash expire）"
else
  log "  cron 任务已存在，跳过"
fi
rm -f "$TMP_CRON"

# ─── Done ───
echo ""
log "✅ 部署完成"
echo ""
echo "📋 接下来你要做的："
echo ""
echo "  1. 编辑 $HERMES_HOME/.env 填入 DeepSeek key + 飞书 App ID/Secret + 你的 open_id"
echo ""
echo "  2. 启动 service:"
echo "     systemctl --user start $UNIT_NAME"
echo "     journalctl --user -u $UNIT_NAME -f          # 看日志"
echo ""
echo "  3. 第一次跑会下载 ~150MB whisper 模型 + 400MB bge embedding 模型"
echo ""
echo "  4. 在飞书加 Bot，发消息「你好」测试连通"
echo ""
echo "  5. 验证命令："
echo "     digest stats"
echo "     review-push    # 手动触发一次复习推送"
echo ""
echo "  6. 看文档：docs/00-架构总览.md 起步"
