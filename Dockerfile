FROM rocker/r2u:24.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PATH=/opt/ontoanno-venv/bin:$PATH \
    ONTOANNO_RSCRIPT=/usr/local/bin/Rscript \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false \
    MPLCONFIGDIR=/app/.cache/matplotlib

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-pip \
    python3-dev \
    python3-venv \
    poppler-utils \
    libcurl4-openssl-dev \
    libssl-dev \
    libxml2-dev \
    libfontconfig1-dev \
    libharfbuzz-dev \
    libfribidi-dev \
    libfreetype6-dev \
    libpng-dev \
    libtiff5-dev \
    libjpeg-dev \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml /app/pyproject.toml
COPY src /app/src
COPY GPTAnno /app/GPTAnno
COPY resources /app/resources
COPY scripts /app/scripts
COPY configs /app/configs
COPY figures /app/figures
COPY ontoanno /app/ontoanno
COPY docker /app/docker
COPY README.md /app/README.md

RUN mkdir -p /app/.cache/matplotlib /app/runs /work /data

RUN Rscript -e 'repos <- getOption("repos"); deps <- c("Depends", "Imports", "LinkingTo"); pkgs <- c("Seurat", "ontologyIndex", "jsonlite", "ggplot2", "dplyr", "magrittr", "tidyr", "stringr", "igraph", "httr", "patchwork", "rlang", "pkgload", "ellmer"); for (pkg in pkgs) { if (!requireNamespace(pkg, quietly = TRUE)) { message("Installing R package: ", pkg); install.packages(pkg, repos = repos, dependencies = deps) } else { message("R package already installed: ", pkg) }; if (!requireNamespace(pkg, quietly = TRUE)) stop("Failed to install R package: ", pkg) }'

RUN python3 -m venv /opt/ontoanno-venv && \
    /opt/ontoanno-venv/bin/python -m pip install --upgrade pip setuptools wheel && \
    /opt/ontoanno-venv/bin/python -m pip install ".[ui]" && \
    /opt/ontoanno-venv/bin/python -m pip install -r /app/GPTAnno/PDF2markers/requirements.txt

RUN chmod +x /app/ontoanno /app/docker/entrypoint.sh

EXPOSE 8501

ENTRYPOINT ["/app/docker/entrypoint.sh"]
