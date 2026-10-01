#!/usr/bin/env bash
# ログインID/パスワードを対話入力で設定し直し、サービスを再起動する。
# 端末の貼り付け二重化を避けるため、値は「その場で手入力(read)」する。
# 使い方:  bash deploy/set-password.sh
set -euo pipefail
ENV=/etc/harness-system.env

read -r -p "ログインID (空Enterで harness): " U; U="${U:-harness}"
# パスワードは空白/引用符/#を避けると環境ファイルで安全(英数＋-_ 推奨)
read -r -s -p "パスワード: " P; echo
read -r -s -p "パスワード(確認): " P2; echo
[ -n "$P" ] || { echo "パスワードが空です。中止。"; exit 1; }
[ "$P" = "$P2" ] || { echo "確認用と一致しません。中止。"; exit 1; }

# 値は argv ではなく環境変数で子プロセスへ渡す(ps露出回避)。sudo env で確実に引き継ぐ。
sudo env _U="$U" _P="$P" python3 - "$ENV" <<'PY'
import os, sys, secrets
env = sys.argv[1]
kv = {}
try:
    for ln in open(env).read().splitlines():
        if '=' in ln and not ln.strip().startswith('#'):
            k, _, v = ln.partition('='); kv[k.strip()] = v
except FileNotFoundError:
    pass
kv['HARNESS_USER'] = os.environ['_U']
kv['HARNESS_PASSWORD'] = os.environ['_P']
if not kv.get('HARNESS_SECRET'):
    kv['HARNESS_SECRET'] = secrets.token_hex(32)
kv.setdefault('HARNESS_WORK', '/var/lib/harness-system/work')
kv.setdefault('HARNESS_STATE', '/var/lib/harness-system/state')
order = ['HARNESS_USER', 'HARNESS_PASSWORD', 'HARNESS_SECRET', 'HARNESS_WORK', 'HARNESS_STATE']
open(env, 'w').write('\n'.join(f'{k}={kv[k]}' for k in order) + '\n')
print('updated:', env, '/ user=', kv['HARNESS_USER'], '/ pw length=', len(kv['HARNESS_PASSWORD']))
PY

sudo chmod 600 "$ENV"
sudo systemctl restart harness-system
sleep 2
echo "is-active: $(sudo systemctl is-active harness-system)"
code=$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8002/)
echo "GET / -> HTTP $code  (302 ならログイン有効・成功)"
