#!/usr/bin/env python3
"""Round-trip "done" check: compare the legacy requirements graph against the
generated target graph.

HISTORY / DESIGN (T4 fix)
-------------------------
The original version computed "done" as CLASS-NAME EXISTENCE only: it resolved
the blueprint-mapped class_name for a requirement and marked PASS iff a
component with that name existed in the target graph. It never read
business_rules / validations / error_paths, so a gate could false-pass simply
because a class was spelled correctly.

This rewrite makes "done" mean RULE-LEVEL COVERAGE:
  * each requirement's business_rules + validations + error_paths form a rule
    set R (ids parsed from either the new {id, statement} object form or the
    legacy "RULE-001: text" string form via split_item());
  * evidence comes from each target component's OPTIONAL `implemented_rules`
    array (rule-id keyed, with an evidence_strength), unioned over the bound
    class AND its dependency chain (blueprint build_order / component deps);
  * an UNCOVERED error_path id is BLOCKING -> the req can never PASS;
  * each req is classified PASS / PARTIAL / FAIL with FAIL > PARTIAL > PASS
    precedence;
  * a machine-readable `functional_comparison_report.json` (sibling of the .md,
    path == report path with a .json suffix) is emitted for GATE_3B /
    GATE_3_BUILD to consume;
  * the process exits non-zero when any req is FAIL or aggregate rule coverage
    falls below --min-coverage (default 1.0).

The Markdown report and the CLI surface stay backward-compatible (additive
columns / sections only). A `--rules-mode off` flag reproduces the exact
pre-change class-existence algorithm for any caller that still depends on it.
"""
import os
import re
import sys
import json
import argparse
from datetime import datetime, timezone

# --- coverage / evidence policy --------------------------------------------
# Below this coverage fraction a requirement is FAIL (even if no error_path is
# uncovered). Between this and 1.0 -> PARTIAL (when error_paths are covered).
MIN_FAIL_COVERAGE = 0.34

# Evidence strengths acceptable for PASS, keyed by mode. A covered rule id only
# counts toward PASS when its (max) evidence strength is in the allowed set.
_PASS_STRENGTHS = {
    "default": {"strong", "medium"},
    "strict": {"strong"},
    "lenient": {"strong", "medium", "weak"},
}

# Ordering used to pick the strongest evidence for a rule id.
_STRENGTH_RANK = {"strong": 3, "medium": 2, "weak": 1}

_ITEM_RE = re.compile(r"^((?:RULE|VAL|ERR)-\d+)\s*:\s*(.*)$", re.DOTALL)


def split_item(item):
    """Normalize a rule/validation/error_path item to (id, statement).

    Supports BOTH the new object form ({"id": "RULE-001", "statement": "..."})
    and the legacy string form ("RULE-001: text"). When a string carries no
    recognizable RULE-/VAL-/ERR- prefix, a synthetic id is returned so the item
    is still counted (it just can never be "covered" by a real evidence id).
    """
    if isinstance(item, dict):
        rid = item.get("id") or item.get("rule_id")
        statement = item.get("statement") or item.get("text") or ""
        return (rid, statement)
    if isinstance(item, str):
        m = _ITEM_RE.match(item.strip())
        if m:
            return (m.group(1), m.group(2).strip())
        return (None, item.strip())
    return (None, str(item))


def _build_rule_set(req):
    """Return ordered rule records for a requirement.

    Each record: {"id", "statement", "kind"} where kind is one of
    business_rule | validation | error_path. error_path (and, under --strict,
    validation) ids are BLOCKING for PASS.
    """
    records = []
    synthetic = 0
    for field, kind in (
        ("business_rules", "business_rule"),
        ("validations", "validation"),
        ("error_paths", "error_path"),
    ):
        for item in req.get(field, []) or []:
            rid, statement = split_item(item)
            if not rid:
                synthetic += 1
                rid = f"_SYN-{kind}-{synthetic:03d}"
            records.append({"id": rid, "statement": statement, "kind": kind})
    return records


def _component_evidence(comp):
    """Return {rule_id: strongest_strength} for one target component."""
    evidence = {}
    for ev in comp.get("implemented_rules", []) or []:
        if not isinstance(ev, dict):
            continue
        rid = ev.get("rule_id") or ev.get("id")
        if not rid:
            continue
        strength = (ev.get("evidence_strength") or "weak").lower()
        prev = evidence.get(rid)
        if prev is None or _STRENGTH_RANK.get(strength, 0) > _STRENGTH_RANK.get(prev, 0):
            evidence[rid] = strength
    return evidence


def _collect_chain_evidence(req_id, target_class, target_components, bp_mappings, bp):
    """Union implemented_rules over the bound class and its dependency chain.

    Walks blueprint component `dependencies` (req_id -> req_id), resolving each
    to its class_name, plus any direct class_name dependencies declared on the
    component itself. Returns {rule_id: strongest_strength}.
    """
    evidence = {}

    def merge(comp):
        for rid, strength in _component_evidence(comp).items():
            prev = evidence.get(rid)
            if prev is None or _STRENGTH_RANK.get(strength, 0) > _STRENGTH_RANK.get(prev, 0):
                evidence[rid] = strength

    # Map class_name -> component for collaborator lookups.
    seen_classes = set()

    def visit_class(class_name):
        if not class_name or class_name in seen_classes:
            return
        seen_classes.add(class_name)
        comp = target_components.get(class_name)
        if comp:
            merge(comp)

    # Bound class first.
    visit_class(target_class)

    # Dependency chain: follow blueprint req-level dependencies to collaborator
    # class_names, and resolve any string deps that are themselves class names.
    seen_reqs = set()

    def visit_req(rid):
        if not rid or rid in seen_reqs:
            return
        seen_reqs.add(rid)
        info = bp_mappings.get(rid)
        if not info:
            return
        visit_class(info.get("class_name"))
        for dep in info.get("dependencies", []) or []:
            if dep in bp_mappings:
                visit_req(dep)
            else:
                # dep may already be a class name
                visit_class(dep)

    visit_req(req_id)
    return evidence


class GraphComparer:
    def __init__(self, requirements_graph_path, blueprint_path, target_graph_path,
                 rules_mode="on", evidence_mode="default", min_coverage=1.0):
        self.req_graph_path = os.path.abspath(requirements_graph_path)
        self.blueprint_path = os.path.abspath(blueprint_path)
        self.target_graph_path = os.path.abspath(target_graph_path)
        self.rules_mode = rules_mode            # "on" | "off"
        self.evidence_mode = evidence_mode      # "default" | "strict" | "lenient"
        self.min_coverage = min_coverage
        # Filled by compare(); used by main() to set the exit code.
        self.exit_ok = True

    # -- load helpers --------------------------------------------------------
    def _load(self):
        with open(self.req_graph_path) as f:
            rg = json.load(f)
        with open(self.blueprint_path) as f:
            bp = json.load(f)
        with open(self.target_graph_path) as f:
            tg = json.load(f)
        return rg, bp, tg

    @staticmethod
    def _index_target(tg):
        target_components = {}
        target_entities = {}
        for d, d_data in tg.get("domains", {}).items():
            for c_name, comp in d_data.get("components", {}).items():
                target_components[c_name] = {**comp, "domain": d}
            for e_name, ent in d_data.get("entities", {}).items():
                target_entities[e_name] = {**ent, "domain": d}
        return target_components, target_entities

    @staticmethod
    def _index_blueprint(bp):
        bp_mappings = {}
        for d, d_data in bp.get("domains", {}).items():
            for req_id, comp in d_data.get("components", {}).items():
                bp_mappings[req_id] = {
                    "class_name": comp.get("class_name"),
                    "type": comp.get("type"),
                    "api": comp.get("api"),
                    "dependencies": comp.get("dependencies", []),
                    "domain": d,
                }
        return bp_mappings

    # -- per-requirement decision -------------------------------------------
    def _evaluate_req(self, req_id, req, bp_mappings, target_components, bp):
        """Return a per-requirement result dict (the row consumed by both the
        Markdown matrix and the JSON report)."""
        title = req.get("title", "")
        legacy_comps = ", ".join(req.get("legacy_components", []))

        bp_info = bp_mappings.get(req_id)
        target_class = bp_info.get("class_name") if bp_info else None

        records = _build_rule_set(req)
        rule_ids = [r["id"] for r in records]
        n = len(records)
        error_path_ids = {r["id"] for r in records if r["kind"] == "error_path"}
        validation_ids = {r["id"] for r in records if r["kind"] == "validation"}
        allowed_strengths = _PASS_STRENGTHS[self.evidence_mode]

        # --- disposition: honor an explicit DROP (the reimagine case) -------
        # A requirement the curator intentionally dropped is OUT OF SCOPE for
        # parity — it is NOT a FAIL and its rules must NOT count against
        # rule_coverage (forcing 1:1 re-implementation would defeat the
        # merge+reimagine purpose). GUARD: only a drop WITH a non-empty
        # disposition_reason is honored; a reason-less "drop" falls through to
        # normal evaluation, so a silent drop cannot launder past the gate. (ISS-01)
        disposition = (req.get("disposition") or "").strip().lower()
        disposition_reason = (req.get("disposition_reason") or "").strip()
        if disposition == "drop" and disposition_reason:
            return {
                "req_id": req_id,
                "title": title,
                "legacy_components": legacy_comps,
                "target_class": target_class or "N/A",
                "status": "DROPPED",
                "details": "Intentionally dropped (reimagine): %s" % disposition_reason,
                "coverage": 1.0,
                "covered_rule_ids": [],
                "uncovered_rule_ids": [],
                "evidence_strength_per_rule": {},
                "rule_count": n,
                "dropped": True,
                "disposition_reason": disposition_reason,
                "warn": None,
            }

        # --- legacy class-existence path (back-compat) ----------------------
        if self.rules_mode == "off":
            exists = bool(target_class and target_class in target_components)
            status = "PASS" if exists else "FAIL"
            if exists:
                tc = target_components[target_class]
                if tc.get("type") == "controller" and tc.get("endpoints"):
                    eps = [f"{e['method']} {e['path']}" for e in tc["endpoints"]]
                    details = f"Exposed REST Endpoint: {', '.join(eps)}"
                elif tc.get("type") == "batch_job" and tc.get("schedules"):
                    scheds = [s["cron"] for s in tc["schedules"]]
                    details = f"Scheduled Daily Job (Cron: {', '.join(scheds)})"
                else:
                    details = f"Implemented Service Component ({tc.get('type', 'service')})"
            else:
                details = "Missing implementation class in target codebase"
            return {
                "req_id": req_id,
                "title": title,
                "legacy_components": legacy_comps,
                "target_class": target_class or "N/A",
                "status": status,
                "details": details,
                "coverage": 1.0 if status == "PASS" else 0.0,
                "covered_rule_ids": [],
                "uncovered_rule_ids": [],
                "evidence_strength_per_rule": {},
                "rule_count": n,
                "warn": None,
            }

        # --- rule-coverage path (default) -----------------------------------
        # 1. Class binding precondition.
        if not target_class or target_class not in target_components:
            return {
                "req_id": req_id,
                "title": title,
                "legacy_components": legacy_comps,
                "target_class": target_class or "N/A",
                "status": "FAIL",
                "details": "No implementation class in target codebase",
                "coverage": 0.0,
                "covered_rule_ids": [],
                "uncovered_rule_ids": list(rule_ids),
                "evidence_strength_per_rule": {},
                "rule_count": n,
                "warn": None,
            }

        # 3. Gather evidence over the bound class + dependency chain.
        evidence = _collect_chain_evidence(
            req_id, target_class, target_components, bp_mappings, bp
        )
        rule_id_set = set(rule_ids)
        covered_strength = {
            rid: evidence[rid] for rid in rule_ids if rid in evidence
        }
        covered_ids = set(covered_strength.keys())
        uncovered_ids = rule_id_set - covered_ids

        # N == 0: requirement carries no rules -> legacy class-existence PASS,
        # but flag it so reviewers know nothing was actually verified.
        if n == 0:
            return {
                "req_id": req_id,
                "title": title,
                "legacy_components": legacy_comps,
                "target_class": target_class,
                "status": "PASS",
                "details": "Class exists; requirement carries no rules to verify",
                "coverage": 1.0,
                "covered_rule_ids": [],
                "uncovered_rule_ids": [],
                "evidence_strength_per_rule": {},
                "rule_count": 0,
                "warn": "no_rules_to_verify",
            }

        coverage = len(covered_ids) / n

        # 4. Classify (FAIL > PARTIAL > PASS).
        uncovered_error_paths = error_path_ids - covered_ids
        uncovered_validations = validation_ids - covered_ids
        # Evidence strong/medium acceptable for PASS (mode-dependent).
        weak_covered_for_pass = [
            rid for rid in covered_ids
            if covered_strength.get(rid) not in allowed_strengths
        ]

        if (
            coverage < MIN_FAIL_COVERAGE
            or uncovered_error_paths
            or (self.evidence_mode == "strict" and uncovered_validations)
        ):
            status = "FAIL"
        elif coverage < 1.0:
            # Every error_path is covered here (else FAIL above).
            status = "PARTIAL"
        else:
            # coverage == 1.0 and every error_path covered.
            if weak_covered_for_pass:
                status = "PARTIAL"
            else:
                status = "PASS"

        details = (
            f"{len(covered_ids)}/{n} rules covered"
            + (f"; uncovered: {', '.join(sorted(uncovered_ids))}" if uncovered_ids else "")
        )

        return {
            "req_id": req_id,
            "title": title,
            "legacy_components": legacy_comps,
            "target_class": target_class,
            "status": status,
            "details": details,
            "coverage": coverage,
            "covered_rule_ids": sorted(covered_ids),
            "uncovered_rule_ids": sorted(uncovered_ids),
            "evidence_strength_per_rule": covered_strength,
            "rule_count": n,
            "warn": None,
        }

    # -- main entry ----------------------------------------------------------
    def compare(self, output_md_path):
        try:
            rg, bp, tg = self._load()
        except Exception as e:
            print(f"Error loading inputs: {e}", file=sys.stderr)
            self.exit_ok = False
            return False

        target_components, _target_entities = self._index_target(tg)
        bp_mappings = self._index_blueprint(bp)

        comparison_rows = []
        for domain_id, d_data in rg.get("domains", {}).items():
            for req_id, req in d_data.get("requirements", {}).items():
                comparison_rows.append(
                    self._evaluate_req(req_id, req, bp_mappings, target_components, bp)
                )

        # Aggregates.
        total = len(comparison_rows)
        n_pass = sum(1 for r in comparison_rows if r["status"] == "PASS")
        n_partial = sum(1 for r in comparison_rows if r["status"] == "PARTIAL")
        n_fail = sum(1 for r in comparison_rows if r["status"] == "FAIL")
        # Intentionally-dropped (reimagine) reqs are out of scope: not satisfied,
        # not missing, and their rules are excluded from the coverage denominator
        # so an honest drop cannot fail the gate. (ISS-01)
        n_dropped = sum(1 for r in comparison_rows if r.get("dropped"))
        rules_dropped = sum(r["rule_count"] for r in comparison_rows if r.get("dropped"))
        total_satisfied = n_pass
        total_missing = n_partial + n_fail

        rules_total = sum(r["rule_count"] for r in comparison_rows if not r.get("dropped"))
        rules_covered = sum(len(r["covered_rule_ids"]) for r in comparison_rows)
        rule_coverage = (rules_covered / rules_total) if rules_total else 1.0
        uncovered_error_paths_total = sum(
            1 for r in comparison_rows
            for rid in r["uncovered_rule_ids"]
            if rid.startswith("ERR-") or rid.startswith("_SYN-error_path")
        )

        # --- write Markdown report (backward-compatible, additive) ----------
        os.makedirs(os.path.dirname(os.path.abspath(output_md_path)) or ".", exist_ok=True)
        with open(output_md_path, 'w') as f:
            f.write("# Parity Comparison Packet (Legacy vs Modernized Target Graph)\n\n")
            f.write(f"**Generated at**: {datetime.now(timezone.utc).isoformat()}  \n")
            f.write(f"**Legacy Requirements Evaluated**: {total}  \n")
            if total:
                rate = (total_satisfied / total) * 100
            else:
                rate = 0.0
            f.write(f"**Parity Satisfaction Rate**: **{total_satisfied} / {total}** ({rate:.1f}%)  \n")
            f.write(
                f"**Rule Coverage**: **{rules_covered} / {rules_total}** rules implemented "
                f"({rule_coverage * 100:.1f}%)  \n"
            )
            if n_dropped:
                f.write(
                    f"**Intentionally Dropped (reimagine)**: {n_dropped} requirement(s) / "
                    f"{rules_dropped} rule(s) excluded from coverage with a reason  \n"
                )
            f.write("\n")

            f.write("## 1. Overall Parity Verification Matrix\n\n")
            f.write("| Requirement ID | Title | Legacy Components | Modernized Target Class | Status | Rules Covered (n/N) | Uncovered Rule IDs | Parity details / Interfaces |\n")
            f.write("| --- | --- | --- | --- | --- | --- | --- | --- |\n")
            for row in sorted(comparison_rows, key=lambda x: x["req_id"]):
                n = row["rule_count"]
                covered_n = len(row["covered_rule_ids"])
                uncovered = ", ".join(row["uncovered_rule_ids"]) or "-"
                f.write(
                    f"| `{row['req_id']}` | {row['title']} | `{row['legacy_components']}` | "
                    f"`{row['target_class']}` | **{row['status']}** | {covered_n}/{n} | "
                    f"{uncovered} | {row['details']} |\n"
                )

            f.write("\n## 2. Bounded Context Database Schema Alignment\n\n")
            f.write("| Bounded Context Domain | Legacy Mainframe / Data File | Modernized Table Name | Columns & Field Types |\n")
            f.write("| --- | --- | --- | --- |\n")
            for d, d_data in bp.get("domains", {}).items():
                for ent_name, ent in d_data.get("entities", {}).items():
                    cols = ent.get("columns", [])
                    cols_str = ", ".join([f"`{c.get('name')}` ({c.get('type')})" for c in cols])
                    f.write(f"| {d} | `{ent_name}` | `{ent.get('table_name')}` | {cols_str} |\n")

            f.write("\n## 3. Structural Parity Verdict\n")
            verified = (
                total_missing == 0
                and rule_coverage >= 1.0
                and uncovered_error_paths_total == 0
            )
            if verified:
                f.write("> [!IMPORTANT]\n")
                f.write(
                    f"> **Verifiable Parity Satisfied**: all {total} requirements have "
                    f"functional equivalents in the target codebase, and every business "
                    f"rule, validation, and error path ({rules_covered}/{rules_total}) is "
                    f"covered by rule-level evidence.\n"
                )
            else:
                f.write("> [!WARNING]\n")
                f.write(
                    f"> **Verification Incomplete**: {n_fail} requirement(s) FAIL, "
                    f"{n_partial} PARTIAL; rule coverage {rules_covered}/{rules_total} "
                    f"({rule_coverage * 100:.1f}%); {uncovered_error_paths_total} uncovered "
                    f"error path(s). Re-dispatch the swarm to implement the uncovered "
                    f"business_rules / validations / error_paths.\n"
                )

        # --- write machine-readable JSON report (sibling of the .md) --------
        report_json_path = os.path.splitext(os.path.abspath(output_md_path))[0] + ".json"
        json_payload = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "rules_mode": self.rules_mode,
            "evidence_mode": self.evidence_mode,
            "min_coverage": self.min_coverage,
            "requirements": [
                {
                    "req_id": r["req_id"],
                    "status": r["status"],
                    "coverage": r["coverage"],
                    "covered_rule_ids": r["covered_rule_ids"],
                    "uncovered_rule_ids": r["uncovered_rule_ids"],
                    "evidence_strength_per_rule": r["evidence_strength_per_rule"],
                    "rule_count": r["rule_count"],
                    "warn": r["warn"],
                }
                for r in comparison_rows
            ],
            "aggregate": {
                "total_reqs": total,
                "pass": n_pass,
                "partial": n_partial,
                "fail": n_fail,
                # Canonical key consumed by the gatekeeper python snippet and
                # validator_discovery's M1 round-trip check; `fail` is retained
                # above purely for back-compat with any older consumer.
                "fail_count": n_fail,
                "rules_total": rules_total,
                "rules_covered": rules_covered,
                "rule_coverage": rule_coverage,
                "uncovered_error_paths": uncovered_error_paths_total,
                # Disposition-aware audit seam (ISS-01): how many reqs/rules were
                # intentionally dropped-with-reason and thus excluded above.
                "dropped_with_reason": n_dropped,
                "rules_dropped": rules_dropped,
            },
        }
        with open(report_json_path, 'w') as jf:
            json.dump(json_payload, jf, indent=2)

        # --- exit-code decision ---------------------------------------------
        if self.rules_mode == "off":
            # Legacy class-existence semantics: exit 0 iff every requirement's
            # class exists (no FAILs). Rule coverage is reported but not gated.
            self.exit_ok = (n_fail == 0)
        else:
            # Fail the run if any req is FAIL, or aggregate coverage < --min-coverage.
            self.exit_ok = (n_fail == 0) and (rule_coverage >= self.min_coverage)

        print(f"Comparison report written to {output_md_path}")
        print(f"Machine-readable report written to {report_json_path}")
        print(
            f"Reqs: {n_pass} PASS / {n_partial} PARTIAL / {n_fail} FAIL; "
            f"Rule Coverage: {rules_covered}/{rules_total} "
            f"({rule_coverage * 100:.1f}%)"
        )
        return True


def main():
    parser = argparse.ArgumentParser(description="Compares the legacy requirements graph against the target graph.")
    parser.add_argument("--requirements-graph", default=".anti-legacy/requirements/requirements_graph.json", help="Path to legacy graph")
    parser.add_argument("--blueprint", default=".anti-legacy/requirements/blueprint.json", help="Path to blueprint")
    parser.add_argument("--target-graph", default=".anti-legacy/target_graph.json", help="Path to target graph")
    parser.add_argument("--report", default=".anti-legacy/evidence/functional_comparison_report.md", help="Path to write report MD")
    parser.add_argument("--strict", action="store_true", help="Require strong evidence and 100%% coverage (and covered validations) for PASS")
    parser.add_argument("--lenient", action="store_true", help="Allow weak evidence to count toward PASS")
    parser.add_argument("--min-coverage", type=float, default=1.0, help="Aggregate rule-coverage threshold below which the run exits non-zero (default 1.0)")
    parser.add_argument("--rules-mode", choices=["on", "off"], default="on", help="'on' = rule-coverage done-check (default); 'off' = legacy class-existence behavior")
    args = parser.parse_args()

    if args.strict and args.lenient:
        parser.error("--strict and --lenient are mutually exclusive")
    evidence_mode = "strict" if args.strict else ("lenient" if args.lenient else "default")

    comparer = GraphComparer(
        args.requirements_graph,
        args.blueprint,
        args.target_graph,
        rules_mode=args.rules_mode,
        evidence_mode=evidence_mode,
        min_coverage=args.min_coverage,
    )
    success = comparer.compare(args.report)
    if not success:
        sys.exit(1)
    sys.exit(0 if comparer.exit_ok else 1)


if __name__ == "__main__":
    main()
