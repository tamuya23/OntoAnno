args <- commandArgs(trailingOnly = TRUE)
if (length(args) != 1) {
  stop("Usage: Rscript render_parent_annotation_preview.R <spec.json>")
}

spec_path <- normalizePath(args[[1]], mustWork = TRUE)

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

spec <- jsonlite::fromJSON(spec_path, simplifyVector = FALSE)
pkgload::load_all(spec$gptanno_path, export_all = FALSE, helpers = FALSE, quiet = TRUE)

seurat_rds <- normalizePath(spec$seurat_rds, mustWork = TRUE)
mapping_csv <- normalizePath(spec$mapping_csv, mustWork = TRUE)
resolution <- as.character(spec$resolution)
output_png <- spec$output_png

dir.create(dirname(output_png), recursive = TRUE, showWarnings = FALSE)

seurat_obj <- readRDS(seurat_rds)
mapping <- utils::read.csv(mapping_csv, stringsAsFactors = FALSE)

resolution_rows <- mapping[mapping$resolution == resolution, , drop = FALSE]
if (nrow(resolution_rows) == 0) {
  stop("No mapping rows found for resolution: ", resolution)
}

selected_rows <- resolution_rows[resolution_rows$role == "selected", , drop = FALSE]
if (nrow(selected_rows) == 0) {
  candidate_rows <- resolution_rows[resolution_rows$role == "candidate", , drop = FALSE]
  if (nrow(candidate_rows) == 0) {
    stop("No selected or candidate mapping rows found for resolution: ", resolution)
  }
  candidate_rows$percentage_num <- suppressWarnings(as.numeric(candidate_rows$percentage))
  candidate_rows$percentage_num[is.na(candidate_rows$percentage_num)] <- -Inf
  ord <- order(candidate_rows$cluster, -candidate_rows$percentage_num)
  candidate_rows <- candidate_rows[ord, , drop = FALSE]
  selected_rows <- candidate_rows[!duplicated(candidate_rows$cluster), , drop = FALSE]
}

resolution_value <- sub("^res_", "", resolution)
cluster_col <- paste0("cluster_res.", resolution_value)
if (!cluster_col %in% colnames(seurat_obj@meta.data)) {
  stop("Cluster column not found in Seurat metadata: ", cluster_col)
}

label_col <- paste0("ui_parent_annotation_", gsub("[^A-Za-z0-9]+", "_", resolution))
labels <- selected_rows$cleaned_label
labels[is.na(labels) | labels == ""] <- selected_rows$label[is.na(labels) | labels == ""]
label_map <- stats::setNames(as.character(labels), as.character(selected_rows$cluster))

cluster_ids <- as.character(seurat_obj@meta.data[[cluster_col]])
assigned <- unname(label_map[cluster_ids])
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

message("[parent_annotation_preview] Saved preview to: ", output_png)
