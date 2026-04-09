args <- commandArgs(trailingOnly = TRUE)
if (length(args) != 1) {
  stop("Usage: Rscript export_reviewed_parent_annotations.R <spec.json>")
}

suppressPackageStartupMessages({
  library(jsonlite)
  library(Seurat)
})

spec <- jsonlite::fromJSON(args[[1]], simplifyVector = FALSE)

output_dir <- spec$output_dir
dir.create(output_dir, recursive = TRUE, showWarnings = FALSE)

message("[reviewed_parent] Loading parent Seurat object")
seurat_obj <- readRDS(spec$parent_seurat_rds)
decisions <- jsonlite::fromJSON(spec$decisions_json)

if (!spec$cluster_col %in% colnames(seurat_obj@meta.data)) {
  stop("Cluster column not found in Seurat metadata: ", spec$cluster_col)
}

meta <- seurat_obj@meta.data
cluster_ids <- as.character(meta[[spec$cluster_col]])

decision_df <- as.data.frame(decisions, stringsAsFactors = FALSE)
decision_df$cluster_id <- as.character(decision_df$cluster_id)
decision_df$final_label <- as.character(decision_df$final_label)
decision_df$selection_source <- as.character(decision_df$selection_source)
decision_df$user_note <- as.character(decision_df$user_note)

for (col_name in names(decision_df)) {
  column <- decision_df[[col_name]]
  if (is.list(column)) {
    decision_df[[col_name]] <- vapply(
      column,
      function(value) {
        if (is.null(value)) {
          return("")
        }
        paste(as.character(value), collapse = " | ")
      },
      character(1)
    )
  }
}

label_map <- setNames(decision_df$final_label, decision_df$cluster_id)
source_map <- setNames(decision_df$selection_source, decision_df$cluster_id)
note_map <- setNames(decision_df$user_note, decision_df$cluster_id)

meta$celltype_parent_reviewed <- unname(label_map[cluster_ids])
meta$celltype_parent_review_source <- unname(source_map[cluster_ids])
meta$celltype_parent_review_note <- unname(note_map[cluster_ids])
seurat_obj@meta.data <- meta

metadata_df <- cbind(
  cell_barcode = rownames(meta),
  meta,
  stringsAsFactors = FALSE
)

metadata_csv <- file.path(output_dir, "metadata_parent_reviewed.csv")
cluster_csv <- file.path(output_dir, "cluster_decisions.csv")
seurat_rds <- file.path(output_dir, "seurat_parent_reviewed.rds")
outputs_json <- spec$outputs_json

message("[reviewed_parent] Writing reviewed metadata CSV")
utils::write.csv(metadata_df, metadata_csv, row.names = FALSE)
message("[reviewed_parent] Writing cluster decision CSV")
utils::write.csv(decision_df, cluster_csv, row.names = FALSE)
message("[reviewed_parent] Saving reviewed Seurat object")
saveRDS(seurat_obj, seurat_rds, compress = "gzip")

outputs <- list(
  output_dir = normalizePath(output_dir, winslash = "/", mustWork = FALSE),
  metadata_csv = normalizePath(metadata_csv, winslash = "/", mustWork = FALSE),
  cluster_decisions_csv = normalizePath(cluster_csv, winslash = "/", mustWork = FALSE),
  seurat_rds = normalizePath(seurat_rds, winslash = "/", mustWork = FALSE),
  cluster_col = spec$cluster_col,
  label_col = "celltype_parent_reviewed",
  source_col = "celltype_parent_review_source",
  note_col = "celltype_parent_review_note",
  decision_count = nrow(decision_df)
)

jsonlite::write_json(outputs, outputs_json, auto_unbox = TRUE, pretty = TRUE, null = "null")
message("[reviewed_parent] Export complete")
