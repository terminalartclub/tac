#!/bin/sh
# Build the render image from the plugin's lib/ (single source of render_piece/check_piece/vscreen)
# plus this directory's fly_bootstrap.py.
#   platform/render-image/build.sh                          -> tac-render:local
#   TAC_RENDER_IMAGE=registry.fly.io/tac-render:<sha> TAC_RENDER_PLATFORM=linux/amd64 build.sh
#                                                            -> image for Fly Machines (x86_64 hosts)
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
LIB="$HERE/../../plugins/tac-studio/lib"
exec docker build ${TAC_RENDER_PLATFORM:+--platform "$TAC_RENDER_PLATFORM"} \
  -f "$HERE/Dockerfile" --build-context "bootstrap=$HERE" \
  -t "${TAC_RENDER_IMAGE:-tac-render:local}" "$LIB"
