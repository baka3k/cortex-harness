# Phase 03 — IDF-Weighted Scoring, Co-occurrence Gate & Hybrid Linking (`normalizers.py`, `link_engine.py`)

## Goal

Eliminate the 16.4-million-link combinatorial explosion caused by boilerplate identifiers (`Source`, `CODE`, `components`, `InitializeComponent`, `Frame1`, `on_submit_click`) and flat `1.0` scores, replacing it with an IDF-weighted, context-aware scoring and pruning pipeline.

## Tasks

1. **Entity Mention & Prose Token Hygiene (`normalizers.py`)**:
   - Require `entity_mentions` from the doc graph to pass `is_linkable_entity_token(token)`:
     - Reject single-word lowercase/titlecase common dictionary words (`Source`, `Code`, `Screen`, `Confirm`, `Component`, `System`, `Status`, `Header`, `Detail`, `Button`, `Label`, `Panel`, `Column`, `Record`, `Table`, `Master`, `Common`, `Update`, `Search`, `Output`, `Target`, `Option`, `Number`, `Amount`, `Format`, `String`, `Length`, `Message`, `Action`, `Method`, `Object`, `Module`, `Window`, `Dialog`, `Report`, `Folder`, `Server`, `Client`, `Section` etc.) unless the token has identifier structure (`_`, camelCase, digits, `/` route, `.` dotted) OR matches a `File` / `Class` / `Module` stem with `file_df <= 5`.
   - Expand `STOPWORDS` with WinForms / VB / .NET / Java boilerplate:
     - `initializecomponent`, `dispose`, `components`, `disposing`, `sender`, `eventargs`, `suspendlayout`, `resumelayout`, `performlayout`, `container`, `resources`, `defaultinstance`, `with_events`.
   - Deduplicate `symbol_match_keys`: only index `(short_name_key,)` and `(class_or_module_plus_name_key,)` (last 2 segments of `qualified_name`), dropping intermediate package/namespace prefix combinations.
2. **Symbol File-Frequency (`file_df`) & Co-occurrence Gate (`link_engine.py`)**:
   - Compute `file_df[match_key]` = number of distinct normalized `file_path` values containing a symbol with `match_key`.
   - **Rule A — High-Frequency Boilerplate Filter**:
     - If `symbol.label in ("Variable", "Field", "Control", "Property")` and `file_df[key] > MAX_UNSCOPED_SYMBOL_DF` (default `15`):
       - Require **Document-File Affinity (Co-occurrence)**: the link is kept **only if** the document (`doc_id` or another low-DF token `file_df <= 5` in the same document/paragraph) matches the symbol's `file_stem` or enclosing `Class`/`Module`!
       - Example: `on_submit_click` or `DEPT_CODE` appearing in 500 forms is NOT linked to all 500 forms when mentioned in `OrderSummaryForm_Spec.xlsx`; it is linked **only** to `OrderSummaryForm.vb` / `OrderSummaryForm.Designer.vb`!
   - **Rule B — Global Cap per `(paragraph, token_key)`**:
     - Even for `Function`/`Class`, if a token matches `> MAX_CANDIDATES_PER_TOKEN` (default `10`) symbols after scoring, keep only the top `10` highest-scoring candidates per paragraph.
3. **Multi-Factor Scoring (`score` in `[0.0, 1.0]`)**:
   - Replace hardcoded `score = 1.0` with a deterministic composite score (rounded to 4 decimals):
     - Base weight by target/label ($w_{\text{kind}}$):
       - `File` / `Class` / `Interface` / `Type` / `Enum` / `Endpoint`: `0.85`
       - `Function` / `Constant`: `0.80`
       - `Property` / `Control` / `Field`: `0.70`
       - `Variable`: `0.55`
     - IDF factor ($w_{\text{idf}}$):
       - $\text{idf} = \frac{1}{1 + 0.30 \ln(\max(1, \text{file\_df}))}$
     - Context Affinity Bonus ($b_{\text{affinity}}$):
       - `+0.25` when `symbol.file_stem` (or parent module name) is a substring/token-subsequence of `paragraph.doc_id` or co-occurs in the same paragraph.
       - `+0.10` when matched on `qualified_name` (`Class.Member`) rather than bare `name`.
     - Clamp final score to `[0.10, 1.00]`.
     - Set `provenance`:
       - `"exact-file-stem"` when matching a `File` stem
       - `"exact-qualified"` when matching `Class.Member`
       - `"exact-scoped"` when co-occurrence affinity bonus fired
       - `"exact-name"` for standard exact symbol match
4. **Optional Tier-3 Vector Similarity Rerank / Fallback (Qdrant)**:
   - Add an opt-in helper (enabled when Qdrant collections share embedding dimensions or via `--semantic`) that reads paragraph vectors from `{project_id}_doc` and queries `{project_id}` top-5 `function`/`class`/`file` points (`score >= 0.78`, `provenance = "semantic-vector"`).

## Files

- Modify: `link-tiny/normalizers.py`, `link-tiny/link_engine.py`, `link-tiny/tests/test_normalizers.py`, `link-tiny/tests/test_link_engine.py`

## Test Scope

Mapped acceptance criteria + size: **L** (~10 test functions).

| Behavior | Test |
| --- | --- |
| Boilerplate symbols (`InitializeComponent`, `components`, `Source`, `CODE`) across 50 files do not explode unscoped links | `test_boilerplate_df_gate` |
| High-DF field/handler (`on_submit_click`, `DEPT_CODE`) links ONLY to the file whose stem matches the doc (`OrderSummaryForm`) | `test_cooccurrence_file_affinity` |
| Unique symbol (`grid_unit_col`, `df=2`) links with high score even without doc-title match | `test_unique_symbol_preserved` |
| Symbol kind + IDF + affinity ordering ranks `File`/`Class`/`Function` in matching file above generic `Variable` in foreign file | `test_composite_score_ranking` |
| Plain English entity mention (`"Source"`, `"Screen"`) from doc graph is rejected while `"grid_unit_col"` is kept | `test_entity_mention_identifier_guard` |
