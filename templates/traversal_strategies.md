# Dependency Graph Traversal & Execution Strategies

During the **Planner** phase, the modernization requirements graph is decomposed into a task list (`task.md`) for target code construction. How this dependency graph is traversed determines the execution ordering and engineering paradigm of the modernization project. 

There are three primary strategies for graph traversal, each presenting distinct trade-offs, risks, and rationales.

---

## 1. Bottom-Up (Dependency-First)

**Execution Flow**: Decompose and order tasks starting at the leaves of the dependency tree (utility classes, database tables, repository interfaces) and move upward through business logic services to the API controller/batch entry points.

### Rationale
*   **Compilation Safety**: Ensures that every class being written compiles immediately, since all of its imported dependencies are already fully implemented.
*   **High-Fidelity Unit Testing**: Allows developers to write true, mock-free unit and integration tests early, as real repository and entity classes exist.
*   **Deterministic Math Parity**: Catch precision anomalies (like COMP-3 to BigDecimal) immediately in the data layer before layer-2 business rules consume them.

### Risks
*   **Late Integration Validation**: The system's API endpoints and user-facing entry points are not built until the very end, deferring cross-system integration validation.
*   **Requirement Disconnect**: If the top-level APIs require fields or signatures that weren't anticipated at the lower layer, developers face major refactoring loops.

---

## 2. Top-Down (API/Entry-First)

**Execution Flow**: Decompose and order tasks starting at the entry points of the target application (REST/gRPC controllers, batch orchestrators, message queue listeners) and work downward, mocking out service and data repository interfaces.

### Rationale
*   **Early Interface Verification**: Lock down API schemas and contracts with client/consuming teams on day one.
*   **Stubbed E2E Flow**: Deliver a mock-running service early, permitting early integration testing in sandbox environments.

### Risks
*   **High Mocking Overhead**: Requires writing extensive mock objects, stubs, and synthetic databases that will eventually be discarded.
*   **Deferred Data/Parity Risk**: Complex database constraints, precision rules, and actual transaction boundaries are verified late in the lifecycle.

---

## 3. Vertical Slice (Tracer Bullet)

**Execution Flow**: Identify cohesive end-to-end features or domains (e.g. `Domain_billing:calculate_invoice` spanning controller -> service -> database) and build the entire vertical thread before starting the next.

### Rationale
*   **Continuous Value Delivery**: Delivers fully functional, deployable business capabilities incrementally rather than waiting for the entire database layer to compile.
*   **End-to-End De-risking**: Proves the architectural stack (JPA configuration, REST controllers, connection pools) works early in the project.

### Risks
*   **Conflict Hotspots**: Developer subagents working in parallel may conflict when modifying shared database models, database migration files, or core utility modules.

---

## Decision Matrix

Select the appropriate execution strategy based on your project constraints:

| Constraint / Goal | Bottom-Up | Top-Down | Vertical Slice |
|---|---|---|---|
| **Compilation Safety** | **High** (Guaranteed) | Low (Requires heavy mocking) | Medium (Slices must compile) |
| **Early Client Integration** | Low | **High** (API contracts ready first) | Medium (Slices ready sequentially) |
| **High Parallelism (1-5 devs)** | Medium | Medium | **High** (Disjoint domain owners) |
| **High Parallelism (>10 devs)**| **High** (Layer-based teams) | Medium | Low (Merge conflict risk on shared schemas) |
| **Precision Parity (COMP-3)** | **High** (Tested at database layer first) | Low (Mock values mask math limits) | Medium (Tested per slice) |

---

## 4. Verification Checklists for Gate 2 Review

When reviewing the generated build plan (`task.md`) during **Gate 2 (Plan Review)**, the Tech Lead and PM must verify compliance using the following checklist items:

### Bottom-Up Checklist
- [ ] **Dependency Precedence**: Confirm that for every requirement, all of its declared dependencies are scheduled in earlier tasks.
- [ ] **Compilation Order**: Verify that Layer 0 (Data models) and Layer 1 (Repositories) are scheduled before Layer 2 (Services) and Layer 3 (API Controllers).
- [ ] **Unit Test Capability**: Confirm that unit tests can be written mock-free using actual lower-layer components.

### Top-Down Checklist
- [ ] **Mock Contract Generation**: Verify that tasks to generate stub contracts or downstream interfaces are scheduled first.
- [ ] **API Controller Priority**: Confirm that REST/gRPC controller layers (Layer 3) are scheduled before the core service and repository layers.
- [ ] **Downstream Stubs**: Ensure downstream repositories and external clients are mocked out to support early stubbed execution.

### Vertical Slice Checklist
- [ ] **Domain Grouping**: Verify that all tasks associated with a single domain (e.g. `Domain_billing`) are scheduled contiguously to minimize context switching.
- [ ] **Slice Independence**: Confirm that cross-domain dependencies do not form cycles, and that dependency chains are followed within each domain slice.
- [ ] **Model Ownership**: Verify that parallel slices modifying shared tables or schemas have explicit conflict resolution tasks scheduled.
