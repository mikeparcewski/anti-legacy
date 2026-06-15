---
name: "anti-legacy:review-packet"
description: >
  Compile everything into a single offline Markdown review packet for the team.
  Includes architecture diagrams, domain breakdowns, entity tables, requirement details,
  test strategy summary, and the GATE_1_DESIGN sign-off checklist. Designed for teams
  with only git and fileshares — no external tools required to read it.
  Use when: "compile the review packet", "create the team review document",
  "prepare for gate 1", "package the design for review".
---

# anti-legacy:review-packet

Compiles the Requirements Graph, Blueprint, and Test Strategy into a single
offline-friendly Markdown document that the team can review, annotate, and sign
off on via git. Nothing requires a running server to read.

## Cross-Platform Notes

Script uses `python3`. Output is a plain Markdown file with embedded Mermaid
diagrams — readable in any Markdown viewer, git diff, or text editor.

## Parameters

- **output** (optional): output path. Defaults to `.anti-legacy/review_packet.md`.
- **include_test_summary** (optional): `true` (default) — include test scenario count per domain.

## Step 1: Verify prerequisites

```bash
python3 .anti-legacy/run.py manifest status
```

All three prerequisite artifacts must be present (review-packet runs **after**
blueprint and test-strategy): `requirements-graph`, `blueprint-json`, `test-strategy`.

## Step 2: Compile the packet

Run the packet generator (enriched with blueprint and test strategy):

```bash
python3 .anti-legacy/run.py packet_generator \
  --input .anti-legacy/requirements/requirements_graph.json \
  --blueprint .anti-legacy/requirements/blueprint.json \
  --test-strategy .anti-legacy/contracts/test-strategy.md \
  --output .anti-legacy/review_packet.md
```

The packet generator produces:

**1. Executive Summary**
- What is being modernized (source apps, languages, line counts)
- Target stack and deployment target
- Timeline summary (phases completed, gates pending)

**2. Architecture Overview**
- Mermaid flowchart of domains, requirements, and dependencies
- Before/After comparison: legacy component → target class mapping

**3. Domain Breakdown** (one section per domain)
- Logical data entities with field mapping tables
- Requirement nodes with extracted business rules
- API surface (for online components)
- Test scenario count and coverage summary

**4. Database Schema**
- Entity relationship diagram (Mermaid ERD)
- Field type mapping table (legacy type → target type, with precision notes)

**5. Test Strategy Summary**
- Parity verification approach
- Total scenario count
- Key precision and equality rules

**6. Open Decisions**
- Data ownership conflicts flagged during blueprint
- Items requiring human review before sign-off

**7. GATE_1_DESIGN Sign-off Checklist**
| Checkpoint | Required Reviewer | Status |
|---|---|---|
| Requirements complete — all programs have extracted rules | Lead Architect | `PENDING` |
| Blueprint approved — package structure and APIs confirmed | Tech Lead | `PENDING` |
| Schema approved — entity fields and types confirmed | Data Architect | `PENDING` |
| Test strategy approved — coverage and parity rules confirmed | QA Lead | `PENDING` |
| Open decisions resolved | Lead Architect + PM | `PENDING` |

**8. Sign-off Instructions**

To record your approval, the team signs off via the manifest CLI or directly in
`audit.jsonl`. Include this in the packet:

```bash
# Record gate sign-off
python3 .anti-legacy/run.py manifest gate GATE_1_DESIGN \
  --opinion passed \
  --evaluator "{your-name}" \
  --rationale "{your rationale}" \
  --evidence "requirements-graph,blueprint-json,test-strategy"

# Then commit to git — the sign-off is now on the audit trail
git add .anti-legacy/manifest.json .anti-legacy/audit.jsonl
git commit -m "chore: GATE_1_DESIGN sign-off by {your-name}"
```

## Step 3: Done-gate, then register artifact and advance phase

**Done-gate (BLOCKING).** Before registering or advancing, assert the packet is
real AND all three prerequisite artifacts are present. The packet only exists if
`requirements_graph.json`, `blueprint.json`, and `test-strategy.md` were all
produced upstream — review-packet runs **after** blueprint and test-strategy.

```bash
python3 -c "import os,sys; \
pkt='.anti-legacy/review_packet.md'; \
prereqs=['.anti-legacy/requirements/requirements_graph.json', \
         '.anti-legacy/requirements/blueprint.json', \
         '.anti-legacy/contracts/test-strategy.md']; \
ok=os.path.isfile(pkt) and os.path.getsize(pkt)>0 and all(os.path.isfile(p) for p in prereqs); \
sys.stderr.write('' if ok else 'review-packet done-gate FAILED: review_packet.md missing/empty or a prerequisite artifact (requirements-graph/blueprint-json/test-strategy) is absent\n'); \
sys.exit(0 if ok else 1)"
```

If this assertion FAILS, do **NOT** run `register --status final` and do **NOT**
run `advance`. Surface the specific gap to the user (which prerequisite is
missing, or that the packet is empty) and stop — the user may retry or fix the
upstream phase. The `register --status final` and `advance` below are
**conditional on the assertion passing**.

Only on success:

```bash
python3 .anti-legacy/run.py manifest register review-packet \
  --path review_packet.md \
  --format markdown \
  --produced-by anti-legacy:review-packet \
  --status final \
  --depends-on requirements-graph,blueprint-json,test-strategy

python3 .anti-legacy/run.py manifest advance review-packet
```

Tell the user:
- Review packet is at `.anti-legacy/review_packet.md`
- Share it via git push or fileshare for team review
- Pipeline is now **paused at GATE_1_DESIGN** — no further phases until signed off
- Run `anti-legacy:gatekeeper GATE_1_DESIGN` after sign-off to verify and proceed

## Output

- `.anti-legacy/review_packet.md` — full offline review document
- Manifest: phase = `review-packet`, artifact `review-packet` registered

**Next step**: Human review → `anti-legacy:gatekeeper` to verify GATE_1_DESIGN sign-off.
