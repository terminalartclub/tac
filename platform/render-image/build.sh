#!/bin/sh
# Build tac-render:local from the plugin's lib/ (single source of render_piece/check_piece/vscreen).
#   platform/render-image/build.sh            -> tac-render:local
#   TAC_RENDER_IMAGE=tac-render:abc build.sh  -> custom tag
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
LIB="$HERE/../../plugins/tac-studio/lib"
exec docker build -f "$HERE/Dockerfile" -t "${TAC_RENDER_IMAGE:-tac-render:local}" "$LIB"
