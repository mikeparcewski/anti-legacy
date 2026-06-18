#!/usr/bin/env python3
"""evidence_log — render the phase evidence log WITH RECEIPTS.

The evidence log is the HONESTY deliverable (AGENTS.md §6). For every phase it
states what ACTUALLY happened, with VERIFIABLE receipts — not a claim of "done".
It is rendered DETERMINISTICALLY from three on-disk sources, and never invents
prose about what happened:

  - manifest.json   — REQUIRED. phase.{current, completed, blocked_reason},
                      gates{<id>: {status, evaluator, rationale, evidence_artifacts}},
                      artifacts{<id>: {path, format, produced_by, status,
                      produced_at, depends_on, checksum?}}. This deliverable
                      reports ON the manifest, so an absent manifest is fatal —
                      there is nothing to report.
  - audit.jsonl     — OPTIONAL. the append-only event stream
                      (phase-advanced, gate-signed-off, gate-kicked-back,
                      artifact-registered, ...). Used for per-phase event counts,
                      the gate ledger, and the chronological timeline.
  - evidence/ + disk — the artifact files themselves. The RECEIPT for each
                      registered artifact is COMPUTED, not claimed: the file is
                      resolved under .anti-legacy/, checked for existence, and
                      re-checksummed with manifest.file_checksum, then compared to
                      the recorded checksum.

Output: .anti-legacy/deliverables/evidence-log.md
Artifact id: deliverable-evidence-log (registered, fmt markdown, status DRAFT
because it is a LIVING report that tracks an evolving workspace, depends_on []
because it reports on the whole workspace, not one upstream artifact). NEVER
advances the phase (a deliverable registers; phase advancement is owned by the
phase skills).

The per-phase table is rendered in PHASE_SEQUENCE order — imported from
antilegacy_core.manifest (the manifest module owns the canonical phase order),
never hardcoded here.

Voice (AGENTS.md §6 / §Voice): factual; surface gaps, do not soften them. A
receipt is one of `✓ verified` | `✗ MISSING FILE` | `✗ CHECKSUM MISMATCH
(stale/tampered)` — a failing receipt is shown as a failure, never hidden. The
"Gaps" section names phases with no receipts yet, artifacts failing their
receipt, and gates still pending.

Pure standard library + antilegacy_core.deliverables (+ antilegacy_core.manifest
for the SHARED checksum / path-resolution predicate, so the receipt matches what
`manifest check` would compute). Cross-platform (macOS / Linux / WSL / Windows):
every path is built with os.path; no shell-isms.
"""
import argparse
import os
import sys

from antilegacy_core import deliverables as D
from antilegacy_core import manifest as mf

ARTIFACT_ID = "deliverable-evidence-log"
PRODUCED_BY = "anti-legacy:evidence-log"
OUTPUT_RELNAME = "evidence-log.md"

# ISS-25: the differential-equivalence (GATE_3C) report carries a golden_confidence the gate
# status alone hides. The evidence log reads it from disk and rides it ALONGSIDE the GATE_3C
# opinion so a low/medium-confidence PASS never renders as a clean proven-parity row.
GATE_3C_ID = "GATE_3C_DIFFERENTIAL"
DIFFERENTIAL_REPORT_RELPATH = os.path.join("evidence", "differential-equivalence-report.json")
# Plain-English gloss per confidence tier (the anti-oversell caveat). The inherent epistemic limit:
# a contract-expected/source-oracle PASS can never PROVE real-legacy parity — so we never hide it.
_CONFIDENCE_GLOSS = {
    "high": "captured legacy I/O (attested) — the gold standard",
    "medium": "source oracle — faithful to the legacy SOURCE, but not the live system",
    "low": "assumed behavior, not captured legacy",
    "none": "no golden corpus — parity NOT evaluated",
}

# Canonical phase order. The manifest module owns it; the contract calls it
# PHASE_SEQUENCE. Prefer that name if it ever exists, else the current symbol
# (PHASE_ENUM) — never hardcode the order here.
PHASE_SEQUENCE = tuple(
    getattr(mf, "PHASE_SEQUENCE", None) or getattr(mf, "PHASE_ENUM", ())
)

# The eight canonical gates and their producing (phase, skill). manifest owns
# this map; reuse it so the gate ledger lists every gate even when a gate row is
# absent from a template-minted manifest, and so the human/auto split is derived
# from one source. The human gates are stated explicitly (AGENTS.md / gatekeeper).
GATE_PRODUCING_PHASE = dict(getattr(mf, "GATE_PRODUCING_PHASE", {}))
HUMAN_GATES = {
    "GATE_1_DESIGN",
    "GATE_2_PLAN",
    "GATE_3B_SEMANTIC",
    "GATE_4_UAT",
}


# --------------------------------------------------------------------------- #
# produced_by (skill) -> phase mapping. Best-effort, per the spec: an artifact
# is attributed to the phase whose producing skill registered it. Built from the
# manifest's own GATE_PRODUCING_PHASE (skill -> phase) plus the known producing
# skills for the non-gate phases. Anything unmapped is attributed to "(unmapped)"
# and listed under its own row so it is never silently dropped.
# --------------------------------------------------------------------------- #
def _skill_to_phase():
    """{produced_by-skill : phase} best-effort map across the pipeline."""
    mapping = {}
    # From the manifest's gate map: each gate's producing skill -> its phase.
    for _gate, pair in GATE_PRODUCING_PHASE.items():
        if isinstance(pair, (list, tuple)) and len(pair) == 2:
            phase, skill = pair
            mapping.setdefault(skill, phase)
    # The remaining phase-producing skills not covered by the gate map. Keys are
    # the `produced_by` values the skills write (verified against the SKILL.md
    # `--produced-by` literals); values are PHASE_SEQUENCE phase ids.
    mapping.update({
        "anti-legacy:survey": "survey",
        "anti-legacy:semantic-join": "semantic-join",
        "anti-legacy:analyze": "analyze",
        "anti-legacy:graph-translator": "graph-translate",
        "anti-legacy:blueprint": "blueprint",
        "anti-legacy:test-strategy": "test-strategy",
        "anti-legacy:review-packet": "review-packet",
        "anti-legacy:planner": "planning",
        "anti-legacy:functional-tests": "functional-tests",
        "anti-legacy:swarm": "build",
        "anti-legacy:developer": "build",
        "anti-legacy:target-review": "target-review",
        "anti-legacy:semantic-validation": "semantic-validation",
        "anti-legacy:uat-crew": "uat",
        "anti-legacy:uat-reviewer": "uat",
        "anti-legacy:document": "document",
        "anti-legacy:final-review": "final-review",
        "anti-legacy:deploy": "complete",
    })
    return mapping


def _phase_of_artifact(art, skill_map):
    """The phase an artifact is attributed to, via its produced_by skill.

    Best-effort: returns the mapped phase, or None when the producing skill is
    not in the map (deliverable skills like this one are intentionally NOT mapped
    to a phase — they report on the workspace, they do not belong to a phase).
    """
    return skill_map.get((art or {}).get("produced_by"))


# --------------------------------------------------------------------------- #
# Receipts — the core feature. COMPUTED, never claimed. Reuses the SAME
# predicate manifest.py uses for `manifest check` so a receipt here matches what
# the integrity check would say: resolve the path under .anti-legacy/, check it
# exists and is a file, then re-checksum and compare to the recorded checksum.
# --------------------------------------------------------------------------- #
RECEIPT_VERIFIED = "✓ verified"
RECEIPT_MISSING = "✗ MISSING FILE"
RECEIPT_MISMATCH = "✗ CHECKSUM MISMATCH (stale/tampered)"
RECEIPT_NO_CHECKSUM = "⚠ no recorded checksum (existence only)"


def _compute_receipt(art):
    """Compute (receipt_label, ok_bool, abs_path) for one registered artifact.

    - file absent / a directory                      -> MISSING       (ok=False)
    - recorded checksum present and matches recompute -> verified      (ok=True)
    - recorded checksum present and differs           -> mismatch      (ok=False)
    - file present but no recorded checksum           -> no-checksum    (ok=True,
      existence-only; flagged so a checksum-less artifact is still visible)
    """
    full = mf._artifact_full_path(art)  # SAME resolution rule as manifest check
    abs_full = os.path.abspath(full)
    if not os.path.exists(full) or os.path.isdir(full):
        return RECEIPT_MISSING, False, abs_full
    recorded = art.get("checksum")
    if not recorded:
        return RECEIPT_NO_CHECKSUM, True, abs_full
    actual = mf.file_checksum(full)
    if actual == recorded:
        return RECEIPT_VERIFIED, True, abs_full
    return RECEIPT_MISMATCH, False, abs_full


def _short(cs, n=12):
    """First n chars of a checksum for compact display, or em-dash when absent."""
    if not cs:
        return "—"
    return (cs[:n] + "…") if len(cs) > n else cs


# --------------------------------------------------------------------------- #
# Audit indexing — group events by the phase they concern (best-effort) and keep
# the raw chronological order for the timeline. Audit rows carry an ISO timestamp
# string; lexical sort on ISO-8601 == chronological sort.
# --------------------------------------------------------------------------- #
def _event_phase(ev):
    """The phase an audit event concerns, best-effort, for per-phase counts.

    - phase-advanced      -> the 'to' phase (the phase being entered)
    - gate-signed-off     -> the gate's producing phase
    - gate-kicked-back    -> the reset_to_phase
    - artifact-registered -> the producing skill is not on the audit row, so it
                             does not contribute to a per-phase count here (the
                             artifact ledger attributes it via produced_by).
    """
    etype = (ev.get("event") or "").replace("anti-legacy:", "")
    d = ev.get("details") or {}
    if etype == "phase-advanced":
        return d.get("to")
    if etype == "gate-signed-off":
        pair = GATE_PRODUCING_PHASE.get(d.get("gate_id"))
        if isinstance(pair, (list, tuple)) and len(pair) == 2:
            return pair[0]
        return None
    if etype == "gate-kicked-back":
        return d.get("reset_to_phase")
    return None


def _audit_phase_stats(audit):
    """{phase: {'count': int, 'first': ts, 'last': ts}} from the audit stream."""
    stats = {}
    for ev in audit or []:
        phase = _event_phase(ev)
        if not phase:
            continue
        ts = ev.get("timestamp") or ""
        s = stats.setdefault(phase, {"count": 0, "first": None, "last": None})
        s["count"] += 1
        if ts:
            if s["first"] is None or ts < s["first"]:
                s["first"] = ts
            if s["last"] is None or ts > s["last"]:
                s["last"] = ts
    return stats


# --------------------------------------------------------------------------- #
# Section renderers — each returns a list[str] of Markdown lines.
# --------------------------------------------------------------------------- #
def _project_name(manifest):
    proj = manifest.get("project")
    if isinstance(proj, dict):
        return proj.get("name") or "(unnamed)"
    if isinstance(proj, str):
        return proj or "(unnamed)"
    return "(unnamed)"


def _render_header(manifest):
    phase = manifest.get("phase") if isinstance(manifest.get("phase"), dict) else {}
    current = phase.get("current") or "(unknown)"
    completed = phase.get("completed") or []
    blocked = phase.get("blocked_reason")

    md = [
        "# Evidence Log — {0}".format(_project_name(manifest)),
        "",
        "> Generated by the anti-legacy `{0}` deliverable from `manifest.json` + "
        "`audit.jsonl` + the artifact files on disk. This is the HONESTY report "
        "(AGENTS.md §6): per phase, what ACTUALLY happened, with COMPUTED "
        "receipts — not a claim of done. It is DERIVED, not hand-written; re-run "
        "the deliverable, do not edit this file.".format(PRODUCED_BY),
        "",
        "- **Project:** {0}".format(_project_name(manifest)),
        "- **Current phase:** `{0}`".format(current),
        "- **Completed phases:** {0}".format(
            ", ".join("`{0}`".format(p) for p in completed) if completed else "_none_"),
        "- **Generated at:** {0}".format(D.now_iso()),
    ]
    if blocked:
        md.append("- **Blocked reason:** {0}".format(D.md_escape(blocked)))
    md.append("")
    return md


def _render_phase_table(manifest, audit, skill_map):
    """Per-phase table in PHASE_SEQUENCE order: status | artifacts | audit stats."""
    phase = manifest.get("phase") if isinstance(manifest.get("phase"), dict) else {}
    current = phase.get("current")
    completed = set(phase.get("completed") or [])
    artifacts = D.manifest_artifacts(manifest)
    audit_stats = _audit_phase_stats(audit)

    # Artifacts grouped by attributed phase.
    arts_by_phase = {}
    for art_id, art in artifacts.items():
        ph = _phase_of_artifact(art, skill_map)
        arts_by_phase.setdefault(ph, []).append(art_id)

    md = ["## Phase evidence", "",
          "Phases in pipeline order (`antilegacy_core.manifest` PHASE_SEQUENCE). "
          "Status is derived from `phase.completed` / `phase.current`. Artifacts "
          "are attributed to a phase via their `produced_by` skill (best-effort). "
          "Audit columns count events that concern each phase.", ""]

    if not PHASE_SEQUENCE:
        md.append("_PHASE_SEQUENCE could not be imported from "
                  "antilegacy_core.manifest — cannot render the phase table._")
        md.append("")
        return md

    rows = []
    for ph in PHASE_SEQUENCE:
        if ph == current:
            status = "current"
        elif ph in completed:
            status = "completed"
        else:
            status = "pending"
        arts = sorted(arts_by_phase.get(ph, []))
        arts_cell = ", ".join("`{0}`".format(a) for a in arts) if arts else "—"
        st = audit_stats.get(ph)
        if st and st["count"]:
            window = "{0} → {1}".format(st["first"] or "?", st["last"] or "?")
            count_cell = str(st["count"])
        else:
            window = "—"
            count_cell = "0"
        rows.append([ph, status, arts_cell, count_cell, window])

    md.append(D.md_table(
        ["Phase", "Status", "Artifacts produced here", "Audit events", "First → last event"],
        rows))
    md.append("")

    # Surface artifacts whose producing skill did not map to a phase (e.g. the
    # deliverable skills) so nothing is silently dropped from the per-phase view.
    unmapped = sorted(arts_by_phase.get(None, []))
    if unmapped:
        md.append("Artifacts not attributed to a pipeline phase (produced by a "
                  "deliverable / unmapped skill): {0}.".format(
                      ", ".join("`{0}`".format(a) for a in unmapped)))
        md.append("")
    return md


def _read_differential_confidence():
    """Read GATE_3C's golden_confidence from the differential-equivalence report (ISS-25).

    Returns (golden_confidence, gate_posture, warnings) read from
    .anti-legacy/evidence/differential-equivalence-report.json, or (None, None, []) when the
    report is absent/unreadable. The report path is resolved against the workspace the SAME way
    every other artifact is (D.load_json -> _abs against the CWD/workspace), so the receipt-style
    'computed, not claimed' contract holds: this reads the on-disk report, it does not invent a
    confidence. Surfacing it keeps a low/medium PASS from reading as proven parity (issue #25).
    """
    report = D.load_json(DIFFERENTIAL_REPORT_RELPATH, default={})
    if not isinstance(report, dict) or not report:
        return None, None, []
    return (report.get("golden_confidence"),
            report.get("gate_posture"),
            report.get("warnings") or [])


def _gate3c_confidence_suffix(golden_confidence):
    """A compact ' (golden confidence: <tier> — <gloss>)' suffix for the GATE_3C opinion cell,
    or '' when there is no report. ISS-25: ride the confidence alongside the bare status."""
    if not golden_confidence:
        return ""
    gloss = _CONFIDENCE_GLOSS.get(golden_confidence, "see report")
    return " (golden confidence: {0} — {1})".format(golden_confidence, gloss)


def _render_gate_ledger(manifest, audit):
    """The eight-gate ledger: opinion, evaluator, rationale, evidence, when.

    ISS-25: the GATE_3C_DIFFERENTIAL row's opinion is annotated with the golden_confidence read
    from the differential-equivalence report on disk, e.g. 'passed (golden confidence: low —
    assumed behavior, not captured legacy)', so a low/medium-confidence PASS is never rendered as
    a clean proven-parity verdict downstream.
    """
    md = ["## Gate ledger", "",
          "All eight gates. **human** gates require a person; **auto** gates clear "
          "on evidence. Opinion + evaluator + rationale + evidence ids come from "
          "`manifest.gates` (cross-checked against the `gate-signed-off` audit "
          "events). A gate not yet decided shows `pending`.", ""]

    gates = manifest.get("gates") if isinstance(manifest.get("gates"), dict) else {}
    # Union of canonical gate ids and any present in the manifest, in a stable
    # canonical order first (GATE_PRODUCING_PHASE preserves insertion order).
    canonical = list(GATE_PRODUCING_PHASE.keys())
    extra = [g for g in gates if g not in canonical]
    all_gates = canonical + sorted(extra)

    # Index the most-recent gate-signed-off audit row per gate for the timestamp.
    signed = {}
    for ev in D.audit_events(audit, "gate-signed-off"):
        gid = (ev.get("details") or {}).get("gate_id")
        ts = ev.get("timestamp") or ""
        if gid and (gid not in signed or ts >= signed[gid]):
            signed[gid] = ts

    # ISS-25: read GATE_3C's golden_confidence once so the row can carry the caveat.
    g3c_conf, _g3c_posture, g3c_warnings = _read_differential_confidence()

    rows = []
    for gid in all_gates:
        g = gates.get(gid) or {}
        opinion = (g.get("status") or "pending")
        # ISS-25: annotate GATE_3C's opinion with the golden_confidence from the report on disk,
        # so a low/medium PASS never reads as proven parity.
        if gid == GATE_3C_ID:
            opinion = opinion + _gate3c_confidence_suffix(g3c_conf)
        kind = "human" if gid in HUMAN_GATES else "auto"
        evaluator = g.get("evaluator") or "—"
        rationale = g.get("rationale") or "—"
        ev_ids = g.get("evidence_artifacts") or []
        ev_cell = ", ".join("`{0}`".format(e) for e in ev_ids) if ev_ids else "—"
        when = g.get("evaluated_at") or signed.get(gid) or "—"
        rows.append([gid, kind, opinion, evaluator, rationale, ev_cell, when])

    md.append(D.md_table(
        ["Gate", "Type", "Opinion", "Evaluator", "Rationale", "Evidence", "When"],
        rows))
    md.append("")

    # ISS-25: spell out the GATE_3C golden-confidence caveat in prose too (the table cell is
    # terse). The inherent epistemic limit: a contract-expected / source-oracle PASS can never
    # PROVE real-legacy parity — we surface the caveat rather than pretend it away.
    if g3c_conf:
        md.append("### GATE_3C_DIFFERENTIAL — golden confidence")
        md.append("")
        md.append("Differential-equivalence parity was graded at **golden confidence: {0}** "
                  "({1}), read from `{2}`. A PASS at `low`/`medium` confidence proves the target "
                  "agrees with ASSUMED/derived behavior, **not** that it matches the real legacy — "
                  "that limit is epistemic and cannot be removed, only surfaced. Only an attested "
                  "`captured-legacy` (high-confidence) golden makes a parity verdict authoritative "
                  "(and a FAIL a hard block).".format(
                      g3c_conf, _CONFIDENCE_GLOSS.get(g3c_conf, "see report"),
                      DIFFERENTIAL_REPORT_RELPATH.replace(os.sep, "/")))
        md.append("")
        for w in g3c_warnings:
            md.append("- ⚠ {0}".format(D.md_escape(w)))
        if g3c_warnings:
            md.append("")

    # Kick-backs: surface every gate-kicked-back event — these are the honest
    # record of work that was rewound.
    kicks = D.audit_events(audit, "gate-kicked-back")
    md.append("### Kick-backs")
    md.append("")
    if not kicks:
        md.append("_No gate kick-backs recorded — no gate has been failed._")
        md.append("")
        return md
    krows = []
    for ev in kicks:
        d = ev.get("details") or {}
        krows.append([
            ev.get("timestamp") or "—",
            d.get("gate_id") or "—",
            d.get("from_phase") or "—",
            d.get("reset_to_phase") or "—",
            d.get("re_run_skill") or "—",
        ])
    md.append(D.md_table(
        ["When", "Gate", "From phase", "Reset to", "Re-run skill"], krows))
    md.append("")
    return md


def _render_artifact_ledger(manifest, receipts):
    """Every registered artifact WITH its computed receipt. The core table."""
    md = ["## Artifact ledger (with receipts)", "",
          "Every registered artifact and its **computed** receipt. The receipt is "
          "not claimed: the file is resolved under `.anti-legacy/`, checked for "
          "existence, and re-checksummed with the SAME predicate `manifest check` "
          "uses, then compared to the recorded checksum.", "",
          "Receipt legend: `{0}` · `{1}` · `{2}` · `{3}`.".format(
              RECEIPT_VERIFIED, RECEIPT_MISSING, RECEIPT_MISMATCH, RECEIPT_NO_CHECKSUM),
          ""]

    artifacts = D.manifest_artifacts(manifest)
    if not artifacts:
        md.append("_No artifacts registered in the manifest yet — nothing to "
                  "verify._")
        md.append("")
        return md

    rows = []
    for art_id in sorted(artifacts):
        art = artifacts[art_id]
        receipt, _ok, _abs = receipts[art_id]
        rows.append([
            art_id,
            art.get("path", "—"),
            art.get("format", "—"),
            art.get("produced_by", "—"),
            art.get("status", "—"),
            art.get("produced_at", "—"),
            _short(art.get("checksum")),
            receipt,
        ])
    md.append(D.md_table(
        ["Artifact", "Path", "Fmt", "Produced by", "Status", "Produced at",
         "Recorded checksum", "Receipt"],
        rows))
    md.append("")
    return md


def _render_timeline(audit):
    """Chronological event timeline: event | timestamp | key details."""
    md = ["## Audit timeline", "",
          "Chronological record from `audit.jsonl` (append-only, tamper-evident).", ""]
    if not audit:
        md.append("_No audit events recorded yet._")
        md.append("")
        return md

    # Stable chronological order; ISO-8601 timestamps sort lexically.
    ordered = sorted(audit, key=lambda e: e.get("timestamp") or "")
    rows = []
    for ev in ordered:
        etype = (ev.get("event") or "").replace("anti-legacy:", "")
        d = ev.get("details") or {}
        if etype == "phase-advanced":
            detail = "{0} → {1}".format(d.get("from", "?"), d.get("to", "?"))
        elif etype == "gate-signed-off":
            detail = "{0} = {1} (by {2})".format(
                d.get("gate_id", "?"), d.get("opinion", "?"), d.get("evaluator", "?"))
        elif etype == "gate-kicked-back":
            detail = "{0} reset {1} → {2}; re-run {3}".format(
                d.get("gate_id", "?"), d.get("from_phase", "?"),
                d.get("reset_to_phase", "?"), d.get("re_run_skill", "?"))
        elif etype == "artifact-registered":
            detail = "{0} → {1} ({2})".format(
                d.get("artifact_id", "?"), d.get("path", "?"), d.get("status", "?"))
        else:
            # Render unknown event details compactly without inventing meaning.
            detail = ", ".join("{0}={1}".format(k, v) for k, v in sorted(d.items())) or "—"
        rows.append([ev.get("timestamp") or "—", etype or "—", detail])
    md.append(D.md_table(["Timestamp", "Event", "Details"], rows))
    md.append("")
    return md


def _render_gaps(manifest, audit, skill_map, receipts):
    """Gaps (AGENTS.md §6): no-receipt phases, failing receipts, pending gates."""
    md = ["## Gaps", "",
          "What is NOT yet evidenced. Named explicitly — a clean-looking log that "
          "hides holes is not honest.", ""]

    phase = manifest.get("phase") if isinstance(manifest.get("phase"), dict) else {}
    completed = set(phase.get("completed") or [])
    current = phase.get("current")
    artifacts = D.manifest_artifacts(manifest)

    # 1. Phases that have run (completed or current) but produced no registered
    #    artifact attributed to them — i.e. no receipt to show.
    arts_phases = set()
    for art in artifacts.values():
        ph = _phase_of_artifact(art, skill_map)
        if ph:
            arts_phases.add(ph)
    ran = [p for p in PHASE_SEQUENCE if p in completed or p == current]
    no_receipt = [p for p in ran
                  if p not in arts_phases and p not in ("uninitialized", "complete")]

    md.append("### Phases with no registered artifact (no receipt yet)")
    md.append("")
    if no_receipt:
        md.append("These phases are completed or current but have no artifact "
                  "attributed to them in the manifest — there is no receipt for "
                  "their output:")
        md.append("")
        for p in no_receipt:
            md.append("- `{0}`".format(p))
    else:
        md.append("_None — every completed/current pipeline phase has at least "
                  "one registered artifact._")
    md.append("")

    # 2. Artifacts whose receipt failed (missing file or checksum mismatch).
    failing = [(aid, rc) for aid, (rc, ok, _abs) in receipts.items() if not ok]
    md.append("### Artifacts failing their receipt")
    md.append("")
    if failing:
        md.append("These registered artifacts did NOT verify — the file is "
                  "missing or its checksum no longer matches what was recorded "
                  "(stale or tampered):")
        md.append("")
        rows = [[aid, artifacts.get(aid, {}).get("path", "—"), rc]
                for aid, rc in sorted(failing)]
        md.append(D.md_table(["Artifact", "Path", "Receipt"], rows))
    else:
        md.append("_None — every registered artifact's file is present and "
                  "(where checksummed) matches._")
    md.append("")

    # 3. Gates still pending (not passed / failed / waived).
    gates = manifest.get("gates") if isinstance(manifest.get("gates"), dict) else {}
    canonical = list(GATE_PRODUCING_PHASE.keys())
    all_gates = canonical + sorted(g for g in gates if g not in canonical)
    pending = [g for g in all_gates
               if (gates.get(g) or {}).get("status", "pending")
               not in ("passed", "failed", "waived")]
    md.append("### Gates still pending")
    md.append("")
    if pending:
        md.append("These gates have not been decided yet:")
        md.append("")
        for g in pending:
            kind = "human" if g in HUMAN_GATES else "auto"
            md.append("- `{0}` ({1})".format(g, kind))
    else:
        md.append("_None — every gate has an opinion on record (passed / failed / "
                  "waived)._")
    md.append("")
    return md


# --------------------------------------------------------------------------- #
# Document assembly
# --------------------------------------------------------------------------- #
def render_evidence_log(manifest, audit):
    """Assemble the full evidence log. Returns (markdown_str, receipts_dict).

    receipts maps artifact_id -> (receipt_label, ok_bool, abs_path); it is
    returned so the CLI can print the one-line receipt summary without recomputing.
    """
    skill_map = _skill_to_phase()
    artifacts = D.manifest_artifacts(manifest)
    receipts = {aid: _compute_receipt(art) for aid, art in artifacts.items()}

    md = []
    md += _render_header(manifest)
    md += _render_phase_table(manifest, audit, skill_map)
    md += _render_gate_ledger(manifest, audit)
    md += _render_artifact_ledger(manifest, receipts)
    md += _render_timeline(audit)
    md += _render_gaps(manifest, audit, skill_map, receipts)
    return "\n".join(md), receipts


def main():
    parser = argparse.ArgumentParser(
        prog="evidence_log",
        description="Render the phase evidence log WITH RECEIPTS "
                    "(evidence-log.md) from manifest.json + audit.jsonl + the "
                    "artifact files on disk, and register it as a manifest "
                    "artifact. Receipts are COMPUTED (existence + checksum), not "
                    "claimed.",
    )
    parser.add_argument("--manifest", default=D.P_MANIFEST,
                        help="Path to manifest.json "
                             "(default: .anti-legacy/manifest.json)")
    parser.add_argument("--audit", default=D.P_AUDIT,
                        help="Path to audit.jsonl (default: .anti-legacy/audit.jsonl)")
    parser.add_argument("--no-register", action="store_true",
                        help="Write the evidence log but do not register it in "
                             "the manifest")
    args = parser.parse_args()

    # Done-gate: this deliverable reports ON the manifest, so the manifest MUST
    # exist. Without it there is nothing to report — fail loudly (AGENTS.md:
    # don't write a hollow artifact).
    manifest_abs = args.manifest if os.path.isabs(args.manifest) \
        else os.path.join(D.workspace_root(), args.manifest)
    if not os.path.exists(manifest_abs) or os.path.isdir(manifest_abs):
        print("Error: no manifest at '{0}'. The evidence log reports ON the "
              "manifest — run anti-legacy:setup (manifest init) first.".format(
                  args.manifest), file=sys.stderr)
        sys.exit(1)

    manifest = D.load_manifest(args.manifest)
    if not isinstance(manifest, dict) or not manifest:
        print("Error: manifest at '{0}' is empty or unreadable — nothing to "
              "report.".format(args.manifest), file=sys.stderr)
        sys.exit(1)
    audit = D.load_audit(args.audit)

    content, receipts = render_evidence_log(manifest, audit)

    # The .md must be non-empty before we register it.
    if not content.strip():
        print("Error: rendered evidence log is empty — not writing or "
              "registering.", file=sys.stderr)
        sys.exit(1)

    out_path = D.write_deliverable(OUTPUT_RELNAME, content)
    if not (os.path.exists(out_path) and os.path.getsize(out_path) > 0):
        print("Error: evidence log was not written to '{0}' (empty file).".format(
            out_path), file=sys.stderr)
        sys.exit(1)

    # One-line receipt summary: N verified / M failing (existence-only counts as
    # verified for the OK tally but is reported in the ledger as no-checksum).
    total = len(receipts)
    failing = sum(1 for (_rc, ok, _abs) in receipts.values() if not ok)
    verified = total - failing

    print("Evidence log written to: {0}".format(out_path))
    print("Receipts: {0} verified / {1} failing (of {2} registered artifact(s))".format(
        verified, failing, total))

    if not args.no_register:
        stored = D.register_deliverable(
            ARTIFACT_ID, out_path, PRODUCED_BY,
            fmt="markdown", status="draft", depends_on=[],
        )
        if stored:
            print("Registered artifact '{0}' -> {1}".format(ARTIFACT_ID, stored))
        else:
            print("Note: manifest absent — evidence log written but not registered "
                  "(use a workspace with .anti-legacy/manifest.json to register).")


if __name__ == "__main__":
    main()
