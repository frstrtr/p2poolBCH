# syntax=docker/dockerfile:1
# P2Pool BCH
#   Runtime: PyPy 2.7 v7.3.20 + Twisted 18.9.0 (linked to local OpenSSL 1.1)
#   Bot:     Python 3 venv with python-telegram-bot ≥ 20
#
# Build args (override with --build-arg):
#   PYPY_VERSION    PyPy release (default: 7.3.20)
#   OPENSSL_VERSION OpenSSL 1.1 patch release (default: 1.1.1w)

##############################################################################
# Stage 1 — compile deps
##############################################################################
FROM ubuntu:24.04 AS builder

ARG PYPY_VERSION=7.3.20
ARG OPENSSL_VERSION=1.1.1w

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update -qq && apt-get install -y --no-install-recommends \
        ca-certificates curl build-essential \
        libffi-dev python3 python3-venv \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

# ── PyPy 2.7 binary ────────────────────────────────────────────────────────
RUN curl -fsSL \
        "https://downloads.python.org/pypy/pypy2.7-v${PYPY_VERSION}-linux64.tar.bz2" \
    | tar -xj \
    && mv "pypy2.7-v${PYPY_VERSION}-linux64" /opt/pypy2

# ── OpenSSL 1.1 (Ubuntu 24.04 ships only OpenSSL 3) ────────────────────────
RUN curl -fsSL \
        "https://www.openssl.org/source/openssl-${OPENSSL_VERSION}.tar.gz" \
    | tar -xz \
    && cd "openssl-${OPENSSL_VERSION}" \
    && ./config \
        --prefix=/opt/openssl-1.1 \
        --openssldir=/opt/openssl-1.1 \
        shared no-tests \
    && make -j"$(nproc)" \
    && make install_sw \
    && cd .. && rm -rf "openssl-${OPENSSL_VERSION}"

# ── pip for PyPy2 ───────────────────────────────────────────────────────────
RUN curl -fsSL https://bootstrap.pypa.io/pip/2.7/get-pip.py \
    | /opt/pypy2/bin/pypy

# ── PyPy2 runtime dependencies ──────────────────────────────────────────────
# Pin pip/setuptools to last Python-2-compatible versions.
# cryptography<3.0 must be compiled from source against our local OpenSSL 1.1.
RUN /opt/pypy2/bin/pypy -m pip install --upgrade "pip<21" "setuptools<45" wheel \
 && /opt/pypy2/bin/pypy -m pip install typing "incremental==21.3.0" pycparser cffi \
 && LDFLAGS="-L/opt/openssl-1.1/lib" \
    CFLAGS="-I/opt/openssl-1.1/include" \
    OPENSSL_DIR="/opt/openssl-1.1" \
    /opt/pypy2/bin/pypy -m pip install \
        --no-build-isolation \
        --no-binary :all: \
        "cryptography<3.0" \
 && /opt/pypy2/bin/pypy -m pip install \
        "Twisted==18.9.0" \
        "pyOpenSSL<20.0" \
        "service_identity<18.2" \
        argparse

# ── Python 3 bot venv ───────────────────────────────────────────────────────
# Install dependencies for BOTH bot impls (PTB / Bot-API and Telethon /
# MTProto) so the image works regardless of BOT_IMPL at runtime.  Combined
# pip footprint is ~25 MB; the choice is just deploy-time switching.
COPY telegram_bot/requirements.txt          /tmp/bot-req-ptb.txt
COPY telegram_bot_mtproto/requirements.txt  /tmp/bot-req-mtproto.txt
RUN python3 -m venv /opt/bot-venv \
 && /opt/bot-venv/bin/pip install --no-cache-dir -r /tmp/bot-req-ptb.txt \
 && /opt/bot-venv/bin/pip install --no-cache-dir -r /tmp/bot-req-mtproto.txt

##############################################################################
# Stage 2 — runtime image (lean)
##############################################################################
FROM ubuntu:24.04

LABEL org.opencontainers.image.title="p2pool-BCH"
LABEL org.opencontainers.image.description="P2Pool mining node for Bitcoin Cash"
LABEL org.opencontainers.image.source="https://github.com/frstrtr/p2poolBCH"

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update -qq && apt-get install -y --no-install-recommends \
        ca-certificates libffi8 python3 \
    && rm -rf /var/lib/apt/lists/*

# Copy artefacts from builder
COPY --from=builder /opt/pypy2      /opt/pypy2
COPY --from=builder /opt/openssl-1.1 /opt/openssl-1.1
COPY --from=builder /opt/bot-venv   /opt/bot-venv

ENV PATH="/opt/pypy2/bin:$PATH"
ENV LD_LIBRARY_PATH="/opt/openssl-1.1/lib"

WORKDIR /p2pool
COPY . .
COPY contrib/docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh

# Bake the build's git revision into a VERSION file so _get_version() in
# p2pool/__init__.py reports something useful at runtime instead of falling
# through to "unknown 7032706f6f6c" (= ASCII 'p2pool' in hex) when no git
# binary or .git directory is available in the runtime image.  The build
# action / docker buildx command should pass --build-arg P2POOL_VERSION=...
# (e.g. "$(git describe --always --dirty)").  Falls back to extracting from
# the bind-mounted .git/HEAD if neither was passed.
ARG P2POOL_VERSION=
RUN set -e; \
    if [ -n "${P2POOL_VERSION}" ]; then \
        echo "${P2POOL_VERSION}" > /p2pool/VERSION; \
    elif [ -f /p2pool/.git/HEAD ]; then \
        head_ref=$(cat /p2pool/.git/HEAD); \
        case "$head_ref" in \
            "ref: "*) ref=${head_ref#ref: }; \
                      cat "/p2pool/.git/$ref" 2>/dev/null | cut -c1-7 > /p2pool/VERSION ;; \
            *)        echo "$head_ref" | cut -c1-7 > /p2pool/VERSION ;; \
        esac; \
    else \
        echo "docker-build" > /p2pool/VERSION; \
    fi; \
    echo "Baked p2pool VERSION=$(cat /p2pool/VERSION)"

# 9348 — Stratum + Web UI (miners connect here)
# 9349 — p2pool P2P network (inter-node, forward from router)
EXPOSE 9348 9349

ENTRYPOINT ["/docker-entrypoint.sh"]
