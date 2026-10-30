# syntax=docker/dockerfile:1
FROM python:3.12-slim-trixie
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 CERTICOEX_DATA=/app/data
WORKDIR /app
# The optional secret is used only in this managed development environment.
# Render uses the normal system CA store. Verification stays enabled in both.
RUN --mount=type=secret,id=proxy_ca,required=false \
    set -eu; \
    certicoex_apt_options=''; \
    if [ -f /run/secrets/proxy_ca ]; then certicoex_apt_options='-o Acquire::https::CaInfo=/run/secrets/proxy_ca'; fi; \
    apt-get $certicoex_apt_options update; \
    apt-get $certicoex_apt_options install -y --no-install-recommends libreoffice-writer fonts-crosextra-carlito fonts-liberation fontconfig; \
    rm -rf /var/lib/apt/lists/*; \
    useradd --create-home --uid 10001 certicoex; \
    mkdir -p /app/data; chown certicoex:certicoex /app/data
COPY requirements.txt ./
RUN --mount=type=secret,id=proxy_ca,required=false \
    set -eu; \
    if [ -f /run/secrets/proxy_ca ]; then export PIP_CERT=/run/secrets/proxy_ca; fi; \
    python -m pip install --no-cache-dir -r requirements.txt
ENV CERTICOEX_AUTH_REQUIRED=true
COPY --chown=certicoex:certicoex app ./app
COPY --chown=certicoex:certicoex static ./static
COPY --chown=certicoex:certicoex defaults ./defaults
USER certicoex
EXPOSE 10000
CMD ["python", "-m", "app.server"]
