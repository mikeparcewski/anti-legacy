/*
 * anti-legacy standard rule-annotation template — DO NOT hand-edit logic into this file.
 *
 * This is the @Repeatable container for ImplementsRule.java. Copy BOTH files
 * into the Java target tree under the SAME package, then change the `package`
 * declaration to match the target's package layout (e.g.
 * `package com.example.acme.annotations;`).
 *
 * A developer almost never references @ImplementsRules directly — the Java
 * compiler synthesizes it automatically when more than one @ImplementsRule is
 * stacked on the same element. Example (placeholders RULE-NNN/RULE-MMM stand
 * in for real ids such as RULE-001 so this template contributes no phantom
 * rule matches when scanned):
 *
 *     @ImplementsRule("RULE-NNN")
 *     @ImplementsRule("RULE-MMM")   // compiler wraps both into @ImplementsRules
 *     public class InterestCalculator { ... }
 *
 * Without this container, stacking two @ImplementsRule annotations on one
 * element is a compile error ("ImplementsRule is not a repeatable annotation
 * type"). It exists so multi-rule mappings compile cleanly. The round-trip
 * scanner (scripts/generate_target_graph.py) reads each nested @ImplementsRule
 * occurrence directly from source text, so no separate scanner support for the
 * container is required.
 */
package annotations;

import java.lang.annotation.Documented;
import java.lang.annotation.ElementType;
import java.lang.annotation.Retention;
import java.lang.annotation.RetentionPolicy;
import java.lang.annotation.Target;

/**
 * Container for repeated {@link ImplementsRule} annotations.
 *
 * <p>Declared as the {@code @Repeatable} target of {@link ImplementsRule}; the
 * compiler populates it automatically when an element carries more than one
 * {@code @ImplementsRule}. The {@link #value()} array preserves declaration
 * order.</p>
 *
 * @see ImplementsRule
 */
@Documented
@Retention(RetentionPolicy.RUNTIME)
@Target({
        ElementType.TYPE,
        ElementType.METHOD,
        ElementType.CONSTRUCTOR,
        ElementType.FIELD
})
public @interface ImplementsRules {

    /**
     * The stacked {@link ImplementsRule} annotations applied to the element.
     *
     * @return the contained rule annotations, in declaration order
     */
    ImplementsRule[] value();
}
