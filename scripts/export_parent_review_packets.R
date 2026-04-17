args <- commandArgs(trailingOnly = TRUE)
if (length(args) != 1) {
  stop("Usage: Rscript export_parent_review_packets.R <spec.json>")
}

suppressPackageStartupMessages({
  library(jsonlite)
  library(Seurat)
})

spec <- jsonlite::fromJSON(args[[1]], simplifyVector = FALSE)

output_dir <- spec$output_dir
packets_dir <- file.path(output_dir, "packets")
index_json <- file.path(output_dir, "index.json")
dir.create(output_dir, recursive = TRUE, showWarnings = FALSE)
dir.create(packets_dir, recursive = TRUE, showWarnings = FALSE)
unlink(file.path(packets_dir, "*.json"))

read_cluster_counts <- function(spec_inputs, cluster_col) {
  empty_counts <- data.frame(cluster = character(0), cell_count = integer(0), stringsAsFactors = FALSE)

  parent_metadata_csv <- spec_inputs$parent_metadata_csv
  if (!is.null(parent_metadata_csv) && file.exists(parent_metadata_csv)) {
    metadata_df <- utils::read.csv(parent_metadata_csv, stringsAsFactors = FALSE, check.names = FALSE)
    if (cluster_col %in% colnames(metadata_df)) {
      cluster_values <- as.character(metadata_df[[cluster_col]])
      cluster_values <- cluster_values[!is.na(cluster_values) & nzchar(cluster_values)]
      if (length(cluster_values) > 0) {
        counts <- as.data.frame(table(cluster_values), stringsAsFactors = FALSE)
        colnames(counts) <- c("cluster", "cell_count")
        return(counts)
      }
    }
  }

  manual_labels_csv <- spec_inputs$manual_labels_csv
  if (!is.null(manual_labels_csv) && file.exists(manual_labels_csv)) {
    manual_df <- utils::read.csv(manual_labels_csv, stringsAsFactors = FALSE, check.names = FALSE)
    if (cluster_col %in% colnames(manual_df)) {
      cluster_values <- as.character(manual_df[[cluster_col]])
      cluster_values <- cluster_values[!is.na(cluster_values) & nzchar(cluster_values)]
      if (length(cluster_values) > 0) {
        counts <- as.data.frame(table(cluster_values), stringsAsFactors = FALSE)
        colnames(counts) <- c("cluster", "cell_count")
        return(counts)
      }
    }
  }

  parent_seurat_rds <- spec_inputs$parent_seurat_rds
  if (is.null(parent_seurat_rds) || !nzchar(parent_seurat_rds) || !file.exists(parent_seurat_rds)) {
    return(empty_counts)
  }

  seurat_obj <- readRDS(parent_seurat_rds)
  counts <- as.data.frame(table(as.character(seurat_obj@meta.data[[cluster_col]])), stringsAsFactors = FALSE)
  colnames(counts) <- c("cluster", "cell_count")
  counts
}

safe_normalize <- function(path) {
  if (is.null(path) || !nzchar(path)) return(NULL)
  normalizePath(path, winslash = "/", mustWork = FALSE)
}

annotation_parent <- readRDS(spec$inputs$annotation_parent_rds)
best_resolution <- spec$annotation$best_resolution
cluster_col <- spec$annotation$cluster_col
policy <- spec$policy

score_csv <- spec$inputs$annotation_scores_csv
if (!is.null(score_csv) && nzchar(score_csv) && file.exists(score_csv)) {
  score_table <- utils::read.csv(score_csv, stringsAsFactors = FALSE)
} else {
  score_table <- data.frame(
    resolution = best_resolution,
    sum_path_length = NA_real_,
    avg_max_percentage = NA_real_,
    min_max_percentage = NA_real_,
    composite_score = NA_real_,
    stringsAsFactors = FALSE
  )
}

if (is.null(annotation_parent[[best_resolution]])) {
  stop("Best resolution not found in annotation_parent: ", best_resolution)
}

final_summary <- annotation_parent[[best_resolution]]$final_summary
markers_best <- readRDS(file.path(spec$inputs$markers_dir, paste0("markers_", best_resolution, ".rds")))

cluster_counts <- read_cluster_counts(spec$inputs, cluster_col)
final_summary$cluster <- as.character(final_summary$cluster)
cluster_counts$cluster <- as.character(cluster_counts$cluster)
packet_table <- merge(final_summary, cluster_counts, by = "cluster", all.x = TRUE, sort = FALSE)

marker_files <- list.files(spec$inputs$markers_dir, pattern = "^markers_res_.*\\.rds$", full.names = TRUE)
marker_file_map <- list()
for (path in marker_files) {
  token <- sub("^markers_", "", basename(path))
  token <- sub("\\.rds$", "", token)
  marker_file_map[[token]] <- normalizePath(path, winslash = "/", mustWork = FALSE)
}

prediction_dir <- spec$inputs$prediction_dir
prediction_files <- if (!is.null(prediction_dir) && nzchar(prediction_dir) && dir.exists(prediction_dir)) {
  list.files(prediction_dir, pattern = "^res_.*\\.pdf$", full.names = TRUE)
} else {
  character(0)
}
prediction_map <- list()
for (path in prediction_files) {
  token <- sub("\\.pdf$", "", basename(path))
  prediction_map[[token]] <- normalizePath(path, winslash = "/", mustWork = FALSE)
}

resolution_scores <- lapply(seq_len(nrow(score_table)), function(i) {
  row <- score_table[i, ]
  list(
    resolution = as.character(row$resolution),
    avg_path_length = as.numeric(row$avg_path_length),
    avg_max_percentage = as.numeric(row$avg_max_percentage),
    min_max_percentage = as.numeric(row$min_max_percentage),
    composite_score = as.numeric(row$composite_score)
  )
})

shared_context <- list(
  tissue_name = spec$annotation$tissue_name,
  best_parent_resolution = best_resolution,
  cluster_col = cluster_col,
  policy = policy,
  files = list(
    annotation_parent_rds = safe_normalize(spec$inputs$annotation_parent_rds),
    parent_seurat_rds = safe_normalize(spec$inputs$parent_seurat_rds),
    parent_metadata_csv = safe_normalize(spec$inputs$parent_metadata_csv),
    markers_best_resolution = normalizePath(
      file.path(spec$inputs$markers_dir, paste0("markers_", best_resolution, ".rds")),
      winslash = "/",
      mustWork = FALSE
    ),
    marker_files = marker_file_map,
    prediction_pdfs = prediction_map
  ),
  resolution_scores = resolution_scores
)

index_packets <- list()
summary_rows <- list()

for (i in seq_len(nrow(packet_table))) {
  row <- packet_table[i, ]
  cluster_id <- as.character(row$cluster)
  assigned_label <- as.character(row$most_frequent_annotation)
  other_annotations <- ifelse(is.na(row$other_annotations), "", as.character(row$other_annotations))
  max_percentage <- ifelse(is.na(row$max_percentage), NA_real_, as.numeric(row$max_percentage))
  avg_distance <- ifelse(is.na(row$avg_distance), NA_real_, as.numeric(row$avg_distance))
  cell_count <- ifelse(is.na(row$cell_count), 0L, as.integer(row$cell_count))

  cluster_markers <- markers_best[as.character(markers_best$cluster) == cluster_id, ]
  if (nrow(cluster_markers) > 0) {
    order_idx <- order(cluster_markers$p_val_adj, -cluster_markers$avg_log2FC)
    cluster_markers <- cluster_markers[order_idx, , drop = FALSE]
    cluster_markers <- head(cluster_markers, 10)
  }

  top_markers <- lapply(seq_len(nrow(cluster_markers)), function(j) {
    mr <- cluster_markers[j, ]
    list(
      gene = as.character(mr$gene),
      avg_log2FC = as.numeric(mr$avg_log2FC),
      pct_1 = as.numeric(mr$pct.1),
      pct_2 = as.numeric(mr$pct.2),
      p_val_adj = as.numeric(mr$p_val_adj)
    )
  })

  notes <- character(0)
  if (isTRUE(policy$review_tie) && nzchar(trimws(other_annotations))) {
    notes <- c(notes, "multiple annotation candidates across runs")
  }
  if (
    isTRUE(policy$review_nomatch) &&
    (is.na(assigned_label) || assigned_label == "" || tolower(assigned_label) %in% c("unknown", "unclassified", "unassigned"))
  ) {
    notes <- c(notes, "no confident ontology label")
  }

  packet <- list(
    packet_version = "0.2",
    level = "parent",
    generated_at = format(Sys.time(), "%Y-%m-%dT%H:%M:%SZ", tz = "UTC"),
    project_name = spec$project_name,
    run_id = spec$run_id,
    index_json = normalizePath(index_json, winslash = "/", mustWork = FALSE),
    cluster_id = cluster_id,
    summary = list(
      assigned_label = assigned_label,
      cell_count = cell_count,
      max_percentage = max_percentage,
      other_annotations = other_annotations,
      avg_distance = avg_distance
    ),
    markers = top_markers,
    review_flags = list(
      review_tie = isTRUE(policy$review_tie),
      review_nomatch = isTRUE(policy$review_nomatch),
      needs_review = length(notes) > 0,
      notes = as.list(notes)
    )
  )

  packet_path <- file.path(packets_dir, paste0("cluster-", cluster_id, ".json"))
  jsonlite::write_json(packet, packet_path, auto_unbox = TRUE, pretty = TRUE, null = "null")

  index_packets[[length(index_packets) + 1]] <- list(
    celltype = paste0("cluster ", cluster_id, " - ", assigned_label),
    cluster_id = cluster_id,
    label = assigned_label,
    needs_review = length(notes) > 0,
    packet_json = normalizePath(packet_path, winslash = "/", mustWork = FALSE),
    packet_uri = paste0("file://", normalizePath(packet_path, winslash = "/", mustWork = FALSE))
  )

  summary_rows[[length(summary_rows) + 1]] <- data.frame(
    cluster_id = cluster_id,
    assigned_label = assigned_label,
    cell_count = cell_count,
    max_percentage = max_percentage,
    other_annotations = other_annotations,
    avg_distance = avg_distance,
    needs_review = tolower(as.character(length(notes) > 0)),
    packet_json = normalizePath(packet_path, winslash = "/", mustWork = FALSE),
    stringsAsFactors = FALSE
  )
}

summary_df <- do.call(rbind, summary_rows)
summary_csv <- file.path(output_dir, "summary.csv")
utils::write.csv(summary_df, summary_csv, row.names = FALSE)

index_payload <- list(
  packet_version = "0.2",
  level = "parent",
  generated_at = format(Sys.time(), "%Y-%m-%dT%H:%M:%SZ", tz = "UTC"),
  project_name = spec$project_name,
  run_id = spec$run_id,
  shared = shared_context,
  summary_csv = normalizePath(summary_csv, winslash = "/", mustWork = FALSE),
  packets = index_packets
)
jsonlite::write_json(index_payload, index_json, auto_unbox = TRUE, pretty = TRUE, null = "null")

outputs <- list(
  level = "parent",
  output_dir = normalizePath(output_dir, winslash = "/", mustWork = FALSE),
  packets_dir = normalizePath(packets_dir, winslash = "/", mustWork = FALSE),
  index_json = normalizePath(index_json, winslash = "/", mustWork = FALSE),
  summary_csv = normalizePath(summary_csv, winslash = "/", mustWork = FALSE),
  packet_count = length(index_packets),
  packets = vapply(index_packets, function(x) x$packet_json, character(1))
)
jsonlite::write_json(outputs, spec$outputs_json, auto_unbox = TRUE, pretty = TRUE, null = "null")
