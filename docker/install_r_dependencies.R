#!/usr/bin/env Rscript

args <- commandArgs(trailingOnly = TRUE)
package_file <- if (length(args) >= 1) args[[1]] else "/tmp/ontoanno-r-packages.txt"

packages <- readLines(package_file, warn = FALSE)
packages <- trimws(packages)
packages <- packages[nzchar(packages) & !startsWith(packages, "#")]
packages <- unique(packages)

repos <- getOption("repos")
if (is.null(repos) || identical(unname(repos["CRAN"]), "@CRAN@")) {
  repos <- c(CRAN = Sys.getenv("CRAN_REPO", "https://cloud.r-project.org"))
}
options(repos = repos)

deps <- c("Depends", "Imports", "LinkingTo")
missing <- packages[!vapply(packages, requireNamespace, logical(1), quietly = TRUE)]

if (length(missing)) {
  message("Installing missing R package(s): ", paste(missing, collapse = ", "))
  install.packages(missing, dependencies = deps)
}

failed <- packages[!vapply(packages, requireNamespace, logical(1), quietly = TRUE)]
if (length(failed)) {
  message("Retrying failed R package(s) one by one: ", paste(failed, collapse = ", "))
  for (pkg in failed) {
    try(install.packages(pkg, dependencies = deps), silent = FALSE)
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
