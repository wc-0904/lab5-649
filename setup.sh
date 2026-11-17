#!/usr/bin/env bash
# Source from lab5: source ./setup.sh (bash or zsh).
if [ -n "${ZSH_VERSION:-}" ]; then
    lab5_setup_file="${(%):-%N}"
else
    lab5_setup_file="${BASH_SOURCE[0]}"
fi
lab5_app_dir="$(cd "$(dirname "$lab5_setup_file")" && pwd)"
lab5_workspace="$(cd "$lab5_app_dir/../.." && pwd)"
if [ ! -f "$lab5_workspace/.venv/bin/activate" ]; then
    echo "Missing workspace venv: $lab5_workspace/.venv" >&2
    return 1
fi
source "$lab5_workspace/.venv/bin/activate"
export ZEPHYR_BASE="$(cd "$lab5_app_dir/.." && pwd)"
export ZEPHYR_TOOLCHAIN_VARIANT=zephyr
# Keep compiler cache inside this writable application directory.
export CCACHE_DIR="$lab5_app_dir/.cache/ccache"
unset lab5_setup_file lab5_app_dir lab5_workspace
