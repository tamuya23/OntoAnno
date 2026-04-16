args <- commandArgs(trailingOnly = TRUE)
if (length(args) != 1) {
  stop("Usage: Rscript run_evaluation.R <spec.json>")
}

spec_path <- normalizePath(args[[1]], mustWork = TRUE)

if (!requireNamespace("jsonlite", quietly = TRUE)) {
  stop("Package 'jsonlite' is required.")
}
if (!requireNamespace("pkgload", quietly = TRUE)) {
  stop("Package 'pkgload' is required to load the vendored GPTAnno package.")
}
if (!requireNamespace("ontologyIndex", quietly = TRUE)) {
  stop("Package 'ontologyIndex' is required.")
}
if (!requireNamespace("magrittr", quietly = TRUE)) {
  stop("Package 'magrittr' is required.")
}
if (!requireNamespace("Seurat", quietly = TRUE)) {
  stop("Package 'Seurat' is required.")
}
if (!requireNamespace("dplyr", quietly = TRUE)) {
  stop("Package 'dplyr' is required.")
}

spec <- jsonlite::fromJSON(spec_path, simplifyVector = FALSE)
pkgload::load_all(spec$gptanno_path, export_all = FALSE, helpers = FALSE, quiet = TRUE)

`%||%` <- function(x, y) if (is.null(x)) y else x

ensure_parent_dir <- function(path) {
  dir.create(dirname(path), recursive = TRUE, showWarnings = FALSE)
}

detect_join_key <- function(df, target_ids) {
  candidates <- c("_index", "cell_id", "cell", "barcode")
  for (candidate in candidates) {
    if (candidate %in% colnames(df)) {
      values <- as.character(df[[candidate]])
      if (sum(values %in% target_ids) > 0) {
        return(candidate)
      }
    }
  }
  NULL
}

attach_manual_labels <- function(seurat_obj, csv_path, manual_col) {
  if (is.null(csv_path) || !file.exists(csv_path)) {
    return(seurat_obj)
  }

  df <- utils::read.csv(csv_path, stringsAsFactors = FALSE, check.names = FALSE)
  if (!manual_col %in% colnames(df)) {
    stop("manual_col '", manual_col, "' not found in manual labels CSV")
  }

  target_ids <- rownames(seurat_obj@meta.data)
  join_key <- detect_join_key(df, target_ids)

  if (!is.null(join_key)) {
    meta <- seurat_obj@meta.data
    meta$cell_id_tmp_ontoanno <- rownames(meta)
    df[[join_key]] <- as.character(df[[join_key]])
    meta <- dplyr::left_join(
      meta,
      df[, c(join_key, manual_col), drop = FALSE],
      by = stats::setNames(join_key, "cell_id_tmp_ontoanno")
    )
    rownames(meta) <- meta$cell_id_tmp_ontoanno
    meta$cell_id_tmp_ontoanno <- NULL
    seurat_obj@meta.data <- meta
  } else if (nrow(df) == nrow(seurat_obj@meta.data)) {
    seurat_obj@meta.data[[manual_col]] <- df[[manual_col]]
  } else {
    stop("Unable to align manual labels CSV to Seurat object")
  }

  seurat_obj
}

attach_baseline <- function(seurat_obj, baseline_result) {
  metadata_csv <- baseline_result$metadata_csv %||% NULL
  prediction_column <- baseline_result$prediction_column %||% NULL
  rename_to <- baseline_result$rename_to %||% prediction_column

  if (is.null(metadata_csv) || is.null(prediction_column) || !file.exists(metadata_csv)) {
    return(seurat_obj)
  }

  df <- utils::read.csv(metadata_csv, stringsAsFactors = FALSE, check.names = FALSE)
  if (!prediction_column %in% colnames(df)) {
    warning("Skipping baseline merge for ", baseline_result$name, ": prediction column missing")
    return(seurat_obj)
  }

  target_ids <- rownames(seurat_obj@meta.data)
  join_key <- baseline_result$join_key %||% detect_join_key(df, target_ids)

  if (!is.null(join_key) && join_key %in% colnames(df)) {
    meta <- seurat_obj@meta.data
    meta$cell_id_tmp_ontoanno <- rownames(meta)
    df[[join_key]] <- as.character(df[[join_key]])
    merged <- dplyr::left_join(
      meta,
      df[, c(join_key, prediction_column), drop = FALSE],
      by = stats::setNames(join_key, "cell_id_tmp_ontoanno")
    )
    rownames(merged) <- merged$cell_id_tmp_ontoanno
    merged$cell_id_tmp_ontoanno <- NULL
    names(merged)[names(merged) == prediction_column] <- rename_to
    seurat_obj@meta.data <- merged
  } else if (nrow(df) == nrow(seurat_obj@meta.data)) {
    seurat_obj@meta.data[[rename_to]] <- df[[prediction_column]]
  } else {
    warning("Skipping baseline merge for ", baseline_result$name, ": unable to align metadata CSV")
  }

  seurat_obj
}

work_dir <- normalizePath(spec$work_dir, mustWork = FALSE)
evaluation_dir <- file.path(work_dir, "evaluation")
dir.create(evaluation_dir, recursive = TRUE, showWarnings = FALSE)

score_result_rds <- file.path(evaluation_dir, "score_result.rds")
ontology_comparison_csv <- file.path(evaluation_dir, "ontology_comparison.csv")
annotation_summary_csv <- file.path(evaluation_dir, "annotation_summary.csv")

cl <- ontologyIndex::get_ontology("http://purl.obolibrary.org/obo/cl.obo", extract_tags = "everything")
graph <- build_ontology_graph(cl)
ancestor_type_map <- build_ancestor_type_map(cl)

seurat_obj <- readRDS(spec$final_seurat_rds)
manual_col <- spec$evaluation$manual_col
seurat_obj <- attach_manual_labels(seurat_obj, spec$inputs$manual_labels_csv %||% NULL, manual_col)

if (!manual_col %in% colnames(seurat_obj@meta.data)) {
  stop("Manual label column '", manual_col, "' not available in evaluation object")
}

baseline_results <- spec$baseline_results %||% list()
if (length(baseline_results) > 0) {
  for (baseline_result in baseline_results) {
    seurat_obj <- attach_baseline(seurat_obj, baseline_result)
  }
}

predicted_columns <- c("celltype_parent", "celltype_final", "celltype_final_inherited")
for (baseline_result in baseline_results) {
  rename_to <- baseline_result$rename_to %||% baseline_result$prediction_column
  if (!is.null(rename_to)) {
    predicted_columns <- c(predicted_columns, rename_to)
  }
}
predicted_columns <- unique(predicted_columns[predicted_columns %in% colnames(seurat_obj@meta.data)])

annotation_summary <- seurat_obj@meta.data[, unique(c(manual_col, predicted_columns)), drop = FALSE]
annotation_summary$cell_id <- rownames(annotation_summary)

score_result <- list()
summary_rows <- list()

for (predicted_col in predicted_columns) {
  result <- score_annotation_agreement_ontology_detailed(
    seurat_obj = seurat_obj,
    manual_col = manual_col,
    predicted_col = predicted_col,
    cl_term_map = GPTAnno::cl_term_map,
    cl_ontology = cl,
    graph = graph,
    ancestor_type_map = ancestor_type_map,
    scoring_weights = c("exact" = 1.0, "parent" = 0.5, "child" = 1.0, "sibling" = 0.5, "no_match" = 0.0)
  )
  result$summary$method <- predicted_col
  result$summary$mean_distance <- mean(result$scores$ontology_distance, na.rm = TRUE)
  score_result[[predicted_col]] <- result
  summary_rows[[predicted_col]] <- result$summary
}

ontology_comparison <- do.call(rbind, lapply(summary_rows, function(x) as.data.frame(x)))
ontology_comparison$method <- rownames(ontology_comparison)
rownames(ontology_comparison) <- NULL

saveRDS(score_result, score_result_rds)
utils::write.csv(ontology_comparison, ontology_comparison_csv, row.names = FALSE)
utils::write.csv(annotation_summary, annotation_summary_csv, row.names = FALSE)

outputs <- list(
  score_result_rds = score_result_rds,
  ontology_comparison_csv = ontology_comparison_csv,
  annotation_summary_csv = annotation_summary_csv,
  predicted_columns = predicted_columns
)

ensure_parent_dir(spec$outputs_json)
jsonlite::write_json(outputs, spec$outputs_json, auto_unbox = TRUE, pretty = TRUE)
