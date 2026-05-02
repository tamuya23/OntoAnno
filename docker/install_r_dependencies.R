#!/usr/bin/env Rscript

args <- commandArgs(trailingOnly = TRUE)
package_file <- if (length(args) >= 1) args[[1]] else "/tmp/ontoanno-r-packages.txt"

packages <- readLines(package_file, warn = FALSE)
packages <- trimws(packages)
packages <- packages[nzchar(packages) & !startsWith(packages, "#")]
packages <- unique(packages)

message("R library paths:")
for (path in .libPaths()) {
  message("  - ", path)
}
message("R executable: ", R.home("bin"))
message("R version: ", R.version.string)
message("OTEL_SDK_DISABLED=", Sys.getenv("OTEL_SDK_DISABLED", unset = ""))

repos <- getOption("repos")
if (is.null(repos) || identical(unname(repos["CRAN"]), "@CRAN@")) {
  repos <- c(CRAN = Sys.getenv("CRAN_REPO", "https://cloud.r-project.org"))
}
options(repos = repos)

deps <- c("Depends", "Imports", "LinkingTo")
source_repo <- Sys.getenv("CRAN_SOURCE_REPO", "https://cloud.r-project.org")
special_repos <- list(
  presto = c(
    satijalab = Sys.getenv("PRESTO_R_UNIVERSE", "https://satijalab.r-universe.dev"),
    CRAN = source_repo
  )
)
special_packages <- intersect(packages, names(special_repos))
missing <- packages[!vapply(packages, requireNamespace, logical(1), quietly = TRUE)]

install_presto_from_github <- function() {
  message("Installing required presto from GitHub: immunogenomics/presto")
  if (requireNamespace("devtools", quietly = TRUE)) {
    return(
      try(
        devtools::install_github(
          "immunogenomics/presto",
          dependencies = deps,
          upgrade = "never",
          build_vignettes = FALSE
        ),
        silent = FALSE
      )
    )
  }

  if (!requireNamespace("remotes", quietly = TRUE)) {
    try(
      install.packages(
        "remotes",
        repos = c(CRAN = source_repo),
        dependencies = deps,
        type = "source",
        Ncpus = 2L
      ),
      silent = FALSE
    )
  }
  if (requireNamespace("remotes", quietly = TRUE)) {
    return(
      try(
        remotes::install_github(
          "immunogenomics/presto",
          dependencies = deps,
          upgrade = "never",
          build_vignettes = FALSE
        ),
        silent = FALSE
      )
    )
  }
}

binary_missing <- setdiff(missing, special_packages)
if (length(binary_missing)) {
  message("Installing missing R package(s) via the configured binary bridge: ", paste(binary_missing, collapse = ", "))
  options(
    bspm.sudo = TRUE,
    bspm.backend.check = FALSE,
    bspm.version.check = FALSE,
    install.packages.compile.from.source = "never"
  )
  if (requireNamespace("bspm", quietly = TRUE)) {
    try(bspm::enable(), silent = TRUE)
  }
  try(install.packages(binary_missing, dependencies = deps), silent = FALSE)
}

failed <- packages[!vapply(packages, requireNamespace, logical(1), quietly = TRUE)]
special_failed <- intersect(failed, special_packages)
if (length(special_failed)) {
  message(
    "Installing required non-CRAN R package(s) from configured upstream repositories: ",
    paste(special_failed, collapse = ", ")
  )
  if (requireNamespace("bspm", quietly = TRUE)) {
    try(bspm::disable(), silent = TRUE)
  }
  options(
    pkgType = "source",
    install.packages.compile.from.source = "always"
  )
  for (pkg in special_failed) {
    if (pkg == "presto") {
      install_presto_from_github()
    } else {
      try(
        install.packages(
          pkg,
          repos = special_repos[[pkg]],
          dependencies = deps,
          type = "source",
          Ncpus = 2L
        ),
        silent = FALSE
      )
    }
    if (!requireNamespace(pkg, quietly = TRUE)) {
      message("Retrying ", pkg, " from r-universe.")
      try(
        install.packages(
          pkg,
          repos = special_repos[[pkg]],
          dependencies = deps,
          type = "source",
          Ncpus = 2L
        ),
        silent = FALSE
      )
    }
  }
}

failed <- packages[!vapply(packages, requireNamespace, logical(1), quietly = TRUE)]
cran_failed <- setdiff(failed, special_packages)
if (length(cran_failed)) {
  message("Binary bridge did not provide all packages. Retrying from CRAN source: ", paste(cran_failed, collapse = ", "))
  if (requireNamespace("bspm", quietly = TRUE)) {
    try(bspm::disable(), silent = TRUE)
  }
  options(
    pkgType = "source",
    repos = c(CRAN = source_repo),
    install.packages.compile.from.source = "always"
  )
  for (pkg in cran_failed) {
    try(
      install.packages(
        pkg,
        dependencies = deps,
        type = "source",
        Ncpus = 2L
      ),
      silent = FALSE
    )
  }
}

failed <- packages[!vapply(packages, requireNamespace, logical(1), quietly = TRUE)]
if (length(failed)) {
  stop("Failed to install required R package(s): ", paste(failed, collapse = ", "))
}

versions <- vapply(packages, function(pkg) as.character(utils::packageVersion(pkg)), character(1))
message("All required R packages are available:")
for (pkg in packages) {
  message("  - ", pkg, " ", versions[[pkg]])
}
