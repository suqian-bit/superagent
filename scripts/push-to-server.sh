#!/usr/bin/env bash
# =================================================================
# 把本地 source/ 推回服务器（适合 Mac 改完代码后部署）
#
# 用法：
#   bash scripts/push-to-server.sh                   # dry-run（只看会改啥）
#   bash scripts/push-to-server.sh --apply           # 真推
#   bash scripts/push-to-server.sh --apply --restart # 推完顺便重启 gateway
#
# 前置：
#   - ~/.ssh/id_ed25519_codex_server 私钥能 ssh 到 154.9.232.37
#   - 本地有完整的 source/ 目录
# =================================================================
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

SSH_KEY="${SSH_KEY:-$HOME/.ssh/id_ed25519_codex_server}"
SERVER="${SERVER:-root@154.9.232.37}"
APPLY=0
RESTART=0

while [[ $# -gt 0 ]]; do
  case $1 in
    --apply) APPLY=1; shift ;;
    --restart) RESTART=1; shift ;;
    --help|-h)
      sed -n '/^# 用法/,/^# ==/p' "$0" | sed 's/^# \?//'
      exit 0 ;;
    *) shift ;;
  esac
done

log() { echo -e "\033[1;34m[push]\033[0m $*"; }
warn() { echo -e "\033[1;33m[warn]\033[0m $*"; }

RSYNC_FLAGS="-az --no-perms --no-times --exclude=__pycache__ --exclude=*.pyc"
[ $APPLY -eq 0 ] && RSYNC_FLAGS="$RSYNC_FLAGS --dry-run -v"

# ─── Step 1: 推 skills ───
log "Step 1: 推 skills/"
rsync $RSYNC_FLAGS -e "ssh -i $SSH_KEY" --delete \
  "$REPO_ROOT/source/skills/" \
  "$SERVER:/root/.hermes/profiles/knowledge/skills/"

# ─── Step 2: 推 SOUL.md ───
log "Step 2: 推 SOUL.md"
rsync $RSYNC_FLAGS -e "ssh -i $SSH_KEY" \
  "$REPO_ROOT/source/SOUL.md" \
  "$SERVER:/root/.hermes/profiles/knowledge/SOUL.md"

# ─── Step 3: 推 experiments ───
log "Step 3: 推 experiments/"
rsync $RSYNC_FLAGS -e "ssh -i $SSH_KEY" \
  "$REPO_ROOT/source/experiments/" \
  "$SERVER:/www/content_fetcher/experiments/"

# ─── Step 4: 重启 gateway（如果 --restart）───
if [ $APPLY -eq 1 ] && [ $RESTART -eq 1 ]; then
  log "Step 4: 重启 hermes-gateway-knowledge"
  ssh -i "$SSH_KEY" "$SERVER" 'systemctl --user restart hermes-gateway-knowledge && sleep 3 && systemctl --user is-active hermes-gateway-knowledge'
fi

if [ $APPLY -eq 0 ]; then
  echo ""
  warn "Dry-run 模式，没有真正推送"
  echo "用 --apply 真推；加 --restart 顺便重启 gateway"
fi

log "✅ 完成"
