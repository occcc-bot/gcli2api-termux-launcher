#!/usr/bin/env bash
# Build the single-file launcher by embedding check.py into launcher.sh.
# The result needs no directory, no clone: one curl and it runs.
set -eu
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
OUT="${1:-$HERE/dist/gcli2api.sh}"

SRC="$HERE/launcher.sh"
HELPER="$HERE/check.py"
[[ -f "$SRC" && -f "$HELPER" ]] || { printf '缺少 launcher.sh 或 check.py\n' >&2; exit 1; }

# Refuse to embed a payload that would terminate the heredoc early.
grep -qx 'GCLI_HELPER_EOF' "$HELPER" && { printf 'check.py 含 heredoc 终止符，拒绝构建。\n' >&2; exit 1; }

digest=$(sha256sum "$HELPER" | cut -d' ' -f1)
mkdir -p "$(dirname -- "$OUT")"

# Substitute the payload line and the placeholder hash in one pass.
python3 - "$SRC" "$HELPER" "$digest" "$OUT" <<'PY'
import sys
src, helper, digest, out = sys.argv[1:5]
text = open(src, encoding='utf-8').read()
payload = open(helper, encoding='utf-8').read()
if payload and not payload.endswith('\n'):
    payload += '\n'
needle = '__GCLI_HELPER_PAYLOAD__\n'
if needle not in text:
    sys.exit('launcher.sh 缺少 __GCLI_HELPER_PAYLOAD__ 占位符')
text = text.replace(needle, payload, 1)
needle = "EMBEDDED_HELPER_SHA256=''"
if needle not in text:
    sys.exit("launcher.sh 缺少空值形式的 EMBEDDED_HELPER_SHA256 占位")
text = text.replace(needle, "EMBEDDED_HELPER_SHA256='%s'" % digest, 1)
open(out, 'w', encoding='utf-8').write(text)
PY

chmod +x "$OUT"
bash -n "$OUT" || { printf '构建结果语法错误。\n' >&2; exit 1; }

# The placeholder in the template must survive in repo mode; confirm the built
# file no longer contains it, otherwise embedding silently failed.
grep -q '__GCLI_HELPER_PAYLOAD__' "$OUT" && { printf '占位符未被替换。\n' >&2; exit 1; }

printf '已生成单文件启动器：%s\n' "$OUT"
printf '大小：%s 字节\n' "$(wc -c <"$OUT")"
printf '内嵌 check.py SHA-256：%s\n' "$digest"
