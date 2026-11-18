#!/usr/bin/env bash
# Source from lab5-649 on WSL: source ./setup_win.sh (bash or zsh).
#
# setup.sh is the Mac layout: it expects the workspace venv two directories up
# and ZEPHYR_BASE at this directory's parent. Neither is true on WSL, where the
# Zephyr workspace and the SDK live in the home directory, so this script sets
# the environment for that layout instead. Use one or the other, never both.
#
# Environment only. Build afterwards with:
#   west build -b nucleo_f401re/stm32f401xe -d build-demo-n2 . -- -DLAB_NODE_ID=2
#   west build -b nucleo_f401re/stm32f401xe -d build-demo-n3 . -- -DLAB_NODE_ID=3

# Override either of these before sourcing if the paths ever move.
: "${ZEPHYR_WORKSPACE:=$HOME/zephyrproject}"
: "${ZEPHYR_SDK:=$HOME/zephyr-sdk-1.0.1}"

if [ -n "${ZSH_VERSION:-}" ]; then
    lab5_setup_file="${(%):-%N}"
else
    lab5_setup_file="${BASH_SOURCE[0]}"
fi
lab5_app_dir="$(cd "$(dirname "$lab5_setup_file")" && pwd)"

lab5_ok=1
if [ ! -f "$ZEPHYR_WORKSPACE/.venv/bin/activate" ]; then
    echo "Missing venv: $ZEPHYR_WORKSPACE/.venv" >&2
    lab5_ok=0
fi
if [ ! -d "$ZEPHYR_WORKSPACE/zephyr" ]; then
    echo "Missing Zephyr tree: $ZEPHYR_WORKSPACE/zephyr" >&2
    lab5_ok=0
fi
if [ ! -d "$ZEPHYR_SDK" ]; then
    echo "Missing Zephyr SDK: $ZEPHYR_SDK" >&2
    lab5_ok=0
fi
if [ "$lab5_ok" -eq 0 ]; then
    unset lab5_setup_file lab5_app_dir lab5_ok
    return 1
fi

source "$ZEPHYR_WORKSPACE/.venv/bin/activate"
export ZEPHYR_BASE="$ZEPHYR_WORKSPACE/zephyr"
export ZEPHYR_TOOLCHAIN_VARIANT=zephyr
export ZEPHYR_SDK_INSTALL_DIR="$ZEPHYR_SDK"
# Keep the compiler cache inside this writable application directory.
export CCACHE_DIR="$lab5_app_dir/.cache/ccache"

echo "Zephyr $(cat "$ZEPHYR_BASE/VERSION" 2>/dev/null | awk -F'= ' '/VERSION_MAJOR|VERSION_MINOR/ {printf "%s.", $2}' | sed 's/\.$//')  SDK $(cat "$ZEPHYR_SDK/sdk_version" 2>/dev/null)  west $(west --version 2>/dev/null | awk '{print $3}')"
echo "ZEPHYR_BASE=$ZEPHYR_BASE"
echo "Build: west build -b nucleo_f401re/stm32f401xe -d build-demo-n2 . -- -DLAB_NODE_ID=2"

unset lab5_setup_file lab5_app_dir lab5_ok
