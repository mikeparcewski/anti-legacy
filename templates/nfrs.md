# Non-Functional Requirements (NFRs) — {project_name}

This document outlines the operational constraints, compliance criteria, performance benchmarks, and security controls defined for the modernization target stack ({target_stack}).

---

## 1. Data Precision & Financial Auditing
*   **Precision Rules**: Money, interest rates, and financial aggregates must use high-precision decimals (`BigDecimal` in Java, `decimal.Decimal` in Python, `shopspring/decimal` in Go) rather than standard IEEE-754 binary floating-point numbers (`float`/`double`).
*   **Rounding Behavior**: Default rounding strategy is **ROUND_HALF_UP** unless legacy system documentation explicitly demands another format.
*   **Precision Parity Invariant**: Values derived from legacy COMP-3 or zoned-decimal layouts must match target outputs to the decimal places specified in their legacy PIC clauses.

---

## 2. Security & Data Sanitization
*   **Input Validation**: All legacy files, batch inputs, and API request payloads must undergo schema validation (e.g. JSON schema, XML validation, or class constraints) at target entry points.
*   **Data Masking**: PII fields (such as account numbers or government identification numbers) must be masked at rest and in logs unless explicit access is authorized.
*   **Sanitization**: Input fields must be sanitized to prevent injection attacks (SQL injection, script injection) in database and downstream call chains.

---

## 3. Auditing & Logging
*   **Execution Logs**: Every transaction or batch run must write a structured log entry detailing:
    - Unique transaction/batch execution identifier.
    - Timestamp (ISO 8601 in UTC).
    - Source/Legacy identifier.
    - Status code (SUCCESS/ERROR) and count of processed records.
*   **Audit Trail**: The target system must maintain an immutable, tamper-evident audit table containing all critical state changes (creation, updates, deletes of financial entities).

---

## 4. Performance & Scalability
*   **Response Time**: API/Online endpoints should maintain a sub-second p95 response time.
*   **Batch window**: Batch processing jobs must execute within a configured daily window and support checkpointing/resumability to handle midway failures.
*   **Resource Footprint**: The modernized containerized services must conform to the target memory limits ({deployment_target} defaults).
