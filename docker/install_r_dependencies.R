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

repos <- getOption("repos")
if (is.null(repos) || identical(unname(repos["CRAN"]), "@CRAN@")) {
  repos <- c(CRAN = Sys.getenv("CRAN_REPO", "https://cloud.r-project.org"))
}
options(repos = repos)

deps <- c("Depends", "Imports", "LinkingTo")
missing <- packages[!vapply(packages, requireNamespace, logical(1), quietly = TRUE)]

if (length(missing)) {
  message("Installing missing R package(s) via the configured binary bridge: ", paste(missing, collapse = ", "))
  options(
    bspm.backend.check = FALSE,
    bspm.version.check = FALSE,
    install.packages.compile.from.source = "never"
  )
  if (requireNamespace("bspm", quietly = TRUE)) {
    try(bspm::enable(), silent = TRUE)
  }
  try(install.packages(missing, dependencies = deps), silent = FALSE)
}

failed <- packages[!vapply(packages, requireNamespace, logical(1), quietly = TRUE)]
if (length(failed)) {
  message("Binary bridge did not provide all packages. Retrying from CRAN source: ", paste(failed, collapse = ", "))
  if (requireNamespace("bspm", quietly = TRUE)) {
    try(bspm::disable(), silent = TRUE)
  }
  source_repo <- Sys.getenv("CRAN_SOURCE_REPO", "https://cloud.r-project.org")
  options(
    pkgType = "source",
    repos = c(CRAN = source_repo),
    install.packages.compile.from.source = "always"
  )
  for (pkg in failed) {
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
