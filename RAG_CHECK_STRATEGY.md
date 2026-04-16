# RAG Check 策略与实现详解

## 目录
1. [RAG Check 整体架构](#1-rag-check-整体架构)
2. [ontology_relations 阶段](#2-ontology_relations-阶段构建本体论关系)
3. [llm_compare 阶段](#3-llm_compare-阶段llm驱动的标签判断)
4. [核心策略](#4-几个关键-strategy)
5. [数据流和决策树](#5-数据流和决策树)
6. [关键文件参考](#6-代码中的关键文件)
7. [PDAC_sn 示例](#7-pdac_sn-示例输出)

---

## 1. RAG Check 整体架构

RAG (Retrieval-Augmented Generation) Check是OntoAnno中**核心的验证阶段**，将数据驱动的本体论推理与LLM的语义理解能力结合，对细胞类型注释进行二次验证。

### 完整管道流程

```
review_packets (14 clusters with markers)
       ↓
ontology_relations (本体映射 + reference DB查询)
       ↓
llm_compare (LLM辅助决策)
       ↓
controller (整合结果)
       ↓
reviewed_parent (最终注释)
```

### 核心理念

- **不过度信任单一证据来源**：dataset markers + reference DB + LLM见解三者平衡
- **显式的不确定性处理**：ambiguous cases 标记为 `review`，交由人工判断
- **本体论约束**：所有决策受Cell Ontology结构约束，避免不一致

---

## 2. ontology_relations 阶段：构建本体论关系

### 工作流程

从 `export_ontology_relations.R` 开始的完整信息提取：

```
Input: review_packets/index.json (14个cluster packets)
       ↓
[1] Parse candidates: 从注释中提取多候选 + 置信度
       ↓
[2] Map to Cell Ontology (CL): 每个候选→CL术语
       ↓
[3] Find consensus ancestor: 共同的高级分类
       ↓
[4] Load reference DBs: PanglaoDB + CellMarker (species-filtered)
       ↓
[5] Extract cluster markers: 从expression数据
       ↓
[6] Build reference_compare: 候选vs reference的overlap分析
       ↓
Output: relations/cluster-*.json (完整的ontology context)
```

### 2.1 候选标签解析

**输入形式**（来自注释工作流）：
```
"T cell 50%, Immune cell 30%, Helper cell 20%"
```

**规范化过程** (`parse_other_annotations`):
```
Split by comma → Extract labels and percentages
  ├─ Normalize variants (remove state prefixes: activated, cycling, etc.)
  ├─ Map to preferred labels
  └─ Sort by percentage
```

**输出**：
```json
[
  {"raw_label": "T cell", "percentage": 50.0},
  {"raw_label": "Immune cell", "percentage": 30.0},
  {"raw_label": "Helper cell", "percentage": 20.0}
]
```

### 2.2 本体论映射与关系构建

**单个候选的映射过程**:

```python
For each raw_label:
  ├─ Query Cell Ontology
  ├─ Find exact matches
  ├─ Try variant matches (singular/plural, abbreviations)
  ├─ Assign mapping_status:
  │   ├─ "exact_match"
  │   ├─ "variant_match"
  │   └─ "no_match"
  ├─ Retrieve CL ID (e.g., CL:0000084 for T cell)
  └─ Store in candidates array
```

**祖先关系发现**:

```
For cluster's focus_candidates:
  ├─ Find all ancestors in ontology graph
  ├─ Filter by min_depth (default: 6)
  ├─ Exclude generic ancestors:
  │   ├─ "cell", "native cell", "eukaryotic cell"
  │   ├─ "leukocyte", "mononuclear leukocyte"
  │   └─ (defined in DEFAULT_EXCLUDED_ANCESTOR_LABELS)
  └─ Compute consensus ancestor:
      └─ Most specific common parent → "consensus_ancestor"
```

**Example**:
```
Candidates: ["CD8 T cell", "Cytotoxic T cell", "Effector T cell"]
  ↓
Consensus ancestor: "T cell" (CL:0000084)
```

### 2.3 Reference Database 查询

#### **PanglaoDB 处理**

```python
def _load_panglaodb(path, dataset_species):
    # 1. Species filtering (Mm, Hs, Mm Hs)
    # 2. Cell type grouping
    # 3. Gene ranking by:
    #    - Canonical marker status (1点)
    #    - Species match quality (2点)
    #    - Specificity (0-1)
    #    - Sensitivity (0-1)
    #    - Ubiquitousness (低为好)
    # 4. Extract top 15 markers
    # 5. Mark canonical markers (top 10)
```

**输出结构**:
```json
{
  "T cell": {
    "display_label": "T cell",
    "top_markers": ["CD3D", "CD3E", "CD3G", ...],
    "canonical_markers": ["CD3D", "CD3E"],
    "species": ["Hs"],
    "organs": ["immune system", "lymphoid tissue"],
    "rows": [...raw_data...]
  }
}
```

#### **CellMarker 处理**

```python
def _load_cellmarker(path, dataset_species):
    # 1. Load from Excel sheet "All"
    # 2. Species-specific filtering
    # 3. Cell type grouping
    # 4. Extract:
    #    - Top markers (by frequency)
    #    - Cell Ontology IDs (CLIDs)
    #    - Tissue types
    #    - PMID count (论文支撑)
    # 5. Top 15 markers, canonical top 10
```

**输出结构**:
```json
{
  "T cell": {
    "display_label": "T cell",
    "top_markers": ["CD3D", "CD3E", "CD2", ...],
    "canonical_markers": ["CD3D", "CD3E"],
    "clids": ["CL:0000084"],
    "paper_count": 156,
    "organs": ["immune system", "blood"]
  }
}
```

### 2.4 候选与Reference Matching

对每个focus candidate：

```python
def _select_reference_entry(candidate, reference_groups):
    # 1. Normalize candidate name
    # 2. Find candidate aliases
    # 3. Search reference_groups for matches:
    #    ├─ Exact normalized match (highest priority)
    #    ├─ Generic preference (prefer non-parenthetical)
    #    └─ Name length as tiebreaker
    # 4. Return best match or None
```

**匹配优先级** (从高到低):
1. Exact normalized label match
2. Alias match + generic preference
3. Longest substring match
4. No match → null

### 2.5 Reference Compare Block 生成

为每个candidate生成对比块：

```json
{
  "candidate": "CD8 T cell",
  "panglaodb": {
    "reference_label": "CD8-positive T cell",
    "species": ["Hs"],
    "organs": ["immune system"],
    "canonical_markers": ["CD8A", "CD8B"],
    "top_markers": ["CD8A", "CD8B", "CD3D", ...],
    "overlap_genes": ["CD8A", "CD8B", "CD3D"],
    "overlap_count": 3
  },
  "cellmarker": {
    "reference_label": "CD8+ T cell",
    "species": ["human"],
    "canonical_markers": ["CD8A", "CD8B"],
    "overlap_genes": ["CD8A", "CD8B"],
    "overlap_count": 2,
    "clids": ["CL:0000625"],
    "paper_count": 47
  }
}
```

---

## 3. llm_compare 阶段：LLM驱动的标签判断

### 3.1 何时触发 LLM Compare

```python
needs_llm_compare 决定条件：
├─ Ontology mapping status is ambiguous
├─ Multiple candidates with conflicting evidence
├─ Reference DB matches partially overlap
├─ Policy.ontology = true 且有非mapped候选需要评估
└─ Focus strategy requires nuanced decision
```

### 3.2 LLM Query 的完整组织

#### **系统角色提示**

```
"You are a careful scRNA-seq annotation judge. 
Only compare the explicitly listed candidate labels. 
Do not invent new labels. 
Reference databases are supportive evidence, not gold standards. 
Candidate labels came from dataset-specific annotation and must be considered seriously. 
If the evidence is insufficient or internally conflicted, choose review."
```

#### **用户提示的多层结构**

**第1层：任务定义**
```
You are comparing candidate cell type annotations for one scRNA-seq cluster.

Current label: [current_label]
Cluster top markers: CD3D, CD3E, CD8A, CD8B, CD28, ...
```

**第2层：策略上下文**
```
Important: the candidate labels came from the dataset-specific annotation workflow.
Reference marker databases are supportive evidence for evidence balancing, not gold standards.

[If consensus_ancestor exists:]
Shared ontology ancestor: T cell

[If policy_granularity set:]
Policy granularity: balanced

[If focus_strategy set:]
Policy focus strategy: [value]
```

**第3层：候选列表**
```
Only choose from these candidate labels:
- CD8 T cell
- Effector T cell
- Cytotoxic T cell
- Helper T cell
```

**第4层：Reference Evidence块**

```
Reference evidence from PanglaoDB and CellMarker (supportive evidence, not a golden rule):

- CD8 T cell | PanglaoDB reference: CD8-positive T cell | 
  canonical markers: CD8A, CD8B | 
  top markers: CD8A, CD8B, CD3D, CD3E, CD28, CD7 | 
  overlap with cluster: CD8A, CD8B, CD3D, CD3E, CD28

  CellMarker reference: CD8+ T cell | 
  cell ontology ids: CL:0000625 | 
  papers: 47 | 
  markers: CD8A, CD8B, CD3D, CD28 | 
  overlap with cluster: CD8A, CD8B, CD3D, CD28

- Helper T cell | PanglaoDB reference: Helper T cell | ...
  [similar structure]

- Effector T cell | PanglaoDB reference: no hit found
  CellMarker reference: Effector T cell | ...
```

**第5层：Evidence 权衡指导**

```
Treat reference marker databases as supportive evidence only, not as a golden rule.
If user-provided marker memory is present, weigh it as researcher-curated supportive evidence.
The candidate labels were generated from this dataset's own marker context and should be weighed seriously.
Lack of overlap with reference markers does not automatically reject a candidate.
Balance three things: 
  - dataset-specific cluster markers
  - ontology-constrained candidate labels
  - reference markers

If the evidence remains mixed, ambiguous, or weak, return decision='review'.
```

**第6层：Ontology Restriction (if enabled)**

```
Ontology restriction is active: the final best_candidate must be an ontology-mapped candidate.
Allowed final candidates: CD8 T cell, Effector T cell, Cytotoxic T cell.
The following candidates are not ontology-mapped and may only be discussed as alternatives; 
if they seem better, return decision='review' instead of choosing them: Helper T cell.
```

**第7层：JSON Schema要求**

```
Return strict JSON only. Do not add markdown fences.
Use this schema: 
{
  "decision": "choose or review",
  "best_candidate": "one of [CD8 T cell, Effector T cell, ...] or null if review",
  "reason": "short explanation",
  "supporting_markers": ["markers supporting best_candidate"],
  "weakening_markers": ["markers that argue against best_candidate"],
  "reference_limitations": "short note about why reference evidence may be incomplete"
}
```

### 3.3 User Memory 增强

如果存在研究员的marker memory（历史知识库）：

```python
def _memory_evidence_block(config, focus_candidates, current_label):
    # 1. Load agent_memory (saved in resources/agent_memory.json)
    # 2. Match saved entries against focus_candidates
    # 3. If matches found, append to prompt:
    
    "User-provided marker memory (researcher-curated evidence):
    - CD8 T cell: markers=CD8A,CD8B,CD3D,... | note=primary markers for CD8+ population
    - Effector T cell: markers=GZMB,GZMA,... | note=cytotoxic granules"
```

### 3.4 Response 解析与规范化

#### **原始响应提取**

```python
def _extract_message_content(response_payload):
    # Navigate: response_payload["choices"][0]["message"]["content"]
    # Handle both string and list (multimodal) formats
```

#### **JSON 块提取**

```python
def _extract_json_block(text):
    # Find first '{' occurrence
    # Attempt JSONDecoder.raw_decode
    # Return parsed dict
```

#### **结果规范化**

```python
def _normalize_result(parsed, focus_candidates, allowed_candidates, ontology_restricted):
    normalized = {
        "decision": "choose" if decision in {"choose"} else "review",
        "best_candidate": best_candidate if decision=="choose" and valid else None,
        "reason": str(reason),
        "supporting_markers": [...],
        "weakening_markers": [...],
        "reference_limitations": str(ref_limitations)
    }
    
    # Validate:
    # 1. best_candidate must be in focus_candidates
    # 2. If ontology_restricted, must be in allowed_candidates
    # 3. If invalid → set decision="review", best_candidate=None
```

### 3.5 完整的Result JSON 输出

```json
{
  "cluster_id": "7",
  "current_label": "T cell",
  "status": "completed",
  "focus_candidates": ["CD8 T cell", "Helper T cell", "Effector T cell"],
  "allowed_candidates": ["CD8 T cell", "Effector T cell"],
  "prompt": "[full original prompt]",
  "prompt_with_schema": "[prompt + schema]",
  "memory_matches": [
    {
      "celltype": "CD8 T cell",
      "markers": ["CD8A", "CD8B", "CD3D"],
      "note": "primary CD8 markers"
    }
  ],
  "raw_response_text": "[raw LLM output]",
  "parsed_response": {
    "decision": "choose",
    "best_candidate": "CD8 T cell",
    "reason": "Strong overlap with PanglaoDB canonical markers...",
    "supporting_markers": ["CD8A", "CD8B", "CD3D", "CD28"],
    "weakening_markers": [],
    "reference_limitations": "Reference DBs may miss rare or tissue-specific variants"
  },
  "result": {
    "decision": "choose",
    "best_candidate": "CD8 T cell",
    "reason": "...",
    "supporting_markers": [...],
    "weakening_markers": [...],
    "reference_limitations": "..."
  },
  "model": "gpt-5",
  "provider": "openai",
  "generated_at": "2026-04-16T10:30:45Z",
  "relation_json": "/path/to/cluster-7.json",
  "api_response": {...raw_api_response...}
}
```

#### **Skip 情况**

如果 `prompt_ready=false`（无需LLM比较）：

```json
{
  "cluster_id": "3",
  "current_label": "Macrophage",
  "status": "skipped",
  "decision": null,
  "best_candidate": null,
  "reason": "No ontology comparison needed",
  "focus_candidates": ["Macrophage"],
  "generated_at": "2026-04-16T10:30:45Z"
}
```

---

## 4. 几个关键 Strategy

### 4.1 **Granularity Policy**：细粒度控制

```yaml
policy:
  granularity: one_of(coarse | balanced | fine)
```

| 级别 | 本体论层级 | 行为 | 适用场景 |
|------|---------|------|---------|
| **coarse** | 高级分类 (胚层/系统水平) | 倾向于更通用的祖先标签 | 快速验证，低假阳性，容错能力强 |
| **balanced** | 中等细粒度 (细胞类型族列) | 平衡精度和覆盖面 | 通常情况，推荐默认 |
| **fine** | 细化特征 (具体亚型) | 倾向于最具体的候选 | 高精度需求，亚型区分 |

**影响机制** (来自R脚本):
- `coarse`: 选择更高的祖先作为focus, 或提前收敛到generic类型
- `balanced`: 按综合评分选择
- `fine`: 优先保留具体亚型候选

### 4.2 **Ontology Restriction Policy**

```yaml
policy:
  ontology: true/false
```

**ontology = true** (推荐):
```python
mapped_candidates = [c for c in focus_candidates 
                     if c in CL_ontology]
allowed_candidates = mapped_candidates

# In LLM prompt:
"Ontology restriction is active: 
 Allowed final candidates: " + ", ".join(allowed_candidates)
"Non-mapped candidates cannot be chosen; 
 if better, return decision='review'"
```

**效果**：
- ✅ 所有决策符合本体论结构
- ✅ 避免不一致的临时命名
- ⚠️ 可能遗漏了未映射到CL的合法标签

**ontology = false**:
```python
allowed_candidates = focus_candidates  # All candidates
# LLM可自由选择任何候选
```

**效果**：
- ✅ 更灵活，可发现新的标签
- ⚠️ 需要更多人工审查
- ⚠️ 可能引入不一致

### 4.3 **Review Triggers**

```yaml
policy:
  review_tie: true      # 同等证据时触发review
  review_nomatch: true  # 没有reference匹配时触发review
```

#### **review_tie = true**

当LLM响应中：
```json
{
  "supporting_markers": ["CD8A", "CD3D"],
  "weakening_markers": ["CD4"],  // 同样权重
  "reason": "Evidence is mixed and inconclusive"
}
```

→ 强制 `decision = "review"` (人工判决)

#### **review_nomatch = true**

当候选无reference DB匹配：
```python
if all(candidate["reference_db"] is None 
       for candidate in focus_candidates):
    decision = "review"  # No evidentiary support
    reason = "No reference database evidence available"
```

### 4.4 **Fallback Strategy**

```yaml
policy:
  fallback: "up"  # 向本体论上级升级
```

**使用场景**：
- 当前candidate在CL中无直接映射
- 向上查找父级（更高层的分类）直到找到映射

**例**：
```
Query: "Rare_Subtype_XYZ"
  ├─ No CL match
  ├─ Ancestors: null_or_too_generic
  └─ Fallback up: T cell (CL:0000084)
```

**效果**：
- 保证每个candidate都有本体论锚点
- 可能会降低特异性（trade-off）

### 4.5 **Focus Candidate 选择策略**

来自R脚本的 `focus_strategy`/`relation_mode`：

| 策略 | 行为 | 用途 |
|------|------|------|
| **all_candidates** | 比较所有identified的候选 | 开放式探索，发现最优标签 |
| **top_3** | 仅保留top 3（按confidence排序） | 计算效率，快速决策 |
| **mapped_only** | 仅CLID已映射的候选 | 本体论一致性优先 |
| **consensus_variant** | 围绕consensus ancestor选择 | 聚焦于语义相近的变体 |
| **confidence_threshold** | 仅>X% confidence的候选 | 高可信度过滤 |

---

## 5. 数据流和决策树

### 完整的per-cluster处理流程

```
FOR each cluster in review_packets:
│
├─ [1] Extract cluster_id, current_label, markers
│   │   └─ markers ← expression data from review packet
│   │
├─ [2] Load candidates from annotation workflow
│   │   └─ Parse "T cell 50%, Helper 30%, ..." format
│   │
├─ [3] Map to Cell Ontology
│   │   ├─ exact_match / variant_match / no_match
│   │   ├─ Find CLIDs
│   │   └─ Compute consensus_ancestor
│   │
├─ [4] Query PanglaoDB + CellMarker (species-filtered)
│   │   ├─ Extract top 15 markers per celltype
│   │   ├─ Compute overlap with cluster markers
│   │   └─ Store canonical markers
│   │
├─ [5] Build focus_candidates
│   │   ├─ Apply focus_strategy filter
│   │   ├─ Apply granularity policy
│   │   ├─ Apply ontology restriction
│   │   └─ Deduplicate & sort
│   │
├─ [6] Build reference_compare block
│   │   ├─ Per-candidate PanglaoDB evidence
│   │   ├─ Per-candidate CellMarker evidence
│   │   └─ Generate prompt (if needs_llm_compare)
│   │
├─ [7] Decision: Skip or Query LLM?
│   │
│   ├─ IF prompt_ready=false:
│   │   │   (current_label already good match)
│   │   └─ Output: status="skipped", decision=null
│   │
│   └─ IF prompt_ready=true:
│       │
│       ├─ [8] Build LLM query
│       │   ├─ Task description
│       │   ├─ Markers & current label
│       │   ├─ Policy context (granularity, etc)
│       │   ├─ Focus candidates list
│       │   ├─ Reference evidence block
│       │   ├─ User memory (if available)
│       │   ├─ Evidence balancing guidance
│       │   ├─ Ontology constraints
│       │   └─ JSON schema
│       │
│       ├─ [9] Call OpenAI API
│       │   ├─ model: gpt-5 (from config)
│       │   ├─ timeout: 180s
│       │   └─ Extract response + parse JSON
│       │
│       ├─ [10] Normalize result
│       │   ├─ Validate: decision ∈ {choose, review}
│       │   ├─ Validate: if choose → best_candidate ∈ focus_candidates
│       │   ├─ Validate: if ontology_restricted → best_candidate ∈ allowed_candidates
│       │   ├─ Extract supporting/weakening markers
│       │   └─ Apply review_triggers (if tie or nomatch)
│       │
│       └─ [11] Output result_json
│           ├─ cluster_id, current_label
│           ├─ decision (choose/review)
│           ├─ best_candidate + reason
│           ├─ supporting/weakening markers
│           ├─ full trace (prompt, raw response, etc)
│           └─ model info + timestamp
│
└─ [12] Aggregate results
    ├─ summary.csv (per-cluster summary)
    ├─ index.json (queryable index)
    ├─ llm_compare.outputs.json (overall stats)
    └─ Analysis: completed/skipped/failed counts
```

---

## 6. 代码中的关键文件

### Python 3 Components

| 文件 | 主要函数/类 | 职责 |
|------|-----------|------|
| **ontology_relations.py** | `build_ontology_relations()` | 主入口，协调论文本体论构建 |
| | `_load_panglaodb()` | 加载+过滤PanglaoDB (species-matched) |
| | `_load_cellmarker()` | 加载+过滤CellMarker (species-matched) |
| | `_select_reference_entry()` | 为candidate匹配reference DB条目 |
| | `_build_reference_compare()` | 为一个cluster生成完整的reference对比块 |
| | `_slim_relation_payload()` | 精简relation JSON用于report |
| | `_enrich_with_reference_db()` | 添加reference evidence并更新index |
| **llm_compare.py** | `build_llm_compare()` | 主入口，协调LLM比较 |
| | `_default_system_prompt()` | LLM系统角色 |
| | `_structured_user_prompt()` | 构建有schema的用户提示 |
| | `_memory_evidence_block()` | 加载user memory并格式化 |
| | `_call_openai_chat()` | 实际API调用 |
| | `_extract_json_block()` | JSON解析 |
| | `_normalize_result()` | 结果验证+规范化 |
| **agent_memory.py** | `load_agent_memory()` | 加载研究员的marker memory |
| | `marker_memory_matches()` | 查询memory中与focus_candidates相关的条目 |

### R Components

| 文件 | 主要函数 | 职责 |
|------|--------|------|
| **export_ontology_relations.R** | `build_ontology_graph()` | Cell Ontology图构建 |
| | `normalize_candidate_variants()` | 候选名称规范化 |
| | `select_focus_candidates()` | 基于策略选择focus candidates |
| | `build_comparison_brief()` | 构建comparison_brief字段 |

---

## 7. PDAC_sn 示例输出

### 运行配置

```yaml
# pdac_sn.yaml
policy:
  ontology: true           # 启用本体论约束
  granularity: balanced    # 中等细粒度
  fallback: up            # 遇到无映射候选时向上升级
  review_tie: true        # 证据平手时触发review
  review_nomatch: true    # 无reference时触发review

annotation:
  species: human
  tissue_name: Human pancreatic ductal adenocarcinoma (PDAC) tumor
```

### 数据统计

| 指标 | 值 |
|------|-----|
| Input clusters (review_packets) | 14 |
| Reference DB enabled | PanglaoDB + CellMarker (human) |
| Policy ontology | true |
| Policy granularity | balanced |

### 示例：Cluster 7 的完整处理

**输入**：
```json
{
  "cluster_id": "7",
  "current_label": "T cell",
  "markers": ["CD3D", "CD3E", "CD8A", "CD8B", "CD28", "CD7", "TRAC", "TRBC2"]
}
```

**Ontology Relations 输出**:
```json
{
  "cluster_id": "7",
  "current_label": "T cell",
  "candidates": [
    {"raw_label": "T cell", "cl_label": "T cell", "clid": "CL:0000084"},
    {"raw_label": "CD8 T cell", "cl_label": "CD8-positive T cell", "clid": "CL:0000625"},
    {"raw_label": "Effector T cell", "cl_label": "Effector T cell", "clid": "CL:0001045"}
  ],
  "comparison_brief": {
    "focus_candidates": ["T cell", "CD8 T cell", "Effector T cell"],
    "mapped_candidates": ["T cell", "CD8 T cell", "Effector T cell"],
    "informative_shared_ancestor": true,
    "needs_llm_compare": true,
    "llm_question": "Distinguish between T cell, CD8-positive T cell, and Effector T cell..."
  },
  "consensus_ancestor": {
    "label": "T cell",
    "clid": "CL:0000084",
    "depth": 8
  },
  "reference_compare": {
    "dataset_species": "human",
    "reference_mode_enabled": true,
    "candidates": [
      {
        "candidate": "CD8 T cell",
        "panglaodb": {
          "reference_label": "CD8-positive T cell",
          "canonical_markers": ["CD8A", "CD8B"],
          "top_markers": ["CD8A", "CD8B", "CD3D", "CD3E", "CD28", "CD7"],
          "overlap_genes": ["CD8A", "CD8B", "CD3D", "CD3E", "CD28", "CD7"],
          "overlap_count": 6
        },
        "cellmarker": {
          "reference_label": "CD8+ T cell",
          "top_markers": ["CD8A", "CD8B", "CD3D", "CD28"],
          "overlap_genes": ["CD8A", "CD8B", "CD3D", "CD28"],
          "overlap_count": 4,
          "clids": ["CL:0000625"],
          "paper_count": 47
        }
      }
    ],
    "prompt_ready": true
  }
}
```

**LLM Query (简化)**:
```
You are a careful scRNA-seq annotation judge. 

Current label: T cell
Cluster top markers: CD3D, CD3E, CD8A, CD8B, CD28, CD7, TRAC, TRBC2

Important: candidate labels came from dataset-specific annotation workflow.

Only choose from these candidate labels:
- T cell
- CD8 T cell
- Effector T cell

Reference evidence from PanglaoDB and CellMarker:

- CD8 T cell | PanglaoDB: CD8-positive T cell | 
  canonical markers: CD8A, CD8B | 
  overlap with cluster: CD8A, CD8B, CD3D, CD3E, CD28, CD7 (6/6) | 
  CellMarker: CD8+ T cell | papers: 47 | 
  overlap: CD8A, CD8B, CD3D, CD28 (4/4)

[Evidence balancing guidance...]

Ontology restriction is active: final best_candidate must be mapped.
Allowed: T cell, CD8 T cell, Effector T cell.

Return JSON schema:
{
  "decision": "choose or review",
  "best_candidate": "...",
  "reason": "...",
  "supporting_markers": [...],
  "weakening_markers": [...],
  "reference_limitations": "..."
}
```

**LLM Response**:
```json
{
  "decision": "choose",
  "best_candidate": "CD8 T cell",
  "reason": "Strong concordance with CD8 T cell markers. All top markers (CD3D, CD3E, CD8A, CD8B) present. PanglaoDB overlap is 100% (6/6 markers). CellMarker also supports with high paper count (47 papers). Minor concerns about TRAC/TRBC2 but these are secondary T cell identity markers.",
  "supporting_markers": ["CD8A", "CD8B", "CD3D", "CD3E", "CD28"],
  "weakening_markers": [],
  "reference_limitations": "Reference databases may undersample tissue-specific variants or newly discovered subtypes. TRAC/TRBC2 provide additional confirmatory evidence of T cell class."
}
```

**Final Result JSON**:
```json
{
  "cluster_id": "7",
  "current_label": "T cell",
  "status": "completed",
  "decision": "choose",
  "best_candidate": "CD8 T cell",
  "reason": "Strong concordance with CD8 T cell markers...",
  "focus_candidates": ["T cell", "CD8 T cell", "Effector T cell"],
  "supporting_markers": ["CD8A", "CD8B", "CD3D", "CD3E", "CD28"],
  "weakening_markers": [],
  "reference_limitations": "Reference databases may undersample...",
  "model": "gpt-5",
  "generated_at": "2026-04-16T10:30:45Z"
}
```

**Report 显示**:
```
Cluster 7: T cell → CD8 T cell ✓
  PanglaoDB overlap: 6/6 markers
  CellMarker: 47 papers supporting CD8+ T cell
  LLM decision: choose (high confidence)
```

---

## 8. Advanced Topics

### 8.1 错误处理与Fallback

```python
# ontology_relations.py
if ontology_outputs is None:
    raise LLMCompareError("ontology_relations outputs not found...")

# llm_compare.py - Per-cluster error handling
try:
    raw_text, parsed_json, raw_response = _call_openai_chat(...)
except Exception as exc:
    payload = {
        "status": "failed",
        "error": str(exc),
        "decision": None,
        "cluster_id": cluster_id
    }
    # Still save result for auditing
```

### 8.2 性能优化

- **Caching**: ontology_relations outputs缓存，避免重复计算
- **Selective Updates**: 支持 `cluster_ids` 参数进行增量更新
- **Batch API Calls**: 可扩展为批量LLM调用（目前单个）

### 8.3 可审计性

每个result JSON包含完整追踪：
- `prompt`: 原始prompt
- `prompt_with_schema`: 带schema的prompt
- `raw_response_text`: 原始LLM输出
- `parsed_response`: 解析前的JSON
- `result`: 规范化后的最终结果
- `api_response`: 完整的API响应（含token使用）

---

## 9. 总结

RAG Check流程特点：

| 维度 | 特点 |
|------|------|
| **数据驱动** | 本体论+reference DB+dataset markers三重证据 |
| **可解释性** | 每个决策都有完整的reasoning chain |
| **灵活策略** | 通过policy配置调整行为（granularity, ontology, review triggers） |
| **人机结合** | ambiguous cases自动标记为review，不强行决策 |
| **可审计** | 完全的decision trace，便于事后分析 |
| **可扩展** | 支持新的reference DB、LLM模型、策略 |

核心理念：**不过度信任单一信息源，让多层证据民主投票，在不确定时优雅降级为人工审查**

