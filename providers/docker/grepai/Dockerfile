FROM debian:bookworm-slim

# Build args are declared inside the build stage (after FROM) so they are in
# scope for the RUN below. ARGs declared before FROM are only visible to the
# FROM line itself, which this image does not parameterize; referencing them in
# RUN would expand to empty and `set -u` would abort the download step.
ARG GREPAI_VERSION
ARG GREPAI_ARCH

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
