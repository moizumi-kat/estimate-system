#!/usr/bin/env bash
# SessionStart hook: ハーネスデータ自動生成の「承認済みルール台帳」を
# 毎セッション冒頭でコンテキストへ強制投入する。茂泉様と確定したルールを
# Claude が読み忘れないための仕組み（マークダウンを足すだけでは忘れるため）。
set -euo pipefail
ROOT="${CLAUDE_PROJECT_DIR:-$(pwd)}"
LEDGER="$ROOT/wireharness/fromto_qc/生成ルール_承認済.md"
if [ -f "$LEDGER" ]; then
  echo "===== 🔴 ハーネス承認済みルール台帳（毎回必読・茂泉様確定済） ====="
  echo "出典: wireharness/fromto_qc/生成ルール_承認済.md ／ 変更は承認が要る・回帰ゼロ厳守"
  echo
  cat "$LEDGER"
  echo
  echo "===== 台帳ここまで。これに反する測定・実装・質問をしないこと ====="
fi
