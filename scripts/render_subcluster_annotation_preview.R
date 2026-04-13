args <- commandArgs(trailingOnly = TRUE)
if (length(args) != 1) {
  stop("Usage: Rscript render_subcluster_annotation_preview.R <spec.json>")
}

if (!requireNamespace("jsonlite", quietly = TRUE)) {
  stop("Package 'jsonlite' is required.")
}
if (!requireNamespace("pkgload", quietly = TRUE)) {
  stop("Package 'pkgload' is required to load the vendored GPTAnno package.")
}
if (!requireNamespace("Seurat", quietly = TRUE)) {
  stop("Package 'Seurat' is required.")
}
if (!requireNamespace("ggplot2", quietly = TRUE)) {
  stop("Package 'ggplot2' is required.")
}

spec_path <- normalizePath(args[[1]], mustWork = TRUE)
spec <- jsonlite::fromJSON(spec_path, simplifyVector = FALSE)
pkgload::load_all(spec$gptanno_path, export_all = FALSE, helpers = FALSE, quiet = TRUE)

seurat_rds <- normalizePath(spec$seurat_rds, mustWork = TRUE)
summary_rds <- normalizePath(spec$summary_rds, mustWork = TRUE)
resolution <- as.character(spec$resolution)
output_png <- spec$output_png
output_csv <- spec$output_csv

dir.create(dirname(output_png), recursive = TRUE, showWarnings = FALSE)
dir.create(dirname(output_csv), recursive = TRUE, showWarnings = FALSE)

seurat_obj <- readRDS(seurat_rds)
annotation_summary <- readRDS(summary_rds)

final_summary <- annotation_summary$final_summary
if (is.null(final_summary) || !"cluster" %in% colnames(final_summary)) {
  stop("annotation_summary$final_summary is missing or malformed")
}

utils::write.csv(final_summary, output_csv, row.names = FALSE)

cluster_candidates <- c(
  paste0("subcluster_res.", resolution),
  paste0("cluster_res.", resolution)
)
cluster_col <- cluster_candidates[cluster_candidates %in% colnames(seurat_obj@meta.data)][1]
if (is.na(cluster_col) || !nzchar(cluster_col)) {
  stop(
    "Cluster column not found in Seurat metadata. Tried: ",
    paste(cluster_candidates, collapse = ", ")
  )
}

label_col <- paste0("ui_subcluster_annotation_", gsub("[^A-Za-z0-9]+", "_", resolution))
cluster_annotations <- stats::setNames(
  as.character(final_summary$most_frequent_annotation),
  as.character(final_summary$cluster)
)
cluster_ids <- as.character(seurat_obj@meta.data[[cluster_col]])
assigned <- unname(cluster_annotations[cluster_ids])
assigned[is.na(assigned) | assigned == ""] <- "unannotated"
seurat_obj@meta.data[[label_col]] <- assigned

plot_obj <- plot_celltype_comparison(
  seurat_obj,
  original_col = cluster_col,
  annotation_col = label_col,
  label = TRUE,
  pt.size = 0.3
)

ggplot2::ggsave(
  filename = output_png,
  plot = plot_obj,
  width = 18,
  height = 8,
  units = "in",
  dpi = 180
)

message("[subcluster_annotation_preview] Saved preview to: ", output_png)
message("[subcluster_annotation_preview] Saved summary to: ", output_csv)
