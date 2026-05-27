FROM ubuntu:jammy AS builder

RUN apt-get update && \
  DEBIAN_FRONTEND=noninteractive TZ="America/Los_Angeles" \
  apt-get install -y \
  build-essential \
  bash \
  libzip-dev \
  libssl-dev \
  libcurl4-gnutls-dev \
  libpugixml-dev

WORKDIR /usr/src

# Build from vendored sources instead of cloning forge.soutade.fr (which has
# been unreliable). vendor/libgourou is libgourou @324c566 with its uPDFParser
# dependency pre-placed at lib/updfparser, so no network access is needed.
# See vendor/README.md for provenance.
COPY vendor/libgourou /usr/src/libgourou

RUN cd /usr/src/libgourou/lib/updfparser \
  && make BUILD_STATIC=1 BUILD_SHARED=0 \
  && cd /usr/src/libgourou \
  && make BUILD_STATIC=1 STATIC_UTILS=1

FROM ubuntu:jammy

COPY --from=builder /usr/src/libgourou/utils/acsmdownloader \
                    /usr/src/libgourou/utils/adept_activate \
                    /usr/src/libgourou/utils/adept_remove \
                    /usr/src/libgourou/utils/adept_loan_mgt \
                    /usr/local/bin/

RUN apt-get update && \
  DEBIAN_FRONTEND=noninteractive TZ="America/Los_Angeles" \
  apt-get install -y \
  python3 \
  python3-psycopg2 \
  libpugixml1v5 \
  libzip4 \
  libssl3 \
  libcurl4-gnutls-dev \
  && apt-get autoclean \
  && rm -rf /var/lib/apt/lists/*

COPY scripts/server.py /home/libgourou/server.py
WORKDIR /home/libgourou

EXPOSE 8080
CMD ["python3", "/home/libgourou/server.py"]
