"""
Repeatable Java rule-annotation templates (feedback #5).

A target component frequently implements several requirements-graph rules.
Mapping all of them onto one element means stacking ``@ImplementsRule`` — which
plain Java rejects unless the annotation is declared ``@Repeatable`` and a
matching container annotation exists. This capability ships those two standard
declarations as plugin templates so multi-rule mappings compile, and updates the
swarm skill to tell the developer subagent to copy and use them.

These tests assert the *contract the round-trip scanner depends on*:

  * both template files exist and have the structure the spec requires
    (``@Repeatable(ImplementsRules.class)`` + ``String value()`` on
    ``ImplementsRule``; ``ImplementsRule[] value()`` on the ``ImplementsRules``
    container);
  * the REAL scanner (``scripts/generate_target_graph.py``) reads a *stacked*
    ``@ImplementsRule`` block as N distinct rule ids — i.e. a single component
    can cover all its rules without a compiler error and the coverage proof
    still sees every one;
  * the template files themselves inject ZERO phantom rule ids when scanned
    (their doc examples use non-matching ``RULE-NNN`` placeholders), so copying
    them into ``src/main/java`` does not pollute ``target_graph.json``;
  * the swarm SKILL.md documents the multi-rule stacking setup and keeps the
    ``@ImplementsRule`` / ``@SatisfiesRule`` names the scanner matches.

Determinism: no network, no clocks, no randomness. The scanner method is driven
on in-memory source strings and on the committed template files; nothing under
``.anti-legacy/`` is read or mutated.
"""
import os
import re

import pytest


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _templates_dir(repo_root):
    return os.path.join(repo_root, "templates")


def _read_template(repo_root, name):
    path = os.path.join(_templates_dir(repo_root), name)
    assert os.path.isfile(path), f"missing rule-annotation template: {path}"
    with open(path, encoding="utf-8") as fh:
        return path, fh.read()


def _java_generator(scripts_dir):
    """Construct the real scanner's generator bound to the java language config.

    ``scripts/`` is already on sys.path via tests/evals/conftest.py, so the
    import resolves the project's module (not a same-named one elsewhere).
    """
    import generate_target_graph as gtg  # noqa: WPS433  (deliberate local import)

    # target_dir is irrelevant for _extract_rule_evidence (it works on a string),
    # but the constructor needs the language config wired, so pass stack="java".
    gen = gtg.TargetGraphGenerator(target_dir=scripts_dir, target_stack="java")
    return gen


# --------------------------------------------------------------------------- #
# Template existence + structure
# --------------------------------------------------------------------------- #
def test_implements_rule_template_is_repeatable_with_string_value(repo_root):
    """@ImplementsRule must be @Repeatable(ImplementsRules.class) with String value()."""
    path, text = _read_template(repo_root, "ImplementsRule.java")

    assert "@interface ImplementsRule" in text, (
        f"{path} does not declare the @ImplementsRule annotation type."
    )
    # @Repeatable pointing at the container is what makes stacking legal.
    assert re.search(r"@Repeatable\(\s*ImplementsRules\.class\s*\)", text), (
        f"{path} is not declared @Repeatable(ImplementsRules.class); stacking "
        "two @ImplementsRule on one element would be a compile error."
    )
    # value() carries the single rule id the scanner reads.
    assert re.search(r"String\s+value\(\)\s*;", text), (
        f"{path} has no `String value();` — the scanner extracts the rule id "
        "from value(), so it must be a single String."
    )


def test_implements_rules_container_template_holds_array(repo_root):
    """The @ImplementsRules container must declare ImplementsRule[] value()."""
    path, text = _read_template(repo_root, "ImplementsRules.java")

    assert "@interface ImplementsRules" in text, (
        f"{path} does not declare the @ImplementsRules container annotation."
    )
    assert re.search(r"ImplementsRule\[\]\s+value\(\)\s*;", text), (
        f"{path} has no `ImplementsRule[] value();` — the @Repeatable container "
        "must hold an array of the repeated annotation."
    )


# --------------------------------------------------------------------------- #
# The REAL scanner reads stacked annotations as N rule ids
# --------------------------------------------------------------------------- #
def test_scanner_reads_stacked_annotations_as_distinct_rule_ids(scripts_dir):
    """Stacking @ImplementsRule N times yields N rule-id evidence rows.

    This is the integration point the templates exist to serve: with the
    @Repeatable declaration the compiler accepts the stack, and the scanner
    (which reads source text line-by-line) records every repeat. Without that,
    a multi-rule component could only carry one id.
    """
    gen = _java_generator(scripts_dir)
    source = (
        '@ImplementsRule("RULE-001")\n'
        '@ImplementsRule("RULE-002")\n'
        '@ImplementsRule("VAL-003")\n'
        "public class InterestCalculator {\n"
        '    @ImplementsRule("ERR-004")\n'
        "    void onOverflow() {}\n"
        "}\n"
    )
    evidence = gen._extract_rule_evidence(source, "InterestCalculator.java")
    rule_ids = {e["rule_id"] for e in evidence}

    assert rule_ids == {"RULE-001", "RULE-002", "VAL-003", "ERR-004"}, (
        "scanner did not read every stacked @ImplementsRule as a distinct rule "
        f"id; got {sorted(rule_ids)}"
    )
    # Every annotation row is recorded with source 'annotation' (WEAK tier).
    assert all(e["source"] == "annotation" for e in evidence), (
        "stacked @ImplementsRule rows must be recorded with source 'annotation'."
    )


def test_scanner_also_matches_satisfiesrule_alias(scripts_dir):
    """The alias @SatisfiesRule the scanner accepts still resolves to a rule id.

    The swarm guidance tells the subagent not to invent a new annotation name;
    only @ImplementsRule and @SatisfiesRule are matched. Guard the alias so the
    guidance stays truthful.
    """
    gen = _java_generator(scripts_dir)
    source = '@SatisfiesRule("RULE-009")\npublic class Foo {}\n'
    evidence = gen._extract_rule_evidence(source, "Foo.java")
    assert {e["rule_id"] for e in evidence} == {"RULE-009"}


# --------------------------------------------------------------------------- #
# The template files inject zero phantom rule ids when scanned
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("name", ["ImplementsRule.java", "ImplementsRules.java"])
def test_template_files_inject_no_phantom_rule_ids(repo_root, scripts_dir, name):
    """Scanning the templates themselves must yield no rule-id evidence.

    The templates are copied into src/main/java; if their doc-comment examples
    used real ids (RULE-001), the scanner — which does not skip comments — would
    attribute phantom coverage to the annotation symbol. The examples therefore
    use non-matching RULE-NNN placeholders.
    """
    _path, text = _read_template(repo_root, name)
    gen = _java_generator(scripts_dir)
    evidence = gen._extract_rule_evidence(text, name)
    assert evidence == [], (
        f"{name} produced phantom rule-id evidence {evidence}; the template's "
        "example snippets must use non-matching placeholders (RULE-NNN), not "
        "real RULE-/VAL-/ERR-NNN ids."
    )


# --------------------------------------------------------------------------- #
# The swarm skill documents the multi-rule setup
# --------------------------------------------------------------------------- #
def test_swarm_skill_documents_repeatable_annotation_setup(repo_root):
    """skills/swarm/SKILL.md must teach copying + stacking the templates."""
    path = os.path.join(repo_root, "skills", "swarm", "SKILL.md")
    assert os.path.isfile(path), f"missing swarm skill: {path}"
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    lowered = text.lower()

    # Names the scanner matches must appear verbatim.
    assert "@ImplementsRule" in text, f"{path} drops the @ImplementsRule name."
    # The container + @Repeatable mechanism is what the guidance must explain.
    assert "ImplementsRules" in text, (
        f"{path} never names the @ImplementsRules container; a developer cannot "
        "set up the repeatable annotation without it."
    )
    assert "@Repeatable" in text or "repeatable" in lowered, (
        f"{path} never explains the annotation is @Repeatable; without it, "
        "stacking is a compile error and the guidance is incomplete."
    )
    # It must instruct copying the templates into the target tree.
    assert ("ImplementsRule.java" in text) and ("ImplementsRules.java" in text), (
        f"{path} does not tell the subagent to copy BOTH template files into "
        "the target tree."
    )
    # It must show the stacking pattern (more than one @ImplementsRule).
    assert len(re.findall(r"@ImplementsRule\(", text)) >= 2, (
        f"{path} never shows two stacked @ImplementsRule annotations; the "
        "multi-rule mapping pattern is the whole point of this guidance."
    )
