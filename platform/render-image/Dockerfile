# tac-render: runs builder-community's check_piece.py / render_piece.py on untrusted pieces.
# Build context = plugins/tac-studio/lib (see build.sh). No uv, no network at run time.
FROM python:3.12-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends fonts-dejavu-core \
 && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir --no-compile rich==15.0.0 pillow==12.3.0 fonttools==4.66.1

COPY *.py /app/
RUN python -m compileall -q /app && chmod -R a+rX /app

# read-only rootfs at run time: nothing may want to write outside /tmp (tmpfs) and /out
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HOME=/tmp MPLCONFIGDIR=/tmp
USER 65534:65534
WORKDIR /tmp
