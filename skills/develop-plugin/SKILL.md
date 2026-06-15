---
name: "anti-legacy:develop-plugin"
description: >
  Guide the agent to update, patch, or expand the anti-legacy plugin itself
  based on recent episodic learnings, translation bugs, or semantic gaps recorded in the git-brain.
  Use when: "update the plugin", "fix parsing bug", "modify translation rule",
  "develop the plugin", "self-correct code translator based on learning".
---

# anti-legacy:develop-plugin

This skill enables the anti-legacy modernization pipeline to be self-correcting and adaptive. When validator subagents or UAT reviews identify gaps, they store them as memories in the git-brain. This skill guides the agent in modifying the plugin's code, templates, or skills to address those learnings.

## Workflow

### Step 1: Retrieve recent learnings and gaps

Query the git-brain to fetch learnings, decisions, and patterns:

```bash
python3 .anti-legacy/run.py git_brain search \
  --query "failure gap semantic-gap error bug translation" \
  --limit 10
```

Read the specific memory file using `python3 .anti-legacy/run.py git_brain read`.

### Step 2: Map the learning to the plugin component

Identify which plugin component requires modification:

- **Code-graph / indexing bugs** (missing dependencies, incorrect entities, missing
  data models, wrong SymbolId resolution, annotation not persisting):
  - The legacy code graph is now produced by **`wicked-estate`** (indexed by `survey`),
    not a local parser. There is no `graph_builder.py`. Structural gaps in the graph
    are wicked-estate concerns; surface them via the engine, not by editing a parser.
  - Modify `scripts/wicked_estate.py` (the integration helper — binary resolution,
    `index`/`query`/`blast_radius`/`stats`/`cross_graph`, and the
    `resolve_symbol_id`/`annotate`/`read_semantics`/`by_requirement` annotation path).
- **Extraction / coverage errors** (a behavior-bearing node left unaccounted, a rule
  annotated with wrong confidence/provenance, ring expansion not gathering enough
  context, coverage report miscounting the denominator):
  - Modify `skills/extraction/SKILL.md` (the adaptive ring-expansion crawl recipe that
    replaces the old graph-translator enrich flow).
  - Modify `scripts/coverage.py` (the resolved-or-flagged metric + behavior-bearing
    denominator) and the `coverage.behavior_kinds` / `crawl.*` keys in `config.json`.
- **Requirements-scaffold drafting** (the structural→functional requirements graph
  draft is wrong):
  - Modify `scripts/graph_normalizer.py` (drafts the requirements scaffold from the
    annotated graph; unchanged in WF1, no longer the code-graph parser).
- **Translation / Code Swarm errors** (incorrect target class structure, wrong types, missing imports):
  - Modify `skills/swarm/SKILL.md` (developer swarm prompt guidelines)
- **Test Strategy / Contract mismatch** (invalid test scenarios, missing rounding verification):
  - Modify `skills/test-strategy/SKILL.md` or `scripts/test_runner.py`
- **Architectural / Compliance gaps** (line-by-line translation, missing safety checks):
  - Modify `templates/anti_patterns.md` or `templates/nfrs.md`

### Step 3: Write a regression test

Before writing code fixes, add a test case that captures the bug or gap:

- For helper / annotation bugs (binary resolution, SymbolId resolution, the silent-no-op
  guard, stats-digest determinism): add an assertion to `tests/test_wicked_estate.py`.
- For extraction / coverage bugs (denominator, resolved-or-flagged state, the unaccounted
  list): add an assertion to `tests/test_coverage.py`.
- For translation/semantic validation: add an assertion to `tests/test_demo_pipeline.py` or write a dedicated test file under `tests/`.

### Step 4: Implement the fix

Modify the target files in the plugin codebase. Maintain documentation integrity and preserve unrelated docstrings or comments.

### Step 5: Verify the fix

Run the specific test and then the full test suite to guarantee compile and runtime integrity:

```bash
# Run the specific test file
python3 -m unittest tests/test_my_fix.py -v

# Run the full test suite
python3 -m unittest discover -s tests -v
```

Halt and correct if any test fails.

### Step 6: Commit the changes

Commit the fix to the git repository:

```bash
git add scripts/ skills/ templates/ tests/
git commit -m "plugin: fixed {bug_desc} based on git-brain learning {learning_id}"
```
