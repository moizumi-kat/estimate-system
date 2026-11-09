#!/bin/bash
# EC2(t3.micro・メモリ1GB)のメモリ不足(OOM)対策として swap 2GB を追加するスクリプト。
# 重い多ページ図面の抽出(3倍解像度×二重Vision)でメモリが逼迫し、抽出ジョブが
# 中断する(「抽出ジョブが見つかりません」)問題の根本対策。
#
# 使い方(EC2上で一度だけ・sudo権限が必要):
#   bash ~/estimate_code_system_v1.4/setup_swap.sh
#
# ・冪等: 既に swap が有効なら何もしない(重複作成しない)。
# ・永続化: /etc/fstab に登録し、再起動後も自動で有効になる。
# ・精度・動作は一切変えない(OSのメモリ余力を増やすだけ)。
set -e

SWAPFILE=/swapfile
SWAPSIZE_MB=2048   # 2GB

echo "=== swap 追加スクリプト (${SWAPSIZE_MB}MB) ==="

# 0) 既に swap が有効なら終了(冪等)
CUR_SWAP_KB=$(awk '/SwapTotal/{print $2}' /proc/meminfo 2>/dev/null || echo 0)
if [ "${CUR_SWAP_KB:-0}" -gt 0 ]; then
  echo "既に swap が有効です(SwapTotal=${CUR_SWAP_KB} kB)。追加作成は行いません。"
  swapon --show || true
  free -h || true
  exit 0
fi

# 1) 空きディスク容量を確認(swapfile 置き場 = ルート想定)。必要量+余裕200MBを確認。
AVAIL_MB=$(df -Pm / | awk 'NR==2{print $4}')
NEED_MB=$((SWAPSIZE_MB + 200))
if [ "${AVAIL_MB:-0}" -lt "$NEED_MB" ]; then
  echo "エラー: 空きディスクが不足しています(空き ${AVAIL_MB}MB < 必要 ${NEED_MB}MB)。"
  echo "不要ファイルを削除するか、より小さい SWAPSIZE_MB で再実行してください。"
  exit 1
fi

# 2) swapfile 作成(fallocate が使えなければ dd にフォールバック)
if [ -e "$SWAPFILE" ]; then
  echo "注意: ${SWAPFILE} が既に存在します。無効化して作り直します。"
  sudo swapoff "$SWAPFILE" 2>/dev/null || true
  sudo rm -f "$SWAPFILE"
fi

echo "swapfile を作成中(${SWAPSIZE_MB}MB)..."
if ! sudo fallocate -l "${SWAPSIZE_MB}M" "$SWAPFILE" 2>/dev/null; then
  echo "fallocate 不可 -> dd で作成(少し時間がかかります)..."
  sudo dd if=/dev/zero of="$SWAPFILE" bs=1M count="$SWAPSIZE_MB" status=progress
fi

# 3) 権限を厳格化(root のみ・必須)
sudo chmod 600 "$SWAPFILE"

# 4) swap 領域として初期化し有効化
sudo mkswap "$SWAPFILE"
sudo swapon "$SWAPFILE"

# 5) /etc/fstab に登録(再起動後も有効。重複登録はしない)
if ! grep -qE "^\s*${SWAPFILE}\s" /etc/fstab; then
  echo "${SWAPFILE} none swap sw 0 0" | sudo tee -a /etc/fstab >/dev/null
  echo "/etc/fstab に登録しました(再起動後も自動で有効)。"
else
  echo "/etc/fstab には既に登録済みです。"
fi

# 6) スワップ傾向を控えめに(通常はRAMを優先し、逼迫時のみswap。OOM防止が目的)
#    永続化のため sysctl.d にも記載。
sudo sysctl -w vm.swappiness=10 >/dev/null || true
if [ ! -f /etc/sysctl.d/99-swappiness.conf ]; then
  echo "vm.swappiness=10" | sudo tee /etc/sysctl.d/99-swappiness.conf >/dev/null || true
fi

echo ""
echo "=== 完了 ==="
swapon --show || true
echo ""
free -h || true
echo ""
echo "OK: swap ${SWAPSIZE_MB}MB を有効化しました。再起動後も自動で有効です。"
echo "    これで重い図面の抽出中のメモリ不足(OOM)が起きにくくなります。"
