/*
 * anti-legacy standard rule-annotation template — DO NOT hand-edit logic into this file.
 *
 * Copy this file into the Java target tree alongside ImplementsRules.java (its
 * @Repeatable container) under the same package, then change the `package`
 * declaration to match the target's package layout (e.g.
 * `package com.example.acme.annotations;`).
 *
 * Purpose: a machine-readable hook binding a target component to the
 * requirements-graph rule id it satisfies. The round-trip scanner
 * (scripts/generate_target_graph.py) matches the literal annotation name
 * `@ImplementsRule` (and its alias `@SatisfiesRule`) and extracts the rule id
 * from value() to prove rule-level coverage in compare_graphs.py.
 *
 * value() carries ONE rule id, a requirements-graph token of the form
 * RULE-NNN / VAL-NNN / ERR-NNN (e.g. "RULE-001", "VAL-002", "ERR-001").
 *
 * Because this annotation is declared @Repeatable(ImplementsRules.class), a
 * single component can carry several rule ids by stacking the annotation —
 * the Java compiler wraps the repeats into the ImplementsRules container
 * automatically, and the scanner reads each repeat independently. Example
 * (placeholders RULE-NNN/RULE-MMM stand in for real ids such as RULE-001 so
 * this template contributes no phantom rule matches when scanned):
 *
 *     @ImplementsRule("RULE-NNN")
 *     @ImplementsRule("RULE-MMM")
 *     public class InterestCalculator { ... }
 *
 * Retention is RUNTIME so reflection-based tooling can read it; the static
 * scanner only needs the source text, but RUNTIME costs nothing extra and
 * keeps the annotation usable by test harnesses that introspect coverage.
 */
package annotations;

import java.lang.annotation.Documented;
import java.lang.annotation.ElementType;
import java.lang.annotation.Repeatable;
import java.lang.annotation.Retention;
import java.lang.annotation.RetentionPolicy;
import java.lang.annotation.Target;

/**
 * Marks a target element as the implementation of a single requirements-graph
 * business rule, validation, or error path.
 *
 * <p>Repeatable: stack multiple {@code @ImplementsRule} annotations on one
 * element to map it to several rule ids. The repeats are collected into the
 * {@link ImplementsRules} container.</p>
 *
 * @see ImplementsRules
 */
@Documented
@Repeatable(ImplementsRules.class)
@Retention(RetentionPolicy.RUNTIME)
@Target({
        ElementType.TYPE,
        ElementType.METHOD,
        ElementType.CONSTRUCTOR,
        ElementType.FIELD
})
public @interface ImplementsRule {

    /**
     * The requirements-graph rule id this element implements, e.g.
     * {@code "RULE-001"}, {@code "VAL-002"}, or {@code "ERR-001"}.
     *
     * @return the rule id token
     */
    String value();
}
