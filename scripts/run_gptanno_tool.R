args <- commandArgs(trailingOnly = TRUE)
if (length(args) != 1) {
  stop("Usage: Rscript run_gptanno_tool.R <spec.json>")
}

spec_path <- normalizePath(args[[1]], mustWork = TRUE)

if (!requireNamespace("jsonlite", quietly = TRUE)) {
  stop("Package 'jsonlite' is required.")
}
if (!requireNamespace("pkgload", quietly = TRUE)) {
  stop("Package 'pkgload' is required to load the vendored GPTAnno package.")
}
if (!requireNamespace("ellmer", quietly = TRUE)) {
  stop("Package 'ellmer' is required.")
}
if (!requireNamespace("magrittr", quietly = TRUE)) {
  stop("Package 'magrittr' is required.")
}
if (!requireNamespace("ontologyIndex", quietly = TRUE)) {
  stop("Package 'ontologyIndex' is required.")
}
if (!requireNamespace("Seurat", quietly = TRUE)) {
  stop("Package 'Seurat' is required.")
}

spec <- jsonlite::fromJSON(spec_path, simplifyVector = FALSE)

pkgload::load_all(spec$gptanno_path, export_all = FALSE, helpers = FALSE, quiet = TRUE)

`%||%` <- function(x, y) if (is.null(x)) y else x

build_llm_config <- function(llm_spec) {
  params_list <- llm_spec$params
  if (!is.null(params_list) &&
      identical(llm_spec$provider %||% "openai", "openai") &&
      grepl("^gpt-5", llm_spec$model %||% "gpt-5")) {
    params_list$temperature <- NULL
  }

  params_obj <- NULL
  if (!is.null(params_list) && length(params_list) > 0) {
    params_obj <- do.call(ellmer::params, params_list)
  }
  list(
    provider = llm_spec$provider %||% "openai",
    model = llm_spec$model %||% "gpt-5",
    params = params_obj,
    api_key = llm_spec$api_key %||% NULL,
    api_url = llm_spec$api_url %||% NULL,
    system_prompt = llm_spec$system_prompt %||% NULL
  )
}

load_mapping_dict <- function() {
  candidates <- c("GPTCelltype_mapping", "GPTCelltyp_mapping")
  ns <- asNamespace("GPTAnno")

  for (candidate in candidates) {
    if (exists(candidate, envir = ns, inherits = FALSE)) {
      return(get(candidate, envir = ns, inherits = FALSE))
    }
  }

  data_env <- new.env(parent = emptyenv())
  for (candidate in candidates) {
    suppressWarnings(
      try(utils::data(list = candidate, package = "GPTAnno", envir = data_env), silent = TRUE)
    )
    if (exists(candidate, envir = data_env, inherits = FALSE)) {
      return(get(candidate, envir = data_env, inherits = FALSE))
    }
  }

  stop("Could not load GPTAnno mapping data (tried GPTCelltype_mapping and GPTCelltyp_mapping).")
}

ensure_parent_dir <- function(path) {
  dir.create(dirname(path), recursive = TRUE, showWarnings = FALSE)
}

derive_best_resolution <- function(scores_csv) {
  table <- utils::read.csv(scores_csv, stringsAsFactors = FALSE, check.names = FALSE)
  if (!"resolution" %in% colnames(table) || nrow(table) == 0) {
    stop("Bootstrap annotation scores CSV is missing a resolution column or has no rows: ", scores_csv)
  }
  best_res_name <- table$resolution[[1]]
  list(
    best_resolution = best_res_name,
    best_resolution_value = sub("^res_", "", best_res_name),
    cluster_col = paste0("cluster_res.", sub("^res_", "", best_res_name))
  )
}

normalize_resolution_name <- function(value) {
  if (is.null(value) || is.na(value)) {
    return(NULL)
  }
  text <- trimws(as.character(value))
  if (!nzchar(text)) {
    return(NULL)
  }
  if (startsWith(text, "res_")) {
    return(text)
  }
  paste0("res_", text)
}

bootstrap_parent_outputs <- function(inputs) {
  bootstrap <- inputs$bootstrap_parent
  if (is.null(bootstrap)) {
    return(NULL)
  }

  required_paths <- c("annotation_parent_rds", "annotation_scores_csv", "parent_seurat_rds", "markers_dir")
  missing <- required_paths[!vapply(required_paths, function(key) {
    value <- bootstrap[[key]]
    !is.null(value) && nzchar(value) && file.exists(value)
  }, logical(1))]
  if (length(missing) > 0) {
    stop(
      "bootstrap_parent is configured but missing required existing path(s): ",
      paste(missing, collapse = ", ")
    )
  }

  derived <- derive_best_resolution(bootstrap$annotation_scores_csv)
  best_resolution <- bootstrap$best_resolution %||% derived$best_resolution
  best_resolution_value <- sub("^res_", "", best_resolution)
  cluster_col <- bootstrap$cluster_col %||% paste0("cluster_res.", best_resolution_value)

  prediction_dir <- bootstrap$prediction_dir %||% NULL
  if (!is.null(prediction_dir) && !file.exists(prediction_dir)) {
    prediction_dir <- NULL
  }

  list(
    preprocessed_rds = inputs$seurat_rds,
    clustered_rds = inputs$seurat_rds,
    markers_dir = bootstrap$markers_dir,
    annotation_parent_rds = bootstrap$annotation_parent_rds,
    annotation_scores_csv = bootstrap$annotation_scores_csv,
    prediction_dir = prediction_dir,
    parent_seurat_rds = bootstrap$parent_seurat_rds,
    best_resolution = best_resolution,
    best_resolution_value = best_resolution_value,
    cluster_col = cluster_col,
    bootstrapped = TRUE
  )
}

inputs <- spec$inputs
annotation_cfg <- spec$annotation
alignment_cfg <- spec$alignment

recreate_seurat_if_needed <- function(seurat_obj) {
  counts <- NULL
  counts <- tryCatch(
    Seurat::GetAssayData(seurat_obj, layer = "counts"),
    error = function(e) {
      tryCatch(
        Seurat::GetAssayData(seurat_obj, slot = "counts"),
        error = function(e2) NULL
      )
    }
  )
  if (is.null(counts)) {
    return(seurat_obj)
  }

  metadata <- seurat_obj@meta.data
  recreated <- Seurat::CreateSeuratObject(counts = counts, meta.data = metadata)
  return(recreated)
}

has_pca_reduction <- function(seurat_obj) {
  "pca" %in% names(seurat_obj@reductions)
}

build_annotation_context <- local({
  cache <- NULL

  function() {
    if (!is.null(cache)) {
      return(cache)
    }

    ontology_url <- "http://purl.obolibrary.org/obo/cl.obo"
    cl <- ontologyIndex::get_ontology(ontology_url, extract_tags = "everything")
    cache <<- list(
      cl = cl,
      graph = build_ontology_graph(cl),
      ancestor_type_map = build_ancestor_type_map(cl),
      llm_config = build_llm_config(spec$llm$annotation),
      mapping_dict = load_mapping_dict(),
      cl_term_map = GPTAnno::cl_term_map
    )
    cache
  }
})

work_dir <- normalizePath(spec$work_dir, mustWork = FALSE)
dir.create(work_dir, recursive = TRUE, showWarnings = FALSE)

annotation_dir <- file.path(work_dir, "annotate_parent")
subcluster_dir <- file.path(work_dir, "annotate_subclusters")
dir.create(annotation_dir, recursive = TRUE, showWarnings = FALSE)
dir.create(subcluster_dir, recursive = TRUE, showWarnings = FALSE)

preprocessed_rds <- file.path(annotation_dir, "seurat_preprocessed.rds")
clustered_rds <- file.path(annotation_dir, "seurat_clustered.rds")
markers_dir <- file.path(annotation_dir, "marker_genes")
prediction_dir <- file.path(annotation_dir, "prediction")
annotation_parent_rds <- file.path(annotation_dir, "annotation_parent.rds")
annotation_scores_csv <- file.path(annotation_dir, "annotation_summary_scores.csv")
parent_seurat_rds <- file.path(annotation_dir, "seurat_parent_annotated.rds")
parent_ontology_map_rds <- file.path(annotation_dir, "parent_ontology_mapping.rds")
parent_ontology_map_csv <- file.path(annotation_dir, "parent_ontology_mapping.csv")
best_parent_resolution_json <- file.path(annotation_dir, "best_parent_resolution.json")

subcluster_result_rds <- file.path(subcluster_dir, "subcluster_find_markers.rds")
final_ontology_rds <- file.path(subcluster_dir, "seurat_ontology_annotated.rds")
final_inherited_rds <- file.path(subcluster_dir, "seurat_final_annotated.rds")
final_metadata_csv <- file.path(subcluster_dir, "metadata_final.csv")
final_dimplot_pdf <- file.path(subcluster_dir, "DimPlot_celltype_final.pdf")
ontology_workflow_rds <- file.path(subcluster_dir, "ontology_workflow.rds")
inheritance_workflow_rds <- file.path(subcluster_dir, "marker_inheritance_workflow.rds")

external_marker_genes_dir <- inputs$marker_genes_dir %||% NULL

has_external_marker_genes <- function() {
  !is.null(external_marker_genes_dir) &&
    nzchar(external_marker_genes_dir) &&
    dir.exists(external_marker_genes_dir)
}

marker_file_for_resolution <- function(marker_dir, resolution) {
  file.path(marker_dir, paste0("markers_res_", as.character(resolution), ".rds"))
}

validate_marker_gene_inputs <- function(seurat_obj, marker_dir) {
  required_cluster_cols <- paste0("cluster_res.", unlist(annotation_cfg$parent_res))
  missing_cluster_cols <- setdiff(required_cluster_cols, colnames(seurat_obj@meta.data))
  if (length(missing_cluster_cols) > 0) {
    stop(
      "inputs.marker_genes_dir was provided, but the input Seurat object is missing required cluster metadata column(s): ",
      paste(missing_cluster_cols, collapse = ", "),
      ". Provide a Seurat object that already contains these cluster_res.* columns, or run clustering normally."
    )
  }

  required_marker_files <- vapply(
    unlist(annotation_cfg$parent_res),
    function(resolution) marker_file_for_resolution(marker_dir, resolution),
    character(1)
  )
  missing_marker_files <- required_marker_files[!file.exists(required_marker_files)]
  if (length(missing_marker_files) > 0) {
    stop(
      "inputs.marker_genes_dir is missing marker file(s) for configured parent resolutions: ",
      paste(missing_marker_files, collapse = ", ")
    )
  }
}

copy_external_marker_genes <- function(source_dir, target_dir) {
  source_norm <- normalizePath(source_dir, mustWork = TRUE)
  target_norm <- normalizePath(target_dir, mustWork = FALSE)
  if (identical(source_norm, target_norm)) {
    return(target_dir)
  }

  if (dir.exists(target_dir)) {
    unlink(target_dir, recursive = TRUE, force = TRUE)
  }
  dir.create(target_dir, recursive = TRUE, showWarnings = FALSE)

  files <- list.files(source_norm, recursive = TRUE, full.names = TRUE, all.files = FALSE, no.. = TRUE)
  for (file in files) {
    relative <- substring(file, nchar(source_norm) + 2L)
    destination <- file.path(target_dir, relative)
    dir.create(dirname(destination), recursive = TRUE, showWarnings = FALSE)
    file.copy(file, destination, overwrite = TRUE)
  }
  target_dir
}

load_parent_resolution_info <- function() {
  if (file.exists(best_parent_resolution_json)) {
    payload <- jsonlite::fromJSON(best_parent_resolution_json)
    return(as.list(payload))
  }
  if (file.exists(annotation_scores_csv)) {
    return(derive_best_resolution(annotation_scores_csv))
  }
  bootstrap <- bootstrap_parent_outputs(inputs)
  if (!is.null(bootstrap)) {
    return(list(
      best_resolution = bootstrap$best_resolution,
      best_resolution_value = bootstrap$best_resolution_value,
      cluster_col = bootstrap$cluster_col
    ))
  }
  stop("Best parent resolution information not found.")
}

extract_other_annotation_terms <- function(text) {
  if (is.null(text) || is.na(text) || !nzchar(text)) {
    return(character(0))
  }
  parts <- unlist(strsplit(text, ","))
  parts <- trimws(parts)
  parts <- gsub("\\s*[0-9.]+\\s*%\\s*$", "", parts)
  parts <- trimws(parts)
  parts[nzchar(parts)]
}

safe_column_value <- function(df, column, index, default = NA) {
  if (is.null(df[[column]]) || length(df[[column]]) < index) {
    return(default)
  }
  value <- df[[column]][[index]]
  if (is.null(value)) {
    return(default)
  }
  value
}

build_parent_ontology_mapping_df <- function(annotation_parent, cl_term_map) {
  rows <- list()
  idx <- 1L

  for (res_name in names(annotation_parent)) {
    res_obj <- annotation_parent[[res_name]]
    summary_tbl <- res_obj$summary
    final_tbl <- res_obj$final_summary

    if (!is.null(summary_tbl) && nrow(summary_tbl) > 0) {
      cleaned <- vapply(summary_tbl$annotation_split, clean_and_match_annotation, character(1))
      mapped <- map_celltypes_to_cl(cleaned, cl_term_map = cl_term_map, verbose = FALSE)
      rows[[idx]] <- data.frame(
        resolution = res_name,
        cluster = as.character(summary_tbl$cluster),
        role = "candidate",
        label = as.character(summary_tbl$annotation_split),
        cleaned_label = cleaned,
        clid = mapped$clid,
        cl_label = mapped$cl_label,
        percentage = as.numeric(summary_tbl$percentage),
        max_percentage = NA_real_,
        other_annotations = NA_character_,
        avg_distance = NA_real_,
        stringsAsFactors = FALSE
      )
      idx <- idx + 1L
    }

    if (!is.null(final_tbl) && nrow(final_tbl) > 0) {
      cleaned_final <- vapply(final_tbl$most_frequent_annotation, clean_and_match_annotation, character(1))
      mapped_final <- map_celltypes_to_cl(cleaned_final, cl_term_map = cl_term_map, verbose = FALSE)
      rows[[idx]] <- data.frame(
        resolution = res_name,
        cluster = as.character(final_tbl$cluster),
        role = "selected",
        label = as.character(final_tbl$most_frequent_annotation),
        cleaned_label = cleaned_final,
        clid = mapped_final$clid,
        cl_label = mapped_final$cl_label,
        percentage = as.numeric(final_tbl$max_percentage),
        max_percentage = as.numeric(final_tbl$max_percentage),
        other_annotations = as.character(final_tbl$other_annotations %||% ""),
        avg_distance = as.numeric(final_tbl$avg_distance %||% NA_real_),
        stringsAsFactors = FALSE
      )
      idx <- idx + 1L

      other_rows <- lapply(seq_len(nrow(final_tbl)), function(i) {
        other_terms <- extract_other_annotation_terms(final_tbl$other_annotations[[i]] %||% "")
        if (length(other_terms) == 0) {
          return(NULL)
        }
        cleaned_other <- vapply(other_terms, clean_and_match_annotation, character(1))
        mapped_other <- map_celltypes_to_cl(cleaned_other, cl_term_map = cl_term_map, verbose = FALSE)
        data.frame(
          resolution = res_name,
          cluster = as.character(final_tbl$cluster[[i]]),
          role = "other",
          label = other_terms,
          cleaned_label = cleaned_other,
          clid = mapped_other$clid,
          cl_label = mapped_other$cl_label,
          percentage = NA_real_,
          max_percentage = as.numeric(final_tbl$max_percentage[[i]] %||% NA_real_),
          other_annotations = as.character(final_tbl$other_annotations[[i]] %||% ""),
          avg_distance = as.numeric(safe_column_value(final_tbl, "avg_distance", i, NA_real_)),
          stringsAsFactors = FALSE
        )
      })
      other_rows <- Filter(Negate(is.null), other_rows)
      if (length(other_rows) > 0) {
        rows[[idx]] <- do.call(rbind, other_rows)
        idx <- idx + 1L
      }
    }
  }

  if (length(rows) == 0) {
    return(data.frame(
      resolution = character(0),
      cluster = character(0),
      role = character(0),
      label = character(0),
      cleaned_label = character(0),
      clid = character(0),
      cl_label = character(0),
      percentage = numeric(0),
      max_percentage = numeric(0),
      other_annotations = character(0),
      avg_distance = numeric(0),
      mapping_status = character(0),
      stringsAsFactors = FALSE
    ))
  }

  mapping_df <- do.call(rbind, rows)
  mapping_df$mapping_status <- ifelse(is.na(mapping_df$clid) | mapping_df$clid == "", "unmapped", "mapped")
  rownames(mapping_df) <- NULL
  mapping_df
}

run_preprocess_parent <- function() {
  bootstrap <- bootstrap_parent_outputs(inputs)
  if (!is.null(bootstrap)) {
    return(list(
      preprocessed_rds = bootstrap$preprocessed_rds,
      bootstrapped = TRUE
    ))
  }

  seurat_obj <- recreate_seurat_if_needed(readRDS(inputs$seurat_rds))
  if (isTRUE(annotation_cfg$preprocess)) {
    if (file.exists(preprocessed_rds)) {
      seurat_obj <- readRDS(preprocessed_rds)
      if (!has_pca_reduction(seurat_obj)) {
        seurat_obj <- preprocess_seurat_object(
          recreate_seurat_if_needed(readRDS(inputs$seurat_rds)),
          save_path = preprocessed_rds
        )
      }
    } else {
      seurat_obj <- preprocess_seurat_object(
        seurat_obj,
        save_path = preprocessed_rds
      )
    }
  } else if (!file.exists(preprocessed_rds)) {
    saveRDS(seurat_obj, preprocessed_rds)
  }

  list(
    preprocessed_rds = preprocessed_rds,
    bootstrapped = FALSE
  )
}

run_cluster_parent_markers <- function() {
  bootstrap <- bootstrap_parent_outputs(inputs)
  if (!is.null(bootstrap)) {
    return(list(
      preprocessed_rds = bootstrap$preprocessed_rds,
      clustered_rds = bootstrap$clustered_rds,
      markers_dir = bootstrap$markers_dir,
      bootstrapped = TRUE
    ))
  }

  if (has_external_marker_genes()) {
    run_preprocess_parent()
    seurat_obj <- readRDS(preprocessed_rds)
    copied_markers_dir <- copy_external_marker_genes(external_marker_genes_dir, markers_dir)
    validate_marker_gene_inputs(seurat_obj, copied_markers_dir)
    saveRDS(seurat_obj, clustered_rds)
    return(list(
      preprocessed_rds = preprocessed_rds,
      clustered_rds = clustered_rds,
      markers_dir = copied_markers_dir,
      external_marker_genes_dir = external_marker_genes_dir,
      skipped_clustering = TRUE,
      bootstrapped = FALSE
    ))
  }

  run_preprocess_parent()
  seurat_obj <- readRDS(preprocessed_rds)
  if (file.exists(clustered_rds) && dir.exists(markers_dir)) {
    return(list(
      preprocessed_rds = preprocessed_rds,
      clustered_rds = clustered_rds,
      markers_dir = markers_dir,
      bootstrapped = FALSE
    ))
  }

  dir.create(markers_dir, recursive = TRUE, showWarnings = FALSE)
  clust_res <- run_multi_resolution_clustering(
    seurat_obj = seurat_obj,
    resolutions = unlist(annotation_cfg$parent_res),
    result_dir = markers_dir
  )
  seurat_obj <- clust_res$seurat_obj
  saveRDS(seurat_obj, clustered_rds)

  list(
    preprocessed_rds = preprocessed_rds,
    clustered_rds = clustered_rds,
    markers_dir = markers_dir,
    bootstrapped = FALSE
  )
}

run_parent_annotation_raw <- function() {
  bootstrap <- bootstrap_parent_outputs(inputs)
  if (!is.null(bootstrap)) {
    return(list(
      clustered_rds = bootstrap$clustered_rds,
      markers_dir = bootstrap$markers_dir,
      annotation_parent_rds = bootstrap$annotation_parent_rds,
      prediction_dir = bootstrap$prediction_dir,
      bootstrapped = TRUE
    ))
  }

  run_cluster_parent_markers()
  if (!file.exists(annotation_parent_rds)) {
    context <- build_annotation_context()
    seurat_obj <- readRDS(clustered_rds)
    dir.create(prediction_dir, recursive = TRUE, showWarnings = FALSE)
    annotation_parent <- gptanno(
      seurat_obj = seurat_obj,
      resolutions = unlist(annotation_cfg$parent_res),
      cl = context$cl,
      graph = context$graph,
      mapping_dict = context$mapping_dict,
      tissue_name = annotation_cfg$tissue_name,
      n_runs = annotation_cfg$n_runs_parent,
      marker_dir = markers_dir,
      save_plots = TRUE,
      plot_dir = prediction_dir,
      llm_config = context$llm_config
    )
    saveRDS(annotation_parent, annotation_parent_rds)
  }

  list(
    clustered_rds = clustered_rds,
    markers_dir = markers_dir,
    annotation_parent_rds = annotation_parent_rds,
    prediction_dir = prediction_dir,
    bootstrapped = FALSE
  )
}

run_map_parent_ontology <- function() {
  run_parent_annotation_raw()
  annotation_parent <- readRDS(annotation_parent_rds)
  mapping_df <- build_parent_ontology_mapping_df(annotation_parent, GPTAnno::cl_term_map)
  saveRDS(mapping_df, parent_ontology_map_rds)
  utils::write.csv(mapping_df, parent_ontology_map_csv, row.names = FALSE)
  list(
    annotation_parent_rds = annotation_parent_rds,
    ontology_mapping_rds = parent_ontology_map_rds,
    ontology_mapping_csv = parent_ontology_map_csv,
    mapped_rows = sum(mapping_df$mapping_status == "mapped"),
    unmapped_rows = sum(mapping_df$mapping_status == "unmapped")
  )
}

run_select_parent_resolution <- function() {
  bootstrap <- bootstrap_parent_outputs(inputs)
  forced_resolution <- normalize_resolution_name(annotation_cfg$forced_parent_resolution %||% NULL)
  if (!is.null(bootstrap)) {
    best_resolution <- bootstrap$best_resolution
    if (!is.null(forced_resolution) && identical(forced_resolution, normalize_resolution_name(best_resolution))) {
      best_resolution <- forced_resolution
    }
    info <- list(
      best_resolution = best_resolution,
      best_resolution_value = sub("^res_", "", best_resolution),
      cluster_col = paste0("cluster_res.", sub("^res_", "", best_resolution))
    )
    jsonlite::write_json(info, best_parent_resolution_json, auto_unbox = TRUE, pretty = TRUE)
    return(c(
      list(
        annotation_scores_csv = bootstrap$annotation_scores_csv,
        best_parent_resolution_json = best_parent_resolution_json,
        bootstrapped = TRUE
      ),
      info
    ))
  }

  run_parent_annotation_raw()
  annotation_parent <- readRDS(annotation_parent_rds)
  summary_table <- score_annotation_resolutions(
    annotation_result_list = annotation_parent,
    output_csv = annotation_scores_csv
  )
  available_resolutions <- as.character(summary_table$resolution)
  best_res_name <- summary_table$resolution[[1]]
  if (!is.null(forced_resolution) && forced_resolution %in% available_resolutions) {
    best_res_name <- forced_resolution
  }
  info <- list(
    best_resolution = best_res_name,
    best_resolution_value = sub("^res_", "", best_res_name),
    cluster_col = paste0("cluster_res.", sub("^res_", "", best_res_name))
  )
  jsonlite::write_json(info, best_parent_resolution_json, auto_unbox = TRUE, pretty = TRUE)
  c(
    list(
      annotation_scores_csv = annotation_scores_csv,
      best_parent_resolution_json = best_parent_resolution_json,
      bootstrapped = FALSE
    ),
    info
  )
}

run_assign_parent_labels <- function() {
  bootstrap <- bootstrap_parent_outputs(inputs)
  if (!is.null(bootstrap)) {
    return(list(
      parent_seurat_rds = bootstrap$parent_seurat_rds,
      best_resolution = bootstrap$best_resolution,
      best_resolution_value = bootstrap$best_resolution_value,
      cluster_col = bootstrap$cluster_col,
      annotation_scores_csv = bootstrap$annotation_scores_csv,
      bootstrapped = TRUE
    ))
  }

  resolution_info <- run_select_parent_resolution()
  seurat_obj <- readRDS(clustered_rds)
  annotation_parent <- readRDS(annotation_parent_rds)
  best_res_obj <- annotation_parent[[resolution_info$best_resolution]]

  seurat_obj <- assign_celltype(
    seurat_obj = seurat_obj,
    annotation_summary = best_res_obj,
    cluster_col = resolution_info$cluster_col,
    new_celltype = "celltype_parent"
  )
  saveRDS(seurat_obj, parent_seurat_rds)

  c(
    list(
      parent_seurat_rds = parent_seurat_rds,
      bootstrapped = FALSE
    ),
    resolution_info
  )
}

run_subcluster_find_markers <- function() {
  if (file.exists(subcluster_result_rds)) {
    cached <- readRDS(subcluster_result_rds)
    cached_parent <- load_parent_resolution_info()
    return(list(
      subcluster_result_rds = subcluster_result_rds,
      subcluster_folder = file.path(subcluster_dir, paste0("subclusters_res", cached_parent$best_resolution_value)),
      subclustering_performed = !is.null(cached)
    ))
  }

  context <- build_annotation_context()
  parent_info <- run_assign_parent_labels()
  seurat_obj <- readRDS(parent_info$parent_seurat_rds)
  subcluster_folder <- file.path(subcluster_dir, paste0("subclusters_res", parent_info$best_resolution_value))
  dir.create(subcluster_folder, recursive = TRUE, showWarnings = FALSE)

  subcluster_result <- subcluster_and_find_markers(
    seurat_obj = seurat_obj,
    cl = context$cl,
    predicted_celltype_column = "celltype_parent",
    cluster_col = parent_info$cluster_col,
    output_dir = subcluster_folder,
    resolutions = unlist(annotation_cfg$sub_res),
    min_cell_count = annotation_cfg$min_cell_count,
    celltypes_to_subcluster = alignment_cfg$celltypes_to_subcluster
  )

  saveRDS(subcluster_result, subcluster_result_rds)
  list(
    subcluster_result_rds = subcluster_result_rds,
    subcluster_folder = subcluster_folder,
    subclustering_performed = !is.null(subcluster_result)
  )
}

run_subcluster_annotate_ontology <- function() {
  if (file.exists(final_ontology_rds) && file.exists(ontology_workflow_rds)) {
    cached_subcluster <- run_subcluster_find_markers()
    return(list(
      ontology_workflow_rds = ontology_workflow_rds,
      ontology_seurat_rds = final_ontology_rds,
      subclustering_performed = isTRUE(cached_subcluster$subclustering_performed)
    ))
  }

  context <- build_annotation_context()
  parent_info <- run_assign_parent_labels()
  subcluster_info <- run_subcluster_find_markers()
  seurat_obj <- readRDS(parent_info$parent_seurat_rds)
  subcluster_result <- readRDS(subcluster_info$subcluster_result_rds)

  if (is.null(subcluster_result)) {
    seurat_obj@meta.data$celltype_final <- seurat_obj@meta.data$celltype_parent
    saveRDS(seurat_obj, final_ontology_rds)
    saveRDS(NULL, ontology_workflow_rds)
    return(list(
      ontology_workflow_rds = ontology_workflow_rds,
      ontology_seurat_rds = final_ontology_rds,
      subclustering_performed = FALSE
    ))
  }

  ontology_workflow <- run_subcluster_annotation_workflow(
    base_dir = subcluster_info$subcluster_folder,
    strategy = "ontology",
    cl = context$cl,
    tissue_name = annotation_cfg$tissue_name,
    resolutions = unlist(annotation_cfg$sub_res),
    n_runs = annotation_cfg$n_runs_sub,
    ontology_graph = context$graph,
    select_best = TRUE,
    user_restrict_to = alignment_cfg$user_restrict_to,
    combine_restrictions = isTRUE(alignment_cfg$combine_restrictions),
    celltypes_to_subcluster = alignment_cfg$celltypes_to_subcluster,
    llm_config = context$llm_config
  )
  saveRDS(ontology_workflow, ontology_workflow_rds)

  manual_resolution_map <- alignment_cfg$manual_resolution_map
  use_best <- is.null(manual_resolution_map) || length(manual_resolution_map) == 0
  ontology_annotated <- assign_subcluster_annotations_to_full(
    full_seurat = seurat_obj,
    workflow_results = ontology_workflow,
    use_best = use_best,
    resolution_map = if (use_best) NULL else unlist(manual_resolution_map),
    parent_column = "celltype_parent",
    final_colname = "celltype_final"
  )
  saveRDS(ontology_annotated, final_ontology_rds)

  list(
    ontology_workflow_rds = ontology_workflow_rds,
    ontology_seurat_rds = final_ontology_rds,
    subclustering_performed = TRUE
  )
}

run_subcluster_annotate_inheritance <- function() {
  if (file.exists(inheritance_workflow_rds)) {
    cached_subcluster <- run_subcluster_find_markers()
    return(list(
      inheritance_workflow_rds = inheritance_workflow_rds,
      subclustering_performed = isTRUE(cached_subcluster$subclustering_performed)
    ))
  }

  context <- build_annotation_context()
  parent_info <- run_assign_parent_labels()
  subcluster_info <- run_subcluster_find_markers()

  if (!isTRUE(subcluster_info$subclustering_performed)) {
    saveRDS(NULL, inheritance_workflow_rds)
    return(list(
      inheritance_workflow_rds = inheritance_workflow_rds,
      subclustering_performed = FALSE
    ))
  }

  inheritance_workflow <- run_subcluster_annotation_workflow(
    base_dir = subcluster_info$subcluster_folder,
    strategy = "marker_inheritance",
    cl = context$cl,
    parent_marker_root = markers_dir,
    parent_res = parent_info$best_resolution_value,
    parent_cluster_col = parent_info$cluster_col,
    tissue_name = annotation_cfg$tissue_name,
    resolutions = unlist(annotation_cfg$sub_res),
    n_runs = annotation_cfg$n_runs_sub,
    ontology_graph = context$graph,
    select_best = TRUE,
    celltypes_to_subcluster = alignment_cfg$celltypes_to_subcluster,
    llm_config = context$llm_config
  )
  saveRDS(inheritance_workflow, inheritance_workflow_rds)

  list(
    inheritance_workflow_rds = inheritance_workflow_rds,
    subclustering_performed = TRUE
  )
}

run_finalize_subcluster_annotations <- function() {
  if (file.exists(final_inherited_rds) && file.exists(final_metadata_csv)) {
    cached_subcluster <- run_subcluster_find_markers()
    return(list(
      final_seurat_rds = final_inherited_rds,
      final_metadata_csv = final_metadata_csv,
      final_dimplot_pdf = if (file.exists(final_dimplot_pdf)) final_dimplot_pdf else NULL,
      subclustering_performed = isTRUE(cached_subcluster$subclustering_performed)
    ))
  }

  run_subcluster_annotate_ontology()
  inheritance_info <- run_subcluster_annotate_inheritance()

  if (!isTRUE(inheritance_info$subclustering_performed)) {
    seurat_obj <- readRDS(final_ontology_rds)
    seurat_obj@meta.data$celltype_final_inherited <- seurat_obj@meta.data$celltype_parent
    saveRDS(seurat_obj, final_inherited_rds)
    utils::write.csv(seurat_obj@meta.data, final_metadata_csv, row.names = TRUE)
    return(list(
      final_seurat_rds = final_inherited_rds,
      final_metadata_csv = final_metadata_csv,
      final_dimplot_pdf = NULL,
      subclustering_performed = FALSE
    ))
  }

  ontology_annotated <- readRDS(final_ontology_rds)
  inheritance_workflow <- readRDS(inheritance_workflow_rds)

  final_annotated <- assign_subcluster_annotations_to_full(
    full_seurat = ontology_annotated,
    workflow_results = inheritance_workflow,
    use_best = TRUE,
    parent_column = "celltype_parent",
    final_colname = "celltype_final_inherited"
  )

  saveRDS(final_annotated, final_inherited_rds)
  utils::write.csv(final_annotated@meta.data, final_metadata_csv, row.names = TRUE)

  grDevices::pdf(final_dimplot_pdf, width = 12, height = 5)
  print(Seurat::DimPlot(final_annotated, group.by = "celltype_final", label = TRUE) + Seurat::NoLegend())
  print(Seurat::DimPlot(final_annotated, group.by = "celltype_final_inherited", label = TRUE) + Seurat::NoLegend())
  grDevices::dev.off()

  list(
    final_seurat_rds = final_inherited_rds,
    final_metadata_csv = final_metadata_csv,
    final_dimplot_pdf = final_dimplot_pdf,
    subclustering_performed = TRUE
  )
}

outputs <- switch(
  spec$stage,
  preprocess_parent = run_preprocess_parent(),
  cluster_parent_markers = run_cluster_parent_markers(),
  annotate_parent_raw = run_parent_annotation_raw(),
  map_parent_ontology = run_map_parent_ontology(),
  select_parent_resolution = run_select_parent_resolution(),
  assign_parent_labels = run_assign_parent_labels(),
  subcluster_find_markers = run_subcluster_find_markers(),
  subcluster_annotate_ontology = run_subcluster_annotate_ontology(),
  subcluster_annotate_inheritance = run_subcluster_annotate_inheritance(),
  finalize_subcluster_annotations = run_finalize_subcluster_annotations(),
  stop("Unsupported GPTAnno tool stage: ", spec$stage)
)

ensure_parent_dir(spec$outputs_json)
jsonlite::write_json(outputs, spec$outputs_json, auto_unbox = TRUE, pretty = TRUE)
