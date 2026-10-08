#!/usr/bin/env bash
# Local bootstrap: copy the entire bundle, then invoke this entry point.
set -u
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
[[ -f "$HERE/launcher.sh" && -f "$HERE/check.py" ]] || { printf '启动包不完整，请复制整个目录。\n' >&2; exit 1; }
exec bash "$HERE/launcher.sh" install
