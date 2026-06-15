# Modernization Anti-Patterns

Avoid these common modernization mistakes. Each has derailed real modernization projects:

---

## 1. Line-by-Line Translation
*   **Definition**: Directly converting legacy structures (e.g., VB.NET to C# or COBOL to Java) without rethinking the architecture.
*   **Consequence**: Produces modern-looking code that inherits all legacy design problems, such as global state, lack of encapsulation, denormalized data models, and massive single-responsibility violations (god classes).
*   **Resolution**: Revisit architectural decisions. Define domain boundaries, build clean services, use proper data types (BigDecimal/decimal), and write idiomatic target code.

---

## 2. Defaulting to Microservices
*   **Definition**: Splitting a small legacy application into dozens of independent distributed services because "microservices are modern."
*   **Consequence**: For teams of 1–5 developers, microservices introduce excessive operational complexity (distributed transactions, network latency, distributed logging, service discovery) without adding organizational benefit.
*   **Resolution**: Build a **Modular Monolith**. It provides the same code organization, clean domain separation, and team independence benefits, without the operational overhead of distributed microservices.

---

## 3. Ignoring Existing Infrastructure
*   **Definition**: Designing the target architecture in a vacuum without analyzing the constraints and license cost of current infrastructure.
*   **Consequence**: Underutilized physical servers, cloud storage limits, database license costs, and network bandwidth bottlenecks can dramatically skew target operations.
*   **Resolution**: Always perform a complete inventory of existing system resources, licenses, hardware capacities, and network limits before designing the target state.
