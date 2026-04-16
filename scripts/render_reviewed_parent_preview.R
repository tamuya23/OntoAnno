args <- commandArgs(trailingOnly = TRUE)
if (length(args) != 1) {
  stop("Usage: Rscript render_reviewed_parent_preview.R <spec.json>")
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
cluster_col <- as.character(spec$cluster_col)
label_col <- as.character(spec$label_col)
output_png <- spec$output_png

dir.create(dirname(output_png), recursive = TRUE, showWarnings = FALSE)

seurat_obj <- readRDS(seurat_rds)
if (!cluster_col %in% colnames(seurat_obj@meta.data)) {
  stop("Cluster column not found in Seurat metadata: ", cluster_col)
}
if (!label_col %in% colnames(seurat_obj@meta.data)) {
  stop("Reviewed label column not found in Seurat metadata: ", label_col)
}

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

message("[reviewed_parent_preview] Saved preview to: ", output_png)
