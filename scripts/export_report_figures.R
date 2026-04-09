args <- commandArgs(trailingOnly = TRUE)
if (length(args) != 1) {
  stop("Usage: Rscript export_report_figures.R <spec.json>")
}

suppressPackageStartupMessages({
  library(jsonlite)
  library(Seurat)
  library(ggplot2)
})

spec <- jsonlite::fromJSON(args[[1]], simplifyVector = FALSE)
output_dir <- spec$output_dir
dir.create(output_dir, recursive = TRUE, showWarnings = FALSE)

pick_reduction <- function(seurat_obj) {
  reduction_names <- names(seurat_obj@reductions)
  if (length(reduction_names) == 0) {
    return(NULL)
  }
  umap_names <- reduction_names[grepl("umap", reduction_names, ignore.case = TRUE)]
  if (length(umap_names) > 0) {
    return(umap_names[[1]])
  }
  reduction_names[[1]]
}

save_dimplot_png <- function(seurat_obj, group_by, out_path, title_text) {
  if (is.null(group_by) || !nzchar(group_by)) {
    return(FALSE)
  }
  if (!group_by %in% colnames(seurat_obj@meta.data)) {
    return(FALSE)
  }
  reduction_name <- pick_reduction(seurat_obj)
  if (is.null(reduction_name)) {
    return(FALSE)
  }

  plot_obj <- Seurat::DimPlot(
    seurat_obj,
    reduction = reduction_name,
    group.by = group_by,
    label = TRUE,
    repel = TRUE
  ) +
    Seurat::NoLegend() +
    ggplot2::ggtitle(title_text) +
    ggplot2::theme(
      plot.title = ggplot2::element_text(face = "bold", size = 14),
      axis.title = ggplot2::element_blank(),
      axis.text = ggplot2::element_blank(),
      axis.ticks = ggplot2::element_blank()
    )

  ggplot2::ggsave(
    filename = out_path,
    plot = plot_obj,
    width = 12,
    height = 8,
    units = "in",
    dpi = 180
  )
  TRUE
}

outputs <- list(
  output_dir = normalizePath(output_dir, winslash = "/", mustWork = FALSE),
  figures = list(),
  warnings = character()
)

save_if_possible <- function(key, seurat_rds, group_by, title_text) {
  if (is.null(seurat_rds) || !file.exists(seurat_rds)) {
    outputs$warnings <<- c(outputs$warnings, paste0(key, ": missing Seurat input"))
    return()
  }
  message("[report_figures] Loading ", key, " object")
  seurat_obj <- readRDS(seurat_rds)
  out_path <- file.path(output_dir, paste0(key, ".png"))
  ok <- save_dimplot_png(seurat_obj, group_by, out_path, title_text)
  if (ok) {
    outputs$figures[[key]] <<- normalizePath(out_path, winslash = "/", mustWork = FALSE)
  } else {
    outputs$warnings <<- c(
      outputs$warnings,
      paste0(key, ": could not render plot for metadata column '", group_by, "'")
    )
  }
}

save_if_possible(
  key = "parent_clusters",
  seurat_rds = spec$parent$seurat_rds,
  group_by = spec$parent$cluster_col,
  title_text = "Parent Clusters"
)
save_if_possible(
  key = "parent_initial",
  seurat_rds = spec$parent$seurat_rds,
  group_by = spec$parent$label_col,
  title_text = "Parent Annotation"
)
save_if_possible(
  key = "parent_reviewed",
  seurat_rds = spec$reviewed_parent$seurat_rds,
  group_by = spec$reviewed_parent$label_col,
  title_text = "Agent-Reviewed Parent Annotation"
)
save_if_possible(
  key = "subcluster_final",
  seurat_rds = spec$subcluster$seurat_rds,
  group_by = spec$subcluster$label_col,
  title_text = "Subcluster Final Annotation"
)
save_if_possible(
  key = "subcluster_inherited",
  seurat_rds = spec$subcluster$seurat_rds,
  group_by = spec$subcluster$inherited_label_col,
  title_text = "Subcluster Inherited Annotation"
)

jsonlite::write_json(
  outputs,
  spec$outputs_json,
  auto_unbox = TRUE,
  pretty = TRUE,
  null = "null"
)
