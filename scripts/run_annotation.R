args <- commandArgs(trailingOnly = TRUE)
if (length(args) != 1) {
  stop("Usage: Rscript run_annotation.R <spec.json>")
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
    cluster_col = cluster_col
  )
}

inputs <- spec$inputs
annotation_cfg <- spec$annotation
alignment_cfg <- spec$alignment

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
      llm_config = build_llm_config(spec$llm$annotation),
      mapping_dict = load_mapping_dict()
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

final_ontology_rds <- file.path(subcluster_dir, "seurat_ontology_annotated.rds")
final_inherited_rds <- file.path(subcluster_dir, "seurat_final_annotated.rds")
final_metadata_csv <- file.path(subcluster_dir, "metadata_final.csv")
final_dimplot_pdf <- file.path(subcluster_dir, "DimPlot_celltype_final.pdf")
ontology_workflow_rds <- file.path(subcluster_dir, "ontology_workflow.rds")
inheritance_workflow_rds <- file.path(subcluster_dir, "marker_inheritance_workflow.rds")

run_parent_stage <- function() {
  bootstrapped_outputs <- bootstrap_parent_outputs(inputs)
  if (!is.null(bootstrapped_outputs)) {
    message("Using bootstrap_parent outputs for annotate_parent.")
    return(bootstrapped_outputs)
  }

  context <- build_annotation_context()
  seurat_obj <- readRDS(inputs$seurat_rds)

  if (isTRUE(annotation_cfg$preprocess)) {
    if (file.exists(preprocessed_rds)) {
      seurat_obj <- readRDS(preprocessed_rds)
    } else {
      seurat_obj <- preprocess_seurat_object(
        seurat_obj,
        save_path = preprocessed_rds
      )
    }
  } else if (file.exists(preprocessed_rds)) {
    seurat_obj <- readRDS(preprocessed_rds)
  } else {
    saveRDS(seurat_obj, preprocessed_rds)
  }

  if (file.exists(clustered_rds) && dir.exists(markers_dir)) {
    seurat_obj <- readRDS(clustered_rds)
  } else {
    dir.create(markers_dir, recursive = TRUE, showWarnings = FALSE)
    clust_res <- run_multi_resolution_clustering(
      seurat_obj = seurat_obj,
      resolutions = unlist(annotation_cfg$parent_res),
      result_dir = markers_dir
    )
    seurat_obj <- clust_res$seurat_obj
    saveRDS(seurat_obj, clustered_rds)
  }

  if (file.exists(annotation_parent_rds)) {
    annotation_parent <- readRDS(annotation_parent_rds)
  } else {
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

  summary_table <- score_annotation_resolutions(
    annotation_result_list = annotation_parent,
    output_csv = annotation_scores_csv
  )
  best_res_name <- summary_table$resolution[[1]]
  best_res_obj <- annotation_parent[[best_res_name]]
  res_val <- sub("^res_", "", best_res_name)
  cluster_col <- paste0("cluster_res.", res_val)

  seurat_obj <- assign_celltype(
    seurat_obj = seurat_obj,
    annotation_summary = best_res_obj,
    cluster_col = cluster_col,
    new_celltype = "celltype_parent"
  )
  saveRDS(seurat_obj, parent_seurat_rds)

  list(
    preprocessed_rds = preprocessed_rds,
    clustered_rds = clustered_rds,
    markers_dir = markers_dir,
    annotation_parent_rds = annotation_parent_rds,
    annotation_scores_csv = annotation_scores_csv,
    prediction_dir = prediction_dir,
    parent_seurat_rds = parent_seurat_rds,
    best_resolution = best_res_name,
    best_resolution_value = res_val,
    cluster_col = cluster_col
  )
}

run_subcluster_stage <- function() {
  context <- build_annotation_context()
  if (!file.exists(parent_seurat_rds)) {
    stop("Parent annotation output not found: ", parent_seurat_rds)
  }

  parent_outputs <- NULL
  if (file.exists(spec$outputs_json)) {
    parent_outputs <- tryCatch(jsonlite::fromJSON(spec$outputs_json), error = function(e) NULL)
  }
  if (is.null(parent_outputs) || is.null(parent_outputs$best_resolution_value)) {
    parent_outputs_path <- file.path(dirname(spec$outputs_json), "annotate_parent.outputs.json")
    if (!file.exists(parent_outputs_path)) {
      stop("annotate_parent outputs JSON not found: ", parent_outputs_path)
    }
    parent_outputs <- jsonlite::fromJSON(parent_outputs_path)
  }

  seurat_obj <- readRDS(parent_seurat_rds)
  res_val <- parent_outputs$best_resolution_value
  cluster_col <- parent_outputs$cluster_col
  subcluster_folder <- file.path(subcluster_dir, paste0("subclusters_res", res_val))
  dir.create(subcluster_folder, recursive = TRUE, showWarnings = FALSE)

  subcluster_result <- subcluster_and_find_markers(
    seurat_obj = seurat_obj,
    cl = context$cl,
    predicted_celltype_column = "celltype_parent",
    cluster_col = cluster_col,
    output_dir = subcluster_folder,
    resolutions = unlist(annotation_cfg$sub_res),
    min_cell_count = annotation_cfg$min_cell_count,
    celltypes_to_subcluster = alignment_cfg$celltypes_to_subcluster
  )

  if (is.null(subcluster_result)) {
    seurat_obj@meta.data$celltype_final <- seurat_obj@meta.data$celltype_parent
    seurat_obj@meta.data$celltype_final_inherited <- seurat_obj@meta.data$celltype_parent
    saveRDS(seurat_obj, final_inherited_rds)
    utils::write.csv(seurat_obj@meta.data, final_metadata_csv, row.names = TRUE)
    return(list(
      subcluster_folder = subcluster_folder,
      ontology_workflow_rds = NULL,
      inheritance_workflow_rds = NULL,
      ontology_seurat_rds = NULL,
      final_seurat_rds = final_inherited_rds,
      final_metadata_csv = final_metadata_csv,
      final_dimplot_pdf = NULL,
      subclustering_performed = FALSE
    ))
  }

  ontology_workflow <- run_subcluster_annotation_workflow(
    base_dir = subcluster_folder,
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

  inheritance_workflow <- run_subcluster_annotation_workflow(
    base_dir = subcluster_folder,
    strategy = "marker_inheritance",
    cl = context$cl,
    parent_marker_root = markers_dir,
    parent_res = res_val,
    parent_cluster_col = cluster_col,
    tissue_name = annotation_cfg$tissue_name,
    resolutions = unlist(annotation_cfg$sub_res),
    n_runs = annotation_cfg$n_runs_sub,
    ontology_graph = context$graph,
    select_best = TRUE,
    celltypes_to_subcluster = alignment_cfg$celltypes_to_subcluster,
    llm_config = context$llm_config
  )
  saveRDS(inheritance_workflow, inheritance_workflow_rds)

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
    subcluster_folder = subcluster_folder,
    ontology_workflow_rds = ontology_workflow_rds,
    inheritance_workflow_rds = inheritance_workflow_rds,
    ontology_seurat_rds = final_ontology_rds,
    final_seurat_rds = final_inherited_rds,
    final_metadata_csv = final_metadata_csv,
    final_dimplot_pdf = final_dimplot_pdf,
    subclustering_performed = TRUE
  )
}

outputs <- switch(
  spec$stage,
  annotate_parent = run_parent_stage(),
  annotate_subclusters = run_subcluster_stage(),
  stop("Unsupported annotation stage: ", spec$stage)
)

ensure_parent_dir(spec$outputs_json)
jsonlite::write_json(outputs, spec$outputs_json, auto_unbox = TRUE, pretty = TRUE)
