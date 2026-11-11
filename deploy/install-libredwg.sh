#!/usr/bin/env bash
# EC2 に DWG→DXF 変換器 dwg2dxf(LibreDWG) を導入する。
# 端子番号割付(2人目)アプリが DWG を受け付けるために必要。図面は社内資産のため
# クラウド変換は使わず、このローカル変換器のみを使う。
# 使い方(sudo可ユーザで): bash deploy/install-libredwg.sh
#   導入後、端子アプリ再起動: sudo systemctl restart terminal-system
#
# 導入の優先順:
#   0) 既に dwg2dxf があれば何もしない
#   1) 同梱の静的バイナリ(deploy/bin/dwg2dxf-<arch>-static.gz)を展開して設置 ← 既定・ビルド不要
#   2) パッケージ(dnf/yum install libredwg)
#   3) ソースからビルド(最後の手段。小さいEC2ではメモリ/ディスク不足で失敗しうる)
set -euo pipefail

DEST=/usr/local/bin/dwg2dxf
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "== LibreDWG(dwg2dxf) 導入 =="

if command -v dwg2dxf >/dev/null 2>&1; then
  echo "既に導入済み: $(command -v dwg2dxf)"
  dwg2dxf --version 2>&1 | head -1 || true
  exit 0
fi

# --- 1) 同梱の静的バイナリ(完全静的=glibc非依存。ビルド不要で最速・最確実) ---
ARCH="$(uname -m)"
GZ="$SCRIPT_DIR/bin/dwg2dxf-${ARCH}-static.gz"
if [ -f "$GZ" ]; then
  echo "-- 同梱の静的バイナリを設置: $(basename "$GZ")"
  TMP="$(mktemp)"
  if gunzip -c "$GZ" > "$TMP" 2>/dev/null && [ -s "$TMP" ]; then
    sudo install -m 0755 "$TMP" "$DEST"
    rm -f "$TMP"
    if "$DEST" --version >/dev/null 2>&1; then
      echo "導入成功: $DEST"
      "$DEST" --version 2>&1 | head -1 || true
      echo "次: sudo systemctl restart terminal-system"
      exit 0
    fi
    echo "   同梱バイナリが動作せず(アーキ不一致?) → 次の方法へ"
    sudo rm -f "$DEST"
  else
    rm -f "$TMP"
    echo "   展開失敗 → 次の方法へ"
  fi
else
  echo "-- 同梱バイナリ無し(arch=$ARCH) → パッケージ/ビルドへ"
fi

PKG=""
command -v dnf >/dev/null 2>&1 && PKG=dnf
[ -z "$PKG" ] && command -v yum >/dev/null 2>&1 && PKG=yum

# --- 2) パッケージ ---
if [ -n "$PKG" ]; then
  echo "-- $PKG でパッケージ導入を試行"
  if sudo "$PKG" install -y libredwg >/dev/null 2>&1 && command -v dwg2dxf >/dev/null 2>&1; then
    echo "パッケージ導入成功: $(command -v dwg2dxf)"
    echo "次: sudo systemctl restart terminal-system"; exit 0
  fi
  echo "   パッケージが無い/失敗 → ソースビルドへ"
fi

# --- 3) ソースからビルド(最後の手段。省メモリ: 逐次make・デバッグ情報なし) ---
VER="0.13.3"
TARBALL="libredwg-${VER}.tar.xz"
URL="https://ftp.gnu.org/gnu/libredwg/${TARBALL}"
WORK="$(mktemp -d)"
echo "-- ビルド依存を導入"
[ -n "$PKG" ] && { sudo "$PKG" install -y gcc make xz tar >/dev/null 2>&1 || true; }
echo "-- ソース取得: $URL"
cd "$WORK"
if command -v curl >/dev/null 2>&1; then curl -fsSL -o "$TARBALL" "$URL"; else wget -q -O "$TARBALL" "$URL"; fi
tar xf "$TARBALL"; cd "libredwg-${VER}"
echo "-- configure(バインディング無効=軽量)"
./configure --disable-bindings --disable-shared --enable-static CFLAGS="-O1 -g0" >/dev/null
echo "-- make(逐次・省メモリ。数分〜十数分)"
make >/dev/null        # 小さいEC2のOOM回避のため -j は付けない
echo "-- インストール"; sudo make install >/dev/null; sudo ldconfig 2>/dev/null || true

if command -v dwg2dxf >/dev/null 2>&1; then
  echo "導入成功: $(command -v dwg2dxf)"
  dwg2dxf --version 2>&1 | head -1 || true
  echo "次: sudo systemctl restart terminal-system"
else
  echo "!! 導入に失敗しました。cat /etc/os-release とエラーを確認してください。" >&2
  exit 1
fi
