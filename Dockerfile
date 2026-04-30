FROM rocker/r2u:24.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PATH=/opt/ontoanno-venv/bin:$PATH \
    ONTOANNO_RSCRIPT=/usr/bin/Rscript \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false \
    MPLCONFIGDIR=/app/.cache/matplotlib

WORKDIR /app
SHELL ["/bin/bash", "-o", "pipefail", "-c"]

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    cmake \
    gfortran \
    libgit2-dev \
    python3 \
    python3-pip \
    python3-dev \
    python3-venv \
    pkg-config \
    poppler-utils \
    libcurl4-openssl-dev \
    libglpk-dev \
    libhdf5-dev \
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
COPY GPTAnno/PDF2markers/requirements.txt /app/GPTAnno/PDF2markers/requirements.txt
COPY docker/r-packages.txt /tmp/ontoanno-r-packages.txt
COPY docker/install_r_dependencies.R /tmp/install_r_dependencies.R

RUN mkdir -p /app/.cache/matplotlib /app/runs /work /data

RUN apt-get update && \
    apt_packages=() && \
    while IFS= read -r pkg; do \
      pkg="${pkg%%#*}"; \
      pkg="$(printf '%s' "${pkg}" | tr -d '[:space:]')"; \
      [[ -z "${pkg}" ]] && continue; \
      apt_name="r-cran-$(tr '[:upper:]' '[:lower:]' <<< "${pkg}")"; \
      if apt-cache show "${apt_name}" >/dev/null 2>&1; then \
        apt_packages+=("${apt_name}"); \
      else \
        printf 'No r2u binary found for %s (%s); install.packages fallback will verify it.\n' "${pkg}" "${apt_name}"; \
      fi; \
    done < /tmp/ontoanno-r-packages.txt && \
    if [[ "${#apt_packages[@]}" -gt 0 ]]; then \
      apt-get install -y --no-install-recommends "${apt_packages[@]}"; \
    fi && \
    Rscript /tmp/install_r_dependencies.R /tmp/ontoanno-r-packages.txt && \
    rm -rf /var/lib/apt/lists/*

RUN python3 -m venv /opt/ontoanno-venv && \
    /opt/ontoanno-venv/bin/python -m pip install --upgrade pip setuptools wheel && \
    python3 -c 'import pathlib, tomllib; data = tomllib.loads(pathlib.Path("pyproject.toml").read_text()); deps = list(data["project"].get("dependencies", [])); deps.extend(data["project"].get("optional-dependencies", {}).get("ui", [])); pathlib.Path("/tmp/ontoanno-python-requirements.txt").write_text("\n".join(deps) + "\n")' && \
    /opt/ontoanno-venv/bin/python -m pip install -r /tmp/ontoanno-python-requirements.txt && \
    /opt/ontoanno-venv/bin/python -m pip install -r /app/GPTAnno/PDF2markers/requirements.txt

COPY src /app/src
COPY GPTAnno /app/GPTAnno
COPY resources /app/resources
COPY scripts /app/scripts
COPY configs /app/configs
COPY figures /app/figures
COPY ontoanno /app/ontoanno
COPY docker /app/docker
COPY README.md /app/README.md

RUN /opt/ontoanno-venv/bin/python -m pip install --no-deps --no-build-isolation -e .

RUN chmod +x /app/ontoanno /app/docker/entrypoint.sh

EXPOSE 8501

ENTRYPOINT ["/app/docker/entrypoint.sh"]
