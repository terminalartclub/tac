# tac-render: runs builder-community's check_piece.py / render_piece.py on untrusted pieces.
# One image, two backends:
#   DockerRenderer      docker run --user 65534:65534 --network none --read-only ... (see SECURITY.md)
#   FlyMachineRenderer  init.exec = python /app/fly_bootstrap.py, as root in a throwaway microVM; the
#                       bootstrap drops each untrusted step into a new netns as uid 65534
# Build context = plugins/tac-studio/lib (see build.sh). No uv; nothing is fetched at run time.
FROM python:3.12-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends fonts-dejavu-core \
 && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir --no-compile rich==15.0.0 pillow==12.3.0 fonttools==4.66.1

COPY *.py /app/
COPY --from=bootstrap fly_bootstrap.py /app/fly_bootstrap.py
RUN python -m compileall -q /app && chmod -R a+rX,go-w /app

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HOME=/tmp MPLCONFIGDIR=/tmp
WORKDIR /tmp
# No USER line on purpose: DockerRenderer always passes --user 65534:65534; the Fly bootstrap
# needs root to create the network namespace and then drops to 65534 itself.
