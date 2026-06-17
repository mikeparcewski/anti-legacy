---
name: "anti-legacy:skill-forge"
description: >
  A meta-skill — a skill that WRITES skills. Once the blueprint exists, it generates one
  target-tailored `build-<domain>` SKILL.md per domain into .anti-legacy/generated-skills/, baking
  the blueprint component specs + each requirement's business rules / validations / error paths +
  entity parity into reusable build instructions a CLI/IDE agent FOLLOWS to build the target
  natively and consistently. Inverse of anti-legacy:develop-plugin (which evolves the plugin):
  this generates the TARGET system's build skills. Use when: "generate the build skills",
  "create skills to build the target", "scaffold the target build", "forge build skills",
  "make it natural to build the target state".
---

# anti-legacy:skill-forge

The pipeline already *designs* the target (`blueprint.json`) and *learns* patterns/decisions
(git-brain). But building the target has been a per-task micro-context assembly inside `swarm`
each run — it isn't a first-class thing a CLI/IDE agent just *does*. `skill-forge` closes that:
it turns the target design into **target-tailored build skills the agent invokes by name**.

It is the target-system analog of `anti-legacy:develop-plugin`: develop-plugin evolves *this
plugin* from learnings; skill-forge **generates skills for the target system**. The product is
`SKILL.md` files (instructions the agent follows), not scripts — because building is an agent
task. They are deterministic projections of the design (no LLM), regenerated whenever the
blueprint changes, and they cite git-brain patterns so they sharpen as the brain learns.

## Cross-Platform Notes

One command through the dispatcher (`python3 .anti-legacy/run.py skill_forge`), pure Python —
macOS / Linux / WSL / Windows.

## When it runs / prerequisites

- **After `anti-legacy:blueprint`** — it reads `blueprint.json` (the target architecture:
  domains → components with `class_name`/`component_type`/`api`/`methods`/`schema`) +
  `requirements_graph.json` (each component's `business_rules` / `validations` / `error_paths` /
  `legacy_components`) + `config.json` (target stack). No blueprint ⇒ it refuses (won't forge
  hollow skills).
- It owns **no** manifest phase enum value — it's a generation utility that runs in/after the
  `blueprint` phase. It does **not** `manifest advance`.

## Step 1: Generate the build skills

```bash
python3 .anti-legacy/run.py skill_forge
```

This writes, per target domain, `.anti-legacy/generated-skills/build-<domain>/SKILL.md` — a
self-contained build contract for that capability — plus a `README.md` index. Each generated
skill contains:
- **Target** (stack, package, style) + **conventions** (idiomatic code, `@ImplementsRule` on every
  rule, rule-coverage 1.0, no stubs, numeric-precision parity).
- A **git-brain patterns** step (query the brain for this stack's translation recipes first).
- A dependency-sorted **build order**.
- Per component: the blueprint spec (class/type/target_file/api/methods/deps) + the requirement's
  **business rules** (with their `RULE-`/`VAL-`/`ERR-` ids to annotate), the **legacy provenance**
  (§2 traceability), and the **data model** with `source_type` parity (COMP-3/DECIMAL).

## Step 2: Build the target by FOLLOWING the generated skills

To build a capability, the agent reads + follows the matching generated skill — e.g. *"build the
billing domain"* → `.anti-legacy/generated-skills/build-billing/SKILL.md`. (`anti-legacy:swarm`
can dispatch these per task instead of re-deriving a micro-context.) Because the spec, rules,
parity, and conventions are baked in, the build is consistent and traceable across any CLI/IDE
agent — and re-running `skill_forge` after a blueprint change regenerates them (idempotent).

## Step 3: Done-gate + report

The generator exits non-zero if there is no blueprint with domains. On success it prints the
generated skills. Confirm the index exists and report which domains got build skills (and any
domain whose components carry **no business rules** — the forge flags those `⚠` in-skill rather
than inventing behavior; they need extraction/human review, not a guess).

## Output

- `.anti-legacy/generated-skills/build-<domain>/SKILL.md` — one target-tailored build skill per
  domain (agent-followed).
- `.anti-legacy/generated-skills/README.md` — the index.

## The memory loop (why this compounds)

Generated skills cite the git-brain patterns they were built from; as `swarm`/`developer` record
new translation patterns (and decisions/learnings) back to git-brain, a re-forge produces sharper
build skills. Design (blueprint) + memory (git-brain) → generated build skills → agent builds →
memory. `skill-forge` is the step that makes the learned target-state design *actionable* as
agent-native skills.

## Don'ts (AGENTS.md)

- DON'T invent behavior for a component with no `business_rules` — the forge flags it `⚠`; send it
  back to extraction or human review.
- DON'T drop the `legacy_components` trace or the `source_type` parity from a generated skill.
- DON'T hand-edit generated skills — regenerate from the blueprint (they're deterministic output).
