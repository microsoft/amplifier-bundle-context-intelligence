FROM python:3.12-slim-bookworm AS base
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
RUN useradd --uid 1000 --create-home user && mkdir /state \
    && chown user:user /state
COPY qualify.py readiness.py start_server.py /qualification/
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /home/user

FROM base AS hook
RUN pip install --no-cache-dir uv==0.9.30
# Override the hook's production @main shared-library requirement, not its code.
RUN printf '%s\n' \
    'amplifier-bundle-context-intelligence @ git+https://github.com/microsoft/amplifier-bundle-context-intelligence@e6de12c343a533187b836f4a99042c415eed2f18' \
    > /qualification/overrides.txt \
    && uv pip install --system --override /qualification/overrides.txt \
    'amplifier-core==2.0.2' 'Pillow==13.0.0' \
    'amplifier-module-hook-context-intelligence @ git+https://github.com/microsoft/amplifier-bundle-context-intelligence@e6de12c343a533187b836f4a99042c415eed2f18#subdirectory=modules/hook-context-intelligence'
USER user

FROM base AS server
RUN pip install --no-cache-dir \
    'context-intelligence-server @ git+https://github.com/microsoft/amplifier-context-intelligence@1622b48b05fcd93f935e5d55b962081a2c0cb229'
USER user