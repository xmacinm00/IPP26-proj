### podman

ARG PYTHON_IMAGE=python:3.14-slim-bookworm
ARG NODE_IMAGE=node:24-bookworm

FROM ${NODE_IMAGE} AS nodebase

# ============================================================
# check
# Environment for code-quality tools for both parts.
# Host dirs will be mounted to /src/int and /src/tester.
# Entry point must be bash.
# ============================================================
FROM ${PYTHON_IMAGE} AS check

RUN apt-get update && apt-get install -y --no-install-recommends \
    bash \
    ca-certificates \
    curl \
    build-essential \
    pkg-config \
    libxml2-dev \
    libxslt1-dev \
    && rm -rf /var/lib/apt/lists/*

COPY --from=nodebase /usr/local/ /usr/local/

WORKDIR /opt/check

COPY int/requirements.txt /opt/check/int-requirements.txt
COPY int/requirements-dev.txt /opt/check/int-requirements-dev.txt
RUN python -m pip install --no-cache-dir --upgrade pip setuptools wheel uv && \
    python -m pip install --no-cache-dir \
      -r /opt/check/int-requirements.txt \
      -r /opt/check/int-requirements-dev.txt

COPY tester/package.json tester/package-lock.json /opt/check/tester/
RUN npm ci --prefix /opt/check/tester

ENV PATH="/opt/check/tester/node_modules/.bin:${PATH}"

WORKDIR /src
ENTRYPOINT ["/bin/bash"]

# ============================================================
# build
# Build / prepare interpreter side
# ============================================================
FROM ${PYTHON_IMAGE} AS build

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    build-essential \
    pkg-config \
    libxml2-dev \
    libxslt1-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/int

COPY int/requirements.txt /opt/int/requirements.txt
RUN python -m pip install --no-cache-dir --upgrade pip setuptools wheel && \
    python -m pip install --no-cache-dir -r /opt/int/requirements.txt

COPY int/ /opt/int/
RUN python -m compileall -q /opt/int/src

# ============================================================
# build-test
# Build TypeScript tester
# ============================================================
FROM ${NODE_IMAGE} AS build-test

WORKDIR /opt/tester

COPY tester/package.json tester/package-lock.json /opt/tester/
RUN npm ci

COPY tester/ /opt/tester/
RUN chmod +x /opt/tester/eslint /opt/tester/prettier || true
RUN npm run build

# ============================================================
# runtime
# Minimal image directly running the interpreter
# ============================================================
FROM ${PYTHON_IMAGE} AS runtime

RUN apt-get update && apt-get install -y --no-install-recommends \
    bash \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/int

COPY int/requirements.txt /opt/int/requirements.txt
RUN python -m pip install --no-cache-dir --upgrade pip setuptools wheel uv && \
    python -m pip install --no-cache-dir -r /opt/int/requirements.txt

COPY --from=build /opt/int/src /opt/int/src

ENTRYPOINT ["python", "/opt/int/src/solint.py"]

# ============================================================
# test
# Derived from runtime, runs the TS tester
# ============================================================
FROM runtime AS test

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    diffutils \
    build-essential \
    pkg-config \
    libxml2-dev \
    libxslt1-dev \
    zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

COPY --from=nodebase /usr/local/ /usr/local/

WORKDIR /opt/tester

COPY --from=build-test /opt/tester/dist /opt/tester/dist
COPY --from=build-test /opt/tester/node_modules /opt/tester/node_modules
COPY --from=build-test /opt/tester/package.json /opt/tester/package.json
COPY --from=build-test /opt/tester/package-lock.json /opt/tester/package-lock.json

# Bundle the provided SOL2XML compiler into the test image
COPY tester/sol2xml /opt/tester/sol2xml
RUN python -m pip install --no-cache-dir -r /opt/tester/sol2xml/requirements.txt
RUN chmod +x /opt/tester/sol2xml/sol_to_xml.py

ENV SOL26_INTERPRETER_PATH=/opt/int/src/solint.py
ENV SOL2XML_PATH=/opt/tester/sol2xml/sol_to_xml.py

ENTRYPOINT ["node", "/opt/tester/dist/tester.js"]
CMD ["--help"]