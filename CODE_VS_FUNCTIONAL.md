# Migration Paradigm: Match Code vs Match Functionality

When modernizing a legacy system, the first and most critical architectural decision is deciding whether the target system should **Match Code** (Structural Migration) or **Match Functionality** (Functional Migration).

This choice is not a manual judgement call made once and forgotten — it is a config knob. `migration_mode` in `.anti-legacy/config.json` takes `functional` (the intended default — **Match Functionality**) or `structural` (**Match Code**), and that value flows into the requirements graph metadata, where it drives how legacy modules are grouped into nodes for the rest of the pipeline.

> **The invariant either way.** This pipeline performs a *behavior-preserving targeted rewrite*. The data **shapes** (contracts), the **interfaces**, and the **jobs** (full functionality) are invariant — they must be preserved regardless of mode. Only the *implementation* is reimagined. Match Code and Match Functionality differ only in how aggressively the implementation is reshaped, not in whether behavior is preserved. The requirements graph **is** the behavioral contract; the target is built against the graph, not against the legacy source.

---

## 1. Match Code (Structural Migration)

> Set via `migration_mode: "structural"` in `.anti-legacy/config.json`.

The goal of a structural migration is a 1-to-1 conversion of the legacy source code into the target language. Each legacy file, class, program, or module translates directly to a corresponding file or class in the target codebase.

### Characteristics
*   **Granularity**: Low. 1 legacy program = 1 target component.
*   **Interface Parity**: High. Legacy entry points, parameters, and database schemas are preserved.
*   **Verification Strategy**: Side-by-side/golden-file unit and module parity testing.

### Pros & Cons
> [!TIP]
> **Pros**:
> - Simplest mechanical translation. Highly automatable.
> - Clear 1-to-1 traceability. If a bug is found in `CUSTMGR`, the fix is easily mapped to `CustMgr.java`.
> - Minimizes domain/business analysis effort.
>
> [!WARNING]
> **Cons**:
> - Preserves legacy technical debt, bad design patterns, and anti-patterns.
> - Often results in non-idiomatic target code (e.g., "COBOL written in Java" or "VB6 written in C#").
> - Preserves obsolete modular boundaries and data layouts (like flat files or denormalized tables).

---

## 2. Match Functionality (Functional Migration)

> Set via `migration_mode: "functional"` in `.anti-legacy/config.json` — the intended default.

The goal of a functional migration is to extract the **business rules and intent** of the legacy system and implement them using modern architectural patterns, clean boundaries, and idiomatic conventions. The extracted rules become the nodes of the requirements graph, which the target is then built against — so even though the implementation is fully reimagined, the captured behavior (shapes, interfaces, jobs) is held invariant.

### Characteristics
*   **Granularity**: Coarse. Legacy modules are grouped and merged into cohesive business capabilities.
*   **Interface Parity**: Selective. Legacy endpoints are redesigned as REST APIs, gRPC services, or clean event consumers.
*   **Verification Strategy**: End-to-end integration and functional parity testing mapped to business requirements.

### Pros & Cons
> [!TIP]
> **Pros**:
> - Eradicates technical debt and dead code.
> - Produces clean, maintainable, and idiomatic modern code.
> - Realizes architectural benefits (modular monoliths, domain-driven design).
>
> [!CAUTION]
> **Cons**:
> - High cognitive load. Requires deep semantic analysis of legacy source code.
> - Complex verification. Parity must be verified at the business capability level rather than the function level.
> - Harder traceability. A single capability may span 5 legacy files.

---

## 3. Decision Matrix

Use the following table to select the appropriate paradigm for each application/component:

| Metric / Constraint | Choose **Match Code** | Choose **Match Functionality** |
|---|---|---|
| **Target Code Lifecycle** | Short-term / migration stepping stone | Long-term core business asset |
| **Legacy Code Quality** | Well-structured, clean modular interfaces | Highly coupled, spaghetti code, dead code |
| **Team Domain Context** | Low/No domain context; mechanical migration | High domain context; active design |
| **Target Architecture** | Lift-and-shift VMs / Containers | Domain-Driven Design (DDD), Modular Monolith |
| **Database Strategy** | Direct migration / Schema preservation | Redesigned schemas, proper normalization |

---

## 4. Test Implications

### Parity Testing under Match Code
Under Match Code, testing is highly deterministic. Each translated module can be verified by feeding it the same input variables as the legacy module and asserting exact output field matching.
- **Precision**: High. Requires matching exact COMP-3/numeric precision scales.
- **Scope**: Narrow. Unit and integration tests map 1-to-1.

### Parity Testing under Match Functionality
Under Match Functionality, testing shifts from "does this class return the same fields" to "does this business capability produce the same business results".
- **Precision**: Selective. Focuses on final data state parity and API contracts.
- **Scope**: Broad. Focuses on end-to-end integration tests, transaction boundaries, and state changes.
