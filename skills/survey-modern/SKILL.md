---
name: "anti-legacy:survey-modern"
description: >
  RETIRED. Modern-language indexing is now handled by wicked-estate, which the
  code-graph engine indexes natively across 91 languages (Java, C#, Go,
  TypeScript, Kotlin, Scala, Python, PHP, Ruby, C/C++, Rust, and more) — the
  same `wicked-estate index` pass that captures the mainframe estate
  (COBOL/JCL/CICS/IMS/DB2). There is no longer a separate modern survey track
  and no `legacy_graph.json` to merge into. Do NOT invoke this skill: run
  `anti-legacy:survey` instead — it indexes every `source_apps` entry
  (mainframe or modern) into a wicked-estate DB and registers the deterministic
  `legacy-graph` digest as evidence.
  This stub exists only so stale references resolve to a clear redirect during
  the WF1 migration; it performs no work.
---

# anti-legacy:survey-modern (RETIRED — use anti-legacy:survey)

**This skill no longer does anything. Do not run it.**

Modern-language indexing moved into the code-graph engine. `wicked-estate index`
covers 91 languages natively — including every modern stack this skill used to
`find` + `grep` (Java, C#, Go, TypeScript, Kotlin, Scala, Python, PHP, Ruby,
C/C++, Rust, Swift, …) — in the same pass that captures the mainframe estate
(COBOL/JCL/CICS/IMS/DB2, plus the cross-language EXEC PGM / CALL edges). A
separate "modern track" that hand-rolled structure with regex and merged the
result into `legacy_graph.json` is obsolete: there is no `legacy_graph.json`
anymore, and the LLM-grep node assembly this skill performed is strictly less
accurate and less complete than the engine's tree-sitter-backed graph.

## What to do instead

Run **`anti-legacy:survey`**. For every entry in `config.json` `source_apps`
— mainframe *or* modern, no language routing — survey runs:

```
wicked-estate index <app_path> --db .anti-legacy/graphs/<app>.db
```

(one DB per source repo, so `cross-graph` can federate across multiple repos),
then writes the deterministic `wicked-estate stats` digest to
`.anti-legacy/legacy-graph.digest.txt` and registers it as the checksummed
`legacy-graph` evidence. The structure all consumers read comes from the
engine via `scripts/wicked_estate.py` (`query` / `blast-radius` / `stats` /
`cross-graph`), and business rules are extracted by `anti-legacy:extraction`
(adaptive ring-expansion crawl → resolve-or-risk annotations), not by this
skill.

| You wanted to…                                  | Do this instead                        |
| ----------------------------------------------- | -------------------------------------- |
| Survey a Java / C# / Go / TS / Python codebase  | `anti-legacy:survey` (indexes it natively) |
| Add a modern app to the code graph              | Add it to `source_apps`, run `anti-legacy:survey` |
| Read a modern node's structure                  | `scripts/wicked_estate.py` helper (`query`/`blast-radius`) |
| Extract business rules from modern source       | `anti-legacy:extraction`               |

## Why this is a stub and not a hard delete

This file is a tombstone kept in place only so that any not-yet-migrated
reference to `anti-legacy:survey-modern` resolves to this explicit redirect
instead of a dangling skill during the WF1 cutover. Once every consumer points
at `anti-legacy:survey` / `wicked-estate`, this directory can be removed
outright.
