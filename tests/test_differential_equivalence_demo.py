"""End-to-end demo: GATE_3C_DIFFERENTIAL is non-vacuous on BILLING.cbl (ISS-7).

Proves the differential-equivalence gate flips from NOT_APPLICABLE to a real verdict on the
bundled COBOL demo, and catches the silent COMP-3 truncate-vs-round divergence. The golden is a
reference oracle derived from BILLING.cbl arithmetic (COMPUTE ... with no ROUNDED clause ->
truncation); see demo/differential-equivalence/README.md.
"""
import os
import sys
import unittest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_REPO, "skills", "anti-legacy-expert", "scripts"))
_DEMO = os.path.join(_REPO, "demo", "differential-equivalence")
sys.path.insert(0, _DEMO)

from antilegacy_core import differential_equivalence as de  # noqa: E402
import billing_oracle as oracle  # noqa: E402


class DiffEquivDemoTest(unittest.TestCase):
    def setUp(self):
        self.parity = de.load_parity_by_req(_DEMO)
        self.corpus = oracle.golden_corpus()

    def test_contract_supplies_parity_rules(self):
        self.assertIn(oracle.REQ_ID, self.parity)
        self.assertEqual({r["field"] for r in self.parity[oracle.REQ_ID]},
                         {"INV-TAX", "INV-TOTAL"})

    def test_golden_reflects_cobol_truncation(self):
        # No ROUNDED clause in BILLING.cbl -> COBOL truncates the product to 2 dp.
        g = {e["scenario_id"]: e["golden_output"] for e in self.corpus}
        self.assertEqual(g["INV-CA-TRUNC"]["INV-TAX"], "0.72")   # 10.00*0.0725=0.7250 -> trunc 0.72
        self.assertEqual(g["INV-TX-TRUNC"]["INV-TAX"], "6.24")   # 99.99*0.0625=6.249375 -> trunc 6.24

    def test_faithful_target_passes(self):
        rep = de.run_harness(self.corpus, oracle.target_actuals("faithful"), self.parity)
        self.assertEqual(rep["status"], de.PASS)
        self.assertEqual(rep["aggregate"]["fail"], 0)

    def test_rounding_target_fails_on_comp3_divergence(self):
        rep = de.run_harness(self.corpus, oracle.target_actuals("rounding"), self.parity)
        self.assertEqual(rep["status"], de.FAIL)
        failed = {s["scenario_id"] for s in rep["scenarios"] if s["status"] != "PASS"}
        # exactly the two between-cent scenarios diverge (truncate != round)
        self.assertEqual(failed, {"INV-CA-TRUNC", "INV-TX-TRUNC"})
        self.assertGreaterEqual(rep["aggregate"]["violations"], 4)  # INV-TAX + INV-TOTAL on each

    def test_gate_is_non_vacuous_with_a_corpus(self):
        rep = de.run_harness(self.corpus, oracle.target_actuals("faithful"), self.parity)
        self.assertNotEqual(rep["status"], de.NOT_APPLICABLE)  # the whole point of ISS-7 follow-up
        self.assertEqual(rep["aggregate"]["scenarios"], 4)


if __name__ == "__main__":
    unittest.main()
