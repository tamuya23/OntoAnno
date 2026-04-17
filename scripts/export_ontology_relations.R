args <- commandArgs(trailingOnly = TRUE)
if (length(args) != 1) {
  stop("Usage: Rscript export_ontology_relations.R <spec.json>")
}

suppressPackageStartupMessages({
  library(jsonlite)
  library(ontologyIndex)
  library(pkgload)
})

`%||%` <- function(x, y) {
  if (is.null(x)) y else x
}

spec <- jsonlite::fromJSON(args[[1]], simplifyVector = FALSE)
pkgload::load_all(spec$gptanno_path, quiet = TRUE)

dir.create(spec$output_dir, recursive = TRUE, showWarnings = FALSE)
dir.create(dirname(spec$ontology_cache), recursive = TRUE, showWarnings = FALSE)
relations_dir <- file.path(spec$output_dir, "relations")
dir.create(relations_dir, recursive = TRUE, showWarnings = FALSE)
unlink(file.path(relations_dir, "*.json"))

review_index <- jsonlite::fromJSON(spec$review_index_json, simplifyVector = FALSE)
policy <- spec$policy %||% list()

resolve_ontology_path <- function(spec) {
  override_path <- spec$ontology_obo
  cache_path <- spec$ontology_cache

  if (!is.null(override_path) && nzchar(override_path)) {
    if (!file.exists(override_path)) {
      stop(sprintf("Local ontology OBO not found: %s", override_path))
    }
    return(normalizePath(override_path, winslash = "/", mustWork = TRUE))
  }

  if (file.exists(cache_path)) {
    return(normalizePath(cache_path, winslash = "/", mustWork = TRUE))
  }

  download_error <- NULL
  tryCatch(
    {
      utils::download.file(spec$ontology_url, destfile = cache_path, mode = "wb", quiet = TRUE)
    },
    error = function(e) {
      download_error <<- conditionMessage(e)
    }
  )

  if (file.exists(cache_path)) {
    return(normalizePath(cache_path, winslash = "/", mustWork = TRUE))
  }

  message_parts <- c(
    "Failed to obtain Cell Ontology OBO.",
    sprintf("Tried download from %s.", spec$ontology_url),
    if (!is.null(download_error)) sprintf("Download error: %s.", download_error) else character(0),
    "Set ONTOANNO_CL_OBO to a local cl.obo file or place one at the cache path.",
    sprintf("Cache path: %s", cache_path)
  )
  stop(paste(message_parts, collapse = " "))
}

ontology_path <- resolve_ontology_path(spec)
cl <- ontologyIndex::get_ontology(ontology_path, extract_tags = "everything")
graph <- build_ontology_graph(cl)
ancestor_type_map <- build_ancestor_type_map(cl)

ancestor_min_depth <- if (!is.null(spec$ancestor_min_depth)) as.numeric(spec$ancestor_min_depth) else 6
excluded_ancestor_labels <- tolower(trimws(unlist(spec$excluded_ancestor_labels %||% list(), use.names = FALSE)))
policy_granularity <- tolower(trimws(policy$granularity %||% "balanced"))
if (!policy_granularity %in% c("coarse", "balanced", "fine")) {
  policy_granularity <- "balanced"
}

parse_other_annotations <- function(text) {
  if (is.null(text) || !nzchar(trimws(text))) return(list())
  parts <- strsplit(text, ",")[[1]]
  out <- list()
  for (part in parts) {
    item <- trimws(part)
    if (!nzchar(item)) next
    label <- sub("\\s+[0-9]+(\\.[0-9]+)?\\s*%$", "", item)
    pct_match <- regmatches(item, regexpr("[0-9]+(\\.[0-9]+)?\\s*%$", item))
    percentage <- if (length(pct_match) > 0 && nzchar(pct_match)) as.numeric(sub("%", "", pct_match)) else NA_real_
    out[[length(out) + 1]] <- list(raw_label = label, percentage = percentage)
  }
  out
}

normalize_candidate_variants <- function(raw_label) {
  state_prefixes <- c(
    "activated", "cycling", "proliferating", "mature",
    "resident", "inflammatory", "resting", "quiescent"
  )

  normalize_one <- function(text) {
    text <- tolower(trimws(text))
    text <- gsub("[_/]+", " ", text)
    text <- gsub("\\s+", " ", text)
    trimws(text)
  }

  variants <- c(normalize_one(raw_label))
  label <- variants[[1]]

  if (grepl("\\(", label) && grepl("\\)", label)) {
    outside <- normalize_one(gsub("\\s*\\([^)]*\\)", "", label))
    inside <- normalize_one(sub(".*\\(([^)]*)\\).*", "\\1", label))
    variants <- c(variants, outside, inside)
  }

  stripped <- c()
  prefix_pattern <- paste0("^(", paste(state_prefixes, collapse = "|"), ")\\s+")
  for (variant in variants) {
    stripped <- c(stripped, normalize_one(gsub(prefix_pattern, "", variant)))
  }
  variants <- unique(c(variants, stripped))
  variants <- variants[nzchar(variants)]
  unique(variants)
}

map_candidate_entry <- function(raw_label, percentage, source) {
  variants <- normalize_candidate_variants(raw_label)
  matched_variant <- NULL
  cleaned_label <- NULL
  clid <- NA_character_
  cl_label <- NA_character_
  mapping_status <- "unmapped"

  for (i in seq_along(variants)) {
    variant <- variants[[i]]
    cleaned <- clean_and_match_annotation(variant)
    mapped <- map_celltypes_to_cl(cleaned, cl_term_map = GPTAnno::cl_term_map, verbose = FALSE)
    candidate_clid <- mapped$clid[[1]]
    candidate_label <- mapped$cl_label[[1]]
    if (!is.na(candidate_clid) && nzchar(candidate_clid)) {
      matched_variant <- variant
      cleaned_label <- cleaned
      clid <- candidate_clid
      cl_label <- candidate_label
      mapping_status <- if (i == 1) "direct" else "normalized"
      break
    }
    if (is.null(cleaned_label)) {
      cleaned_label <- cleaned
    }
  }

  list(
    raw_label = raw_label,
    percentage = percentage,
    source = source,
    normalized_variants = variants,
    matched_variant = matched_variant,
    cleaned_label = cleaned_label,
    clid = clid,
    cl_label = cl_label,
    mapping_status = mapping_status
  )
}

candidate_df <- function(packet) {
  assigned <- packet$summary$assigned_label
  others <- parse_other_annotations(packet$summary$other_annotations)
  rows <- list(list(raw_label = assigned, percentage = packet$summary$max_percentage, source = "assigned"))
  if (length(others) > 0) {
    for (item in others) {
      rows[[length(rows) + 1]] <- list(
        raw_label = item$raw_label,
        percentage = item$percentage,
        source = "other"
      )
    }
  }
  entries <- lapply(rows, function(row) {
    map_candidate_entry(
      raw_label = row$raw_label,
      percentage = row$percentage,
      source = row$source
    )
  })
  df <- do.call(rbind, lapply(entries, function(entry) {
    data.frame(
      raw_label = entry$raw_label,
      percentage = if (is.null(entry$percentage) || is.na(entry$percentage)) NA_real_ else as.numeric(entry$percentage),
      source = entry$source,
      matched_variant = if (is.null(entry$matched_variant)) NA_character_ else entry$matched_variant,
      cleaned_label = if (is.null(entry$cleaned_label)) NA_character_ else entry$cleaned_label,
      clid = entry$clid,
      cl_label = entry$cl_label,
      mapping_status = entry$mapping_status,
      stringsAsFactors = FALSE
    )
  }))
  df
}

pair_relation <- function(from_row, to_row) {
  from_id <- from_row$clid
  to_id <- to_row$clid
  relation <- "unmapped"
  distance <- NA_real_
  common_ancestor <- NULL

  if (!is.na(from_id) && !is.na(to_id)) {
    rel <- check_cl_relationship(from_id, to_id, cl, graph)
    relation <- rel$relationship
    distance <- rel$distance
    common <- tryCatch(
      get_common_ancestors(from_row$cl_label, to_row$cl_label, GPTAnno::cl_term_map, cl, ancestor_type_map, verbose = FALSE),
      error = function(e) NULL
    )
    if (!is.null(common) && nrow(common) > 0) {
      common_ancestor <- list(
        clid = common$ancestor_clid[[1]],
        label = common$ancestor_name[[1]]
      )
    }
  }

  list(
    from_label = from_row$raw_label,
    from_clid = from_id,
    to_label = to_row$raw_label,
    to_clid = to_id,
    relation = relation,
    distance = distance,
    common_ancestor = common_ancestor
  )
}

candidate_display_label <- function(row) {
  if (!is.na(row$cl_label) && nzchar(row$cl_label)) return(row$cl_label)
  row$raw_label
}

empty_candidate_table <- function(candidate_table) {
  candidate_table[0, , drop = FALSE]
}

bind_candidate_rows <- function(candidate_table, rows) {
  valid <- Filter(function(item) !is.null(item) && nrow(item) > 0, rows)
  if (length(valid) == 0) return(empty_candidate_table(candidate_table))
  out <- do.call(rbind, valid)
  rownames(out) <- NULL
  out
}

sort_candidate_rows <- function(candidate_table) {
  if (nrow(candidate_table) == 0) return(candidate_table)
  sort_percentages <- ifelse(is.na(candidate_table$percentage), Inf, -candidate_table$percentage)
  labels <- tolower(ifelse(is.na(candidate_table$cl_label), candidate_table$raw_label, candidate_table$cl_label))
  ord <- order(sort_percentages, labels, na.last = TRUE)
  candidate_table[ord, , drop = FALSE]
}

dedupe_candidate_rows <- function(candidate_table) {
  if (nrow(candidate_table) == 0) return(candidate_table)
  dedupe_key <- ifelse(
    !is.na(candidate_table$clid) & nzchar(candidate_table$clid),
    paste0("clid:", candidate_table$clid),
    paste0("label:", tolower(candidate_table$raw_label))
  )
  keep <- !duplicated(dedupe_key)
  candidate_table[keep, , drop = FALSE]
}

build_ontology_candidate_row <- function(clid, source) {
  label <- if (clid %in% names(cl$name)) cl$name[[clid]] else clid
  cleaned <- clean_and_match_annotation(label)
  data.frame(
    raw_label = label,
    percentage = NA_real_,
    source = source,
    matched_variant = label,
    cleaned_label = cleaned,
    clid = clid,
    cl_label = label,
    mapping_status = "ontology_neighbor",
    stringsAsFactors = FALSE
  )
}

informative_parent_ids <- function(clid) {
  parent_ids <- unique(get_cl_direct_parents(clid, cl))
  parent_ids <- parent_ids[grepl("^CL:", parent_ids)]
  if (length(parent_ids) == 0) return(character(0))
  depths <- get_cl_node_depths(parent_ids, graph)
  informative_mask <- vapply(
    seq_along(parent_ids),
    function(i) {
      is_informative_ancestor(parent_ids[[i]], cl$name[[parent_ids[[i]]]] %||% NA_character_, as.numeric(depths[[parent_ids[[i]]]]))
    },
    logical(1)
  )
  parent_ids[informative_mask]
}

get_self_row <- function(candidate_table) {
  assigned <- candidate_table[candidate_table$source == "assigned" & !is.na(candidate_table$clid), , drop = FALSE]
  if (nrow(assigned) > 0) return(sort_candidate_rows(assigned)[1, , drop = FALSE])
  mapped <- candidate_table[!is.na(candidate_table$clid), , drop = FALSE]
  if (nrow(mapped) > 0) return(sort_candidate_rows(mapped)[1, , drop = FALSE])
  NULL
}

select_parent_rows <- function(self_row, candidate_table, limit = 1) {
  parent_ids <- informative_parent_ids(self_row$clid[[1]])
  if (length(parent_ids) == 0) return(empty_candidate_table(candidate_table))

  observed <- sort_candidate_rows(candidate_table[!is.na(candidate_table$clid) & candidate_table$clid %in% parent_ids, , drop = FALSE])
  observed_ids <- unique(observed$clid)
  remaining_ids <- setdiff(parent_ids, observed_ids)
  if (length(remaining_ids) > 0) {
    remaining_labels <- vapply(remaining_ids, function(id) cl$name[[id]] %||% id, character(1))
    remaining_ids <- remaining_ids[order(tolower(remaining_labels))]
  }
  selected_ids <- c(observed_ids, remaining_ids)
  if (length(selected_ids) == 0) return(empty_candidate_table(candidate_table))
  selected_ids <- selected_ids[seq_len(min(limit, length(selected_ids)))]

  rows <- lapply(selected_ids, function(id) {
    observed_row <- observed[observed$clid == id, , drop = FALSE]
    if (nrow(observed_row) > 0) {
      observed_row[1, , drop = FALSE]
    } else {
      build_ontology_candidate_row(id, "policy_parent")
    }
  })
  dedupe_candidate_rows(bind_candidate_rows(candidate_table, rows))
}

select_sibling_rows <- function(self_row, candidate_table, limit = 2) {
  parent_ids <- informative_parent_ids(self_row$clid[[1]])
  if (length(parent_ids) == 0) return(empty_candidate_table(candidate_table))

  sibling_ids <- unique(unlist(lapply(parent_ids, function(id) get_cl_direct_children(id, cl)), use.names = FALSE))
  sibling_ids <- sibling_ids[grepl("^CL:", sibling_ids)]
  sibling_ids <- setdiff(sibling_ids, self_row$clid[[1]])
  if (length(sibling_ids) == 0) return(empty_candidate_table(candidate_table))

  observed <- sort_candidate_rows(candidate_table[!is.na(candidate_table$clid) & candidate_table$clid %in% sibling_ids, , drop = FALSE])
  observed_ids <- unique(observed$clid)
  remaining_ids <- setdiff(sibling_ids, observed_ids)
  if (length(remaining_ids) > 0) {
    remaining_labels <- vapply(remaining_ids, function(id) cl$name[[id]] %||% id, character(1))
    remaining_ids <- remaining_ids[order(tolower(remaining_labels))]
  }
  selected_ids <- c(observed_ids, remaining_ids)
  if (length(selected_ids) == 0) return(empty_candidate_table(candidate_table))
  selected_ids <- selected_ids[seq_len(min(limit, length(selected_ids)))]

  rows <- lapply(selected_ids, function(id) {
    observed_row <- observed[observed$clid == id, , drop = FALSE]
    if (nrow(observed_row) > 0) {
      observed_row[1, , drop = FALSE]
    } else {
      build_ontology_candidate_row(id, "policy_sibling")
    }
  })
  dedupe_candidate_rows(bind_candidate_rows(candidate_table, rows))
}

select_child_rows <- function(self_row, candidate_table, limit = 2) {
  child_ids <- unique(get_cl_direct_children(self_row$clid[[1]], cl))
  child_ids <- child_ids[grepl("^CL:", child_ids)]
  if (length(child_ids) == 0) return(empty_candidate_table(candidate_table))

  observed <- sort_candidate_rows(candidate_table[!is.na(candidate_table$clid) & candidate_table$clid %in% child_ids, , drop = FALSE])
  observed_ids <- unique(observed$clid)
  remaining_ids <- setdiff(child_ids, observed_ids)
  if (length(remaining_ids) > 0) {
    remaining_labels <- vapply(remaining_ids, function(id) cl$name[[id]] %||% id, character(1))
    remaining_ids <- remaining_ids[order(tolower(remaining_labels))]
  }
  selected_ids <- c(observed_ids, remaining_ids)
  if (length(selected_ids) == 0) return(empty_candidate_table(candidate_table))
  selected_ids <- selected_ids[seq_len(min(limit, length(selected_ids)))]

  rows <- lapply(selected_ids, function(id) {
    observed_row <- observed[observed$clid == id, , drop = FALSE]
    if (nrow(observed_row) > 0) {
      observed_row[1, , drop = FALSE]
    } else {
      build_ontology_candidate_row(id, "policy_child")
    }
  })
  dedupe_candidate_rows(bind_candidate_rows(candidate_table, rows))
}

build_focus_candidate_table <- function(candidate_table, granularity) {
  mapped_rows <- sort_candidate_rows(candidate_table[!is.na(candidate_table$clid), , drop = FALSE])
  self_row <- get_self_row(candidate_table)
  observed_rows <- sort_candidate_rows(candidate_table)

  if (nrow(observed_rows) >= 2) {
    observed_rows <- observed_rows[seq_len(min(5, nrow(observed_rows))), , drop = FALSE]
    return(list(
      table = dedupe_candidate_rows(observed_rows),
      strategy = "observed_raw",
      self_label = if (!is.null(self_row)) candidate_display_label(self_row) else candidate_display_label(observed_rows[1, , drop = FALSE])
    ))
  }

  if (is.null(self_row)) {
    return(list(
      table = empty_candidate_table(candidate_table),
      strategy = "no_mapped_self",
      self_label = NA_character_
    ))
  }

  if (granularity == "coarse") {
    parent_rows <- select_parent_rows(self_row, candidate_table, limit = 1)
    focus <- dedupe_candidate_rows(bind_candidate_rows(candidate_table, list(parent_rows, self_row)))
    return(list(
      table = focus,
      strategy = "parent+self",
      self_label = candidate_display_label(self_row)
    ))
  }

  if (granularity == "fine") {
    child_rows <- select_child_rows(self_row, candidate_table, limit = 2)
    focus <- dedupe_candidate_rows(bind_candidate_rows(candidate_table, list(self_row, child_rows)))
    return(list(
      table = focus,
      strategy = "self+child",
      self_label = candidate_display_label(self_row)
    ))
  }

  observed_focus <- mapped_rows
  if (nrow(observed_focus) >= 2) {
    observed_focus <- observed_focus[seq_len(min(3, nrow(observed_focus))), , drop = FALSE]
    return(list(
      table = dedupe_candidate_rows(observed_focus),
      strategy = "observed",
      self_label = candidate_display_label(self_row)
    ))
  }

  sibling_rows <- select_sibling_rows(self_row, candidate_table, limit = 2)
  focus <- dedupe_candidate_rows(bind_candidate_rows(candidate_table, list(self_row, sibling_rows)))
  list(
    table = focus,
    strategy = "observed+sibling",
    self_label = candidate_display_label(self_row)
  )
}

is_informative_ancestor <- function(clid, label, depth) {
  if (is.na(clid) || is.na(depth) || depth < ancestor_min_depth) return(FALSE)
  label_norm <- tolower(trimws(label %||% ""))
  if (!nzchar(label_norm)) return(FALSE)
  !label_norm %in% excluded_ancestor_labels
}

common_ancestor_candidates <- function(candidate_table) {
  clids <- unique(candidate_table$clid[!is.na(candidate_table$clid)])
  if (length(clids) < 2) {
    return(list(all = list(), informative = list(), selected = NULL))
  }

  ancestor_sets <- lapply(clids, function(clid) unique(c(clid, ancestor_type_map[[clid]])))
  common_ids <- Reduce(intersect, ancestor_sets)
  common_ids <- common_ids[grepl("^CL:", common_ids)]
  if (length(common_ids) == 0) {
    return(list(all = list(), informative = list(), selected = NULL))
  }

  depths <- get_cl_node_depths(common_ids, graph)
  depth_values <- as.numeric(depths[common_ids])

  candidates <- lapply(seq_along(common_ids), function(i) {
    clid <- common_ids[[i]]
    label <- if (clid %in% names(cl$name)) cl$name[[clid]] else NA_character_
    depth <- depth_values[[i]]
    list(
      clid = clid,
      label = label,
      depth = depth,
      informative = is_informative_ancestor(clid, label, depth)
    )
  })

  informative <- Filter(function(item) isTRUE(item$informative), candidates)
  selected <- NULL
  if (length(informative) > 0) {
    informative_depths <- vapply(informative, function(item) item$depth, numeric(1))
    selected <- informative[[which.max(informative_depths)]]
  }

  list(
    all = candidates,
    informative = informative,
    selected = selected
  )
}

infer_relation_mode <- function(candidate_table, pairwise_unique) {
  mapped_mask <- !is.na(candidate_table$clid)
  mapped_count <- sum(mapped_mask)
  unmapped_count <- sum(!mapped_mask)

  if (nrow(candidate_table) <= 1) return("single_candidate")
  if (mapped_count == 0) return("all_unmapped")
  if (mapped_count == 1) {
    if (unmapped_count > 0) return("one_mapped_with_unmapped")
    return("single_mapped_candidate")
  }

  mapped_relations <- vapply(pairwise_unique, function(item) item$relation, character(1))
  cross_branch <- any(mapped_relations %in% c("sibling", "no_match"))
  same_branch <- length(mapped_relations) > 0 && all(mapped_relations %in% c("exact", "parent", "child"))

  if (cross_branch && unmapped_count > 0) return("cross_branch_with_unmapped")
  if (cross_branch) return("cross_branch")
  if (same_branch && unmapped_count > 0) return("same_branch_with_unmapped")
  if (same_branch) return("same_branch")
  if (unmapped_count > 0) return("mixed_with_unmapped")
  "mixed_mapped"
}

build_comparison_brief <- function(packet, candidate_table, granularity) {
  observed_unmapped_rows <- candidate_table[is.na(candidate_table$clid), , drop = FALSE]
  focus_result <- build_focus_candidate_table(candidate_table, granularity)
  focus_rows <- focus_result$table

  focus_pairwise <- list()
  if (nrow(focus_rows) > 1) {
    for (i in seq_len(nrow(focus_rows) - 1)) {
      for (j in seq((i + 1), nrow(focus_rows))) {
        if (is.na(focus_rows$clid[[i]]) || is.na(focus_rows$clid[[j]])) next
        focus_pairwise[[length(focus_pairwise) + 1]] <- pair_relation(focus_rows[i, ], focus_rows[j, ])
      }
    }
  }

  focus_ancestors <- common_ancestor_candidates(focus_rows)
  relation_mode <- infer_relation_mode(focus_rows, focus_pairwise)
  mapped_candidates <- if (nrow(focus_rows) > 0) {
    vapply(seq_len(nrow(focus_rows)), function(i) candidate_display_label(focus_rows[i, ]), character(1))
  } else {
    character(0)
  }
  unmapped_candidates <- observed_unmapped_rows$raw_label
  informative_ancestor <- focus_ancestors$selected$label %||% NULL
  focus_text <- paste(mapped_candidates, collapse = " vs ")

  question <- if (nrow(focus_rows) < 2) {
    sprintf(
      "No ontology comparison needed under %s policy. Keep '%s' unless marker evidence strongly argues otherwise.%s",
      granularity,
      focus_result$self_label %||% packet$summary$assigned_label,
      if (length(unmapped_candidates) > 0) {
        paste0(" Unmapped surfaced labels for context only: ", paste(unmapped_candidates, collapse = ", "), ".")
      } else ""
    )
  } else if (focus_result$strategy == "observed_raw") {
    sprintf(
      "Compare annotation candidates surfaced across GPTAnno runs: %s. Some candidates may not map to Cell Ontology; do not add parent or sibling ontology labels for unmapped candidates. Choose the label best supported by the cluster markers and reference evidence.",
      focus_text
    )
  } else if (focus_result$strategy == "parent+self") {
    sprintf(
      "Under coarse policy, compare the current ontology label against its nearest informative parent: %s. Decide whether the cluster should stay at the current term or collapse upward.%s",
      focus_text,
      if (length(unmapped_candidates) > 0) {
        paste0(" Unmapped surfaced labels are context only: ", paste(unmapped_candidates, collapse = ", "), ".")
      } else ""
    )
  } else if (focus_result$strategy == "self+child") {
    sprintf(
      "Under fine policy, compare the current ontology label against nearby child terms: %s. Decide whether the markers justify moving to a more specific child term.%s",
      focus_text,
      if (length(unmapped_candidates) > 0) {
        paste0(" Unmapped surfaced labels are context only: ", paste(unmapped_candidates, collapse = ", "), ".")
      } else ""
    )
  } else {
    sprintf(
      "Under balanced policy, compare ontology candidates %s%s. Choose the most supported ontology term and use unmapped surfaced labels only as context if needed.%s",
      focus_text,
      if (!is.null(informative_ancestor)) paste0(" under shared ancestor '", informative_ancestor, "'") else "",
      if (length(unmapped_candidates) > 0) {
        paste0(" Unmapped surfaced labels: ", paste(unmapped_candidates, collapse = ", "), ".")
      } else ""
    )
  }

  list(
    needs_llm_compare = nrow(focus_rows) >= 2,
    policy_granularity = granularity,
    focus_strategy = focus_result$strategy,
    relation_mode = relation_mode,
    mapped_candidates = unname(mapped_candidates),
    unmapped_candidates = unname(unmapped_candidates),
    focus_candidates = unname(mapped_candidates),
    informative_shared_ancestor = informative_ancestor,
    llm_question = question,
    focus_consensus_ancestor = focus_ancestors$selected,
    focus_rows = focus_rows
  )
}

summary_rows <- list()
index_entries <- list()

for (packet_entry in review_index$packets) {
  packet <- jsonlite::fromJSON(packet_entry$packet_json, simplifyVector = FALSE)
  cluster_id <- packet$cluster_id
  candidate_table <- candidate_df(packet)
  comparison_brief <- build_comparison_brief(packet, candidate_table, policy_granularity)
  relation_candidate_table <- dedupe_candidate_rows(bind_candidate_rows(candidate_table, list(comparison_brief$focus_rows)))

  candidate_payload <- lapply(seq_len(nrow(relation_candidate_table)), function(i) {
    row <- relation_candidate_table[i, ]
    list(
      raw_label = row$raw_label,
      matched_variant = if (is.na(row$matched_variant)) NULL else row$matched_variant,
      cleaned_label = row$cleaned_label,
      percentage = if (is.na(row$percentage)) NULL else as.numeric(row$percentage),
      source = row$source,
      mapping_status = row$mapping_status,
      clid = row$clid,
      cl_label = row$cl_label
    )
  })
  comparison_brief$focus_rows <- NULL
  relation_payload <- list(
    packet_version = "0.1",
    generated_at = format(Sys.time(), "%Y-%m-%dT%H:%M:%SZ", tz = "UTC"),
    project_name = spec$project_name,
    run_id = spec$run_id,
    cluster_id = cluster_id,
    current_label = packet$summary$assigned_label,
    candidates = candidate_payload,
    consensus_ancestor = comparison_brief$focus_consensus_ancestor,
    comparison_brief = comparison_brief
  )

  relation_path <- file.path(relations_dir, paste0("cluster-", cluster_id, ".json"))
  jsonlite::write_json(relation_payload, relation_path, auto_unbox = TRUE, pretty = TRUE, null = "null")
  summary_rows[[length(summary_rows) + 1]] <- data.frame(
    cluster_id = cluster_id,
    current_label = packet$summary$assigned_label,
    policy_granularity = comparison_brief$policy_granularity,
    focus_strategy = comparison_brief$focus_strategy,
    needs_llm_compare = tolower(as.character(comparison_brief$needs_llm_compare)),
    relation_mode = comparison_brief$relation_mode,
    mapped_candidates = paste(comparison_brief$mapped_candidates, collapse = " | "),
    unmapped_candidates = paste(comparison_brief$unmapped_candidates, collapse = " | "),
    focus_candidates = paste(comparison_brief$focus_candidates, collapse = " | "),
    consensus_ancestor = if (!is.null(comparison_brief$focus_consensus_ancestor)) comparison_brief$focus_consensus_ancestor$label else "",
    llm_question = comparison_brief$llm_question,
    relation_json = normalizePath(relation_path, winslash = "/", mustWork = FALSE),
    stringsAsFactors = FALSE
  )

  index_entries[[length(index_entries) + 1]] <- list(
    cluster_id = cluster_id,
    label = packet$summary$assigned_label,
    policy_granularity = comparison_brief$policy_granularity,
    focus_strategy = comparison_brief$focus_strategy,
    needs_llm_compare = comparison_brief$needs_llm_compare,
    relation_mode = comparison_brief$relation_mode,
    consensus_ancestor = comparison_brief$informative_shared_ancestor,
    relation_json = normalizePath(relation_path, winslash = "/", mustWork = FALSE),
    relation_uri = paste0("file://", normalizePath(relation_path, winslash = "/", mustWork = FALSE))
  )
}

summary_df <- do.call(rbind, summary_rows)
summary_csv <- file.path(spec$output_dir, "summary.csv")
utils::write.csv(summary_df, summary_csv, row.names = FALSE)

index_payload <- list(
  packet_version = "0.1",
  generated_at = format(Sys.time(), "%Y-%m-%dT%H:%M:%SZ", tz = "UTC"),
  project_name = spec$project_name,
  run_id = spec$run_id,
  policy = policy,
  ontology_path = normalizePath(ontology_path, winslash = "/", mustWork = FALSE),
  ancestor_min_depth = ancestor_min_depth,
  excluded_ancestor_labels = excluded_ancestor_labels,
  summary_csv = normalizePath(summary_csv, winslash = "/", mustWork = FALSE),
  relations = index_entries
)

index_json <- file.path(spec$output_dir, "index.json")
jsonlite::write_json(index_payload, index_json, auto_unbox = TRUE, pretty = TRUE, null = "null")

outputs <- list(
  output_dir = normalizePath(spec$output_dir, winslash = "/", mustWork = FALSE),
  relations_dir = normalizePath(relations_dir, winslash = "/", mustWork = FALSE),
  index_json = normalizePath(index_json, winslash = "/", mustWork = FALSE),
  summary_csv = normalizePath(summary_csv, winslash = "/", mustWork = FALSE),
  ontology_path = normalizePath(ontology_path, winslash = "/", mustWork = FALSE),
  relation_count = length(index_entries),
  relations = vapply(index_entries, function(x) x$relation_json, character(1))
)
jsonlite::write_json(outputs, spec$outputs_json, auto_unbox = TRUE, pretty = TRUE, null = "null")
