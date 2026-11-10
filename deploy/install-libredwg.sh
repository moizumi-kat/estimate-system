#!/usr/bin/env bash
# EC2(Amazon Linux想定)に DWG→DXF 変換器 dwg2dxf(LibreDWG) を導入する。
# 端子番号割付(2人目)アプリが DWG を受け付けるために必要。図面は社内資産のため
# クラウド変換は使わず、このローカル変換器のみを使う。
# 使い方(sudo可ユーザで): bash deploy/install-libredwg.sh
#   導入後、端子アプリ再起動: sudo systemctl restart terminal-system
set -euo pipefail

echo "== LibreDWG(dwg2dxf) 導入 =="

if command -v dwg2dxf >/dev/null 2>&1; then
  echo "既に導入済み: $(command -v dwg2dxf)"
  dwg2dxf --version 2>&1 | head -1 || true
  exit 0
fi

PKG=""
command -v dnf >/dev/null 2>&1 && PKG=dnf
[ -z "$PKG" ] && command -v yum >/dev/null 2>&1 && PKG=yum

# 1) パッケージで入れば一番簡単(ディストリによる)
if [ -n "$PKG" ]; then
  echo "-- $PKG でパッケージ導入を試行"
  if sudo "$PKG" install -y libredwg >/dev/null 2>&1 && command -v dwg2dxf >/dev/null 2>&1; then
    echo "パッケージ導入成功: $(command -v dwg2dxf)"; exit 0
  fi
  echo "   パッケージが無い/失敗 → ソースビルドへ"
fi

# 2) ソースからビルド(configure済みの公式リリース tarball を使う=autotools不要)
VER="0.13.3"
TARBALL="libredwg-${VER}.tar.xz"
URL="https://ftp.gnu.org/gnu/libredwg/${TARBALL}"
WORK="$(mktemp -d)"
echo "-- ビルド依存を導入"
if [ -n "$PKG" ]; then
  sudo "$PKG" install -y gcc make xz tar pcre2-devel >/dev/null 2>&1 || \
    sudo "$PKG" install -y gcc make xz tar >/dev/null 2>&1 || true
fi
echo "-- ソース取得: $URL"
cd "$WORK"
if command -v curl >/dev/null 2>&1; then
  curl -fsSL -o "$TARBALL" "$URL"
else
  wget -q -O "$TARBALL" "$URL"
fi
tar xf "$TARBALL"
cd "libredwg-${VER}"
echo "-- configure(バインディング無効=軽量・高速)"
./configure --disable-bindings --disable-shared --enable-static >/dev/null
echo "-- make(数分かかります)"
make -j"$(nproc 2>/dev/null || echo 1)" >/dev/null
echo "-- インストール"
sudo make install >/dev/null
sudo ldconfig 2>/dev/null || true

if command -v dwg2dxf >/dev/null 2>&1; then
  echo "導入成功: $(command -v dwg2dxf)"
  dwg2dxf --version 2>&1 | head -1 || true
  echo "次: sudo systemctl restart terminal-system"
else
  echo "!! 導入に失敗しました。OSとエラーを確認してください(cat /etc/os-release)。" >&2
  exit 1
fi
