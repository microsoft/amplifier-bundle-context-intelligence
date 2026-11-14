FROM python:3.12-slim-bookworm AS references
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY *.py /build-input/
COPY harness-manifest.json /harness-manifest.json
RUN echo '9277fb53e1c6adff58b25dd9376f758b463b6cc25076c988375a2d761292676b  /harness-manifest.json' \
    | sha256sum --check --strict
# Independent inputs: full-SHA Git archives, plus wheel ZIPs downloaded BEFORE
# installation. This stage never imports installed candidate packages.
RUN git init /ci-reference && git -C /ci-reference remote add origin \
    https://github.com/microsoft/amplifier-bundle-context-intelligence \
    && git -C /ci-reference fetch --depth=1 origin e6de12c343a533187b836f4a99042c415eed2f18 \
    && git -C /ci-reference archive --format=tar e6de12c343a533187b836f4a99042c415eed2f18 > /ci.tar
RUN git init /server-reference && git -C /server-reference remote add origin \
    https://github.com/microsoft/amplifier-context-intelligence \
    && git -C /server-reference fetch --depth=1 origin 1622b48b05fcd93f935e5d55b962081a2c0cb229 \
    && git -C /server-reference archive --format=tar 1622b48b05fcd93f935e5d55b962081a2c0cb229 > /server.tar
RUN pip download --only-binary=:all: --no-deps --dest /wheels amplifier-core==2.0.2 Pillow==13.0.0 \
    && python /build-input/provenance.py --ci-archive /ci.tar --server-archive /server.tar \
    --wheels /wheels --harness /build-input --harness-manifest /harness-manifest.json \
    --output /reference.json

FROM python:3.12-slim-bookworm AS base
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir --no-compile uv==0.9.30
RUN useradd --uid 1000 --create-home user && mkdir /state \
    && chown user:user /state
COPY *.py /qualification/
COPY --from=references /reference.json /reference.sha256 /qualification/
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /home/user

FROM base AS hook
COPY --from=references /wheels /reference-wheels
# Override the hook's production @main shared-library requirement, not its code.
RUN printf '%s\n' \
    'amplifier-bundle-context-intelligence @ git+https://github.com/microsoft/amplifier-bundle-context-intelligence@e6de12c343a533187b836f4a99042c415eed2f18' \
    > /qualification/overrides.txt \
    && uv pip install --system --override /qualification/overrides.txt \
    /reference-wheels/*.whl \
    'amplifier-module-hook-context-intelligence @ git+https://github.com/microsoft/amplifier-bundle-context-intelligence@e6de12c343a533187b836f4a99042c415eed2f18#subdirectory=modules/hook-context-intelligence'
USER user

FROM base AS server
RUN pip install --no-cache-dir --no-compile \
    'context-intelligence-server @ git+https://github.com/microsoft/amplifier-context-intelligence@1622b48b05fcd93f935e5d55b962081a2c0cb229'
USER user