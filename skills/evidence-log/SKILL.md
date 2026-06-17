---
name: "anti-legacy:evidence-log"
description: >
  Render the phase evidence log WITH RECEIPTS — the honesty deliverable
  (AGENTS.md §6). For every pipeline phase it states what ACTUALLY happened,
  with COMPUTED receipts (file exists + checksum re-verified), rendered
  deterministically from manifest.json + audit.jsonl + the artifact files on
  disk. It never invents prose about what happened. Use when: "evidence log",
  "what did the pipeline actually do", "receipts", "phase evidence",
  "audit report".
---

# anti-legacy:evidence-log

This is the **honesty deliverable** (AGENTS.md §6 — "every done needs still not
done"). A status line that says "build complete" is a claim. This log answers
the harder question — *what is verifiably true, with evidence* — for every phase
of the pipeline, and it names what is **not** yet true.

## Mental model

Three on-disk sources, rendered deterministically. Nothing here is coined; the
log only reports what the manifest, the audit stream, and the files on disk
actually say:

- **manifest.json** — the pipeline's own state: `phase.{current, completed,
  blocked_reason}`, the eight `gates`, and every registered `artifacts` row
  (path, format, produced_by, status, produced_at, recorded checksum).
- **audit.jsonl** — the append-only, tamper-evident event stream
  (`phase-advanced`, `gate-signed-off`, `gate-kicked-back`,
  `artifact-registered`). Used for per-phase event counts, gate timestamps, and
  the chronological timeline.
- **the artifact files themselves** — the source of the **receipts**.

The core feature is the **receipt**. For every registered artifact the log does
not trust the manifest's recorded checksum — it **re-computes** it. It resolves
the artifact's path under `.anti-legacy/` (the SAME resolution rule
`manifest check` uses), checks the file exists, re-checksums it with
`antilegacy_core.manifest.file_checksum`, and compares. The result is one of:

- `✓ verified` — file present and checksum matches what was recorded.
- `✗ MISSING FILE` — the registered file is gone.
- `✗ CHECKSUM MISMATCH (stale/tampered)` — the file changed since registration.
- `⚠ no recorded checksum (existence only)` — file present but never checksummed.

A receipt that fails is shown as a failure. The log surfaces holes; it does not
soften them.

## When it runs & prerequisites

This is a **living report** — run it any time after `setup` (manifest init) to
see the current state, and re-run it as the pipeline progresses. It is most
useful at gate reviews and at hand-off. It reports on the WHOLE workspace, so it
has no upstream-artifact prerequisite beyond the manifest itself.

The one hard prerequisite: **`.anti-legacy/manifest.json` must exist** — the log
reports ON the manifest. With no manifest there is nothing to report, and the
script exits non-zero. `audit.jsonl` and the artifact files are optional: an
empty audit renders the timeline as "no events yet", and absent files surface as
failing receipts (which is the point).

It **registers** its output and **never advances the phase** — phase
advancement is owned by the phase skills.

## Cross-Platform Notes

Pure standard-library Python (`python3`); every path is built with `os.path`. No
shell-isms. The phase order is imported from `antilegacy_core.manifest`
(`PHASE_SEQUENCE`), never hardcoded, so this log tracks the canonical sequence on
macOS, Linux, WSL, and Windows alike.

## Parameters

- `--manifest <path>` — manifest.json (default `.anti-legacy/manifest.json`).
- `--audit <path>` — audit.jsonl (default `.anti-legacy/audit.jsonl`).
- `--no-register` — write the log but do not touch the manifest (hermetic / dry
  run).

## Steps

### Step 1 — Confirm the manifest exists

```bash
python3 .anti-legacy/run.py manifest status
```

If this errors with "Manifest not found", run `anti-legacy:setup` first. The
evidence log cannot report on a workspace that has not been initialized.

### Step 2 — Render the evidence log

```bash
python3 .anti-legacy/run.py evidence_log
```

This reads the manifest + audit + disk, computes a receipt per artifact, writes
`.anti-legacy/deliverables/evidence-log.md`, and registers
`deliverable-evidence-log` (markdown, status `draft` — it is a living report).
Add `--no-register` for a dry run.

### Step 3 — Read it and act on the receipts

```bash
python3 .anti-legacy/run.py evidence_log   # prints the written path + a receipt summary
```

The script prints a one-line summary: `Receipts: N verified / M failing (of T
registered artifact(s))`. Then open the file and check:

- **Phase evidence** — one row per phase in pipeline order, its status
  (completed / current / pending), the artifacts attributed to it, and how many
  audit events concern it.
- **Gate ledger** — all eight gates with opinion, evaluator, rationale,
  evidence ids, and whether each is a **human** or **auto** gate, plus any
  kick-backs.
- **Artifact ledger (with receipts)** — the core table; every artifact with its
  COMPUTED receipt.
- **Audit timeline** — the chronological event record.
- **Gaps** — phases with no receipt yet, artifacts failing their receipt, and
  gates still pending.

If a receipt reads `✗ MISSING FILE` or `✗ CHECKSUM MISMATCH`, that is the log
doing its job: re-produce the artifact via its owning skill and re-register it,
then re-run this log. Do not hand-edit the artifact to make the receipt pass.

## Done-gate

Before registering, the script asserts:

- `.anti-legacy/manifest.json` exists and is non-empty (else stderr + exit
  non-zero — it reports ON the manifest).
- the rendered `evidence-log.md` is written and non-empty.

If either fails, the gap is surfaced and the script stops WITHOUT registering.
On success the artifact `deliverable-evidence-log` is registered `draft` and an
`anti-legacy:artifact-registered` audit row is appended. The receipts in the
ledger are computed at render time, so the log is honest at the moment it is
produced.

## Output

- `.anti-legacy/deliverables/evidence-log.md` — the rendered log.
- Manifest artifact `deliverable-evidence-log` (fmt markdown, status `draft`,
  produced_by `anti-legacy:evidence-log`, depends_on `[]`).

## Failure cases

- **"no manifest at …"** — the workspace was never initialized, or `--manifest`
  points at the wrong path. Run `anti-legacy:setup`, or pass the right path.
- **A receipt shows `✗ MISSING FILE`** — a registered artifact's file was moved
  or deleted. Re-run the producing skill (see the artifact's `produced_by`) and
  re-register; then re-run this log.
- **A receipt shows `✗ CHECKSUM MISMATCH`** — a file changed after registration
  (stale or tampered). Re-produce + re-register via the owning skill rather than
  editing the file.
- **Phase table empty / "PHASE_SEQUENCE could not be imported"** — the
  `antilegacy_core` library could not be located. Re-run `anti-legacy:setup` so
  `run.py` resolves the bundled library, then re-run.

## Still not done (callers should not assume)

- This log **reports**; it does not fix. A failing receipt is named, not
  repaired.
- Phase→artifact attribution is **best-effort** (via each artifact's
  `produced_by` skill). Artifacts produced by a deliverable or an unmapped skill
  are listed separately, not forced into a phase.
- It is **not a gate**. It clears nothing and blocks nothing — it is evidence a
  human (or a gate reviewer) reads.
