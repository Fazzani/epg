#!/bin/sh
set -eu
repo_dir="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
printf '%s\n' 'Deprecated: use python update_index.py --output-dir dist' >&2
exec python3 "$repo_dir/update_index.py" --output-dir "${1:-$repo_dir/dist}"
