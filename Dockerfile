FROM rocker/tidyverse:4.4.1

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    ONTOANNO_RSCRIPT=/usr/local/bin/Rscript \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false \
    MPLCONFIGDIR=/app/.cache/matplotlib

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-pip \
    python3-venv \
    python3-dev \
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
    libgit2-dev \
    libglpk-dev \
    libudunits2-dev \
    libgdal-dev \
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

RUN install2.r --error --skipinstalled \
    Seurat \
    ontologyIndex \
    jsonlite \
    ggplot2 \
    dplyr \
    magrittr \
    tidyr \
    stringr \
    igraph \
    httr \
    patchwork \
    rlang \
    pkgload \
    ellmer \
    openai

RUN python3 -m pip install --upgrade pip setuptools wheel && \
    python3 -m pip install ".[ui]" && \
    python3 -m pip install -r /app/GPTAnno/PDF2markers/requirements.txt

RUN chmod +x /app/ontoanno /app/docker/entrypoint.sh

EXPOSE 8501

ENTRYPOINT ["/app/docker/entrypoint.sh"]

