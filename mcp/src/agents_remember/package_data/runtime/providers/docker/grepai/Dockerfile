ARG GREPAI_VERSION
ARG GREPAI_ARCH

FROM debian:bookworm-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl tar \
    && rm -rf /var/lib/apt/lists/*

RUN set -eux; \
    release_base="https://github.com/yoanbernabeu/grepai/releases/download/v${GREPAI_VERSION}"; \
    asset_name="grepai_${GREPAI_VERSION}_linux_${GREPAI_ARCH}.tar.gz"; \
    curl -fsSL -o /tmp/grepai.tar.gz "${release_base}/${asset_name}"; \
    tar -xzf /tmp/grepai.tar.gz -C /tmp; \
    find /tmp -type f -name grepai -exec install -m 0755 {} /usr/local/bin/grepai \; -quit; \
    test -x /usr/local/bin/grepai; \
    rm -rf /tmp/*

ENTRYPOINT ["grepai"]
