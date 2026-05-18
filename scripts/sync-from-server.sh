#!/usr/bin/env bash
# =================================================================
# 从服务器同步源码到本地 source/，再 commit + push 到 GitHub
#
# 用法：
#   bash scripts/sync-from-server.sh                # 同步 + diff（不 commit）
#   bash scripts/sync-from-server.sh --commit       # 同步 + commit + push
#   bash scripts/sync-from-server.sh --message "改了 essence prompt"
#
# 前置：
#   - ~/.ssh/id_ed25519_codex_server 私钥能 ssh 到 154.9.232.37
#   - 本地 cwd 是 git 仓库根
# =================================================================
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

SSH_KEY="${SSH_KEY:-$HOME/.ssh/id_ed25519_codex_server}"
SERVER="${SERVER:-root@154.9.232.37}"
COMMIT_MSG="${1:-}"
DO_COMMIT=0

# 解析参数
while [[ $# -gt 0 ]]; do
  case $1 in
    --commit) DO_COMMIT=1; shift ;;
    --message|-m) COMMIT_MSG="$2"; DO_COMMIT=1; shift 2 ;;
    --help|-h)
      sed -n '/^# 用法/,/^# ==/p' "$0" | sed 's/^# \?//'
      exit 0 ;;
    *) shift ;;
  esac
done

log() { echo -e "\033[1;34m[sync]\033[0m $*"; }
warn() { echo -e "\033[1;33m[warn]\033[0m $*"; }

# ─── Step 1: 服务器上 tar 打包 ───
log "Step 1: 在服务器 tar 打包"
ssh -i "$SSH_KEY" -o ConnectTimeout=30 "$SERVER" 'cd / && tar czf /tmp/superagent_src.tgz \
  --exclude="__pycache__" \
  root/.hermes/profiles/knowledge/SOUL.md \
  root/.hermes/profiles/knowledge/skills/_lib \
  root/.hermes/profiles/knowledge/skills/content-fetcher \
  root/.hermes/profiles/knowledge/skills/score \
  root/.hermes/profiles/knowledge/skills/classify \
  root/.hermes/profiles/knowledge/skills/essence \
  root/.hermes/profiles/knowledge/skills/save \
  root/.hermes/profiles/knowledge/skills/knowledge-digest \
  root/.hermes/profiles/knowledge/skills/project \
  www/content_fetcher/experiments \
  root/.config/systemd/user/hermes-gateway-knowledge.service'

# ─── Step 2: scp 下载 ───
log "Step 2: scp 下载"
scp -i "$SSH_KEY" -q "$SERVER:/tmp/superagent_src.tgz" /tmp/superagent_src.tgz

# ─── Step 3: 解压到临时目录 ───
log "Step 3: 解压"
EXTRACT_DIR=$(mktemp -d)
tar xzf /tmp/superagent_src.tgz -C "$EXTRACT_DIR"

# ─── Step 4: 同步到 source/ ───
log "Step 4: 同步到 source/"
rsync -a --delete "$EXTRACT_DIR/root/.hermes/profiles/knowledge/skills/" "$REPO_ROOT/source/skills/"
cp "$EXTRACT_DIR/root/.hermes/profiles/knowledge/SOUL.md" "$REPO_ROOT/source/SOUL.md"

# experiments/ 单独处理（不删 install.sh 等本地配置）
mkdir -p "$REPO_ROOT/source/experiments"
cp -f "$EXTRACT_DIR/www/content_fetcher/experiments/"*.py "$REPO_ROOT/source/experiments/"

# systemd unit
cp "$EXTRACT_DIR/root/.config/systemd/user/hermes-gateway-knowledge.service" \
   "$REPO_ROOT/source/deploy/hermes-gateway-knowledge.service"

# 清理临时
rm -rf "$EXTRACT_DIR" /tmp/superagent_src.tgz

# ─── Step 5: 看变更 ───
log "Step 5: 变更摘要"
echo ""
git -C "$REPO_ROOT" diff --stat
echo ""

if [ "$DO_COMMIT" -eq 0 ]; then
  warn "未 commit，加 --commit 或 --message '...' 才会自动 commit + push"
  echo ""
  echo "手动操作："
  echo "  git add -A"
  echo "  git commit -m '<改了啥>'"
  echo "  git push"
  exit 0
fi

# ─── Step 6: commit + push ───
log "Step 6: commit + push"
git -C "$REPO_ROOT" add -A

if git -C "$REPO_ROOT" diff --cached --quiet; then
  warn "没有变更，跳过 commit"
  exit 0
fi

MSG="${COMMIT_MSG:-Sync from server $(date +%Y-%m-%d_%H:%M)}"
git -C "$REPO_ROOT" commit -m "$MSG"
git -C "$REPO_ROOT" push

log "✅ 完成"
