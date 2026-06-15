#!/usr/bin/env python3
"""Unit tests for scripts/detect_dead_ends.py (WF1 wicked-estate rewire).

The script now reads the code graph through the scripts/wicked_estate.py helper
(CLI-backed) instead of the deleted .anti-legacy/legacy_graph.json. These tests
inject a FAKE helper (duck-typed to the helper's query/blast_radius/source/
list_nodes surface) and a temp workspace, so they assert the dead-end detection
logic end-to-end without needing the real wicked-estate binary or a built DB.
"""
import io
import os
import sys
import unittest
import tempfile
import shutil
from contextlib import redirect_stdout

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "scripts"))
import detect_dead_ends as dde  # noqa: E402


class FakeEstate:
    """A minimal stand-in for the wicked_estate helper.

    `nodes`: list of {symbol,name,kind,file,line}.
    `deps`:  name -> {"file": <own_file>, "dependents": [<node>, ...]}.
    `sources`: name -> source text (for `source` slices).
    """

    def __init__(self, nodes=None, deps=None, sources=None, omit_own_file=False):
        self._nodes = nodes or []
        self._deps = deps or {}
        self._sources = sources or {}
        # When True, mimic the REAL helper: blast_radius carries NO own-file key
        # for the queried node, forcing the query() fallback in _own_file_of.
        self._omit_own_file = omit_own_file

    def list_nodes(self, db, kinds=None):
        # Mirror the real helper: `kind` is de-quoted to a bare token for the
        # filter; estate object-kinds are excluded when a kinds filter is given.
        if kinds is None:
            return list(self._nodes)
        want = {str(k).strip().strip('"').lower() for k in kinds}
        out = []
        for n in self._nodes:
            k = str(n.get("kind", "")).strip().strip('"').lower()
            if k in want:
                out.append(n)
        return out

    def blast_radius(self, db, name):
        info = self._deps.get(name, {"file": None, "dependents": []})
        if self._omit_own_file:
            # Real helper shape: {name, dependents} only.
            return {"name": name, "dependents": info.get("dependents", [])}
        return info

    def query(self, db, name):
        # Used by _own_file_of's fallback when blast_radius omits the own file.
        own = self._deps.get(name, {}).get("file")
        matches = []
        if own:
            matches.append(_node(name, "Module", own))
        return {"name": name, "matches": matches}

    def source(self, db, name):
        # Real helper returns {name, matches, body}; we return the body key.
        if name in self._sources:
            return {"name": name, "matches": [], "body": self._sources[name]}
        return {"name": name, "matches": [], "body": ""}


def _node(name, kind, file, line=1, symbol=None):
    return {
        "symbol": symbol or f"sym::{name}",
        "name": name,
        "kind": kind,
        "file": file,
        "line": line,
    }


class TestDeadEndDetection(unittest.TestCase):
    def setUp(self):
        dde._emitted_questions.clear()
        self.workspace = tempfile.mkdtemp(prefix="anti-legacy-dde-test-")
        # An app source tree the cross-reference scan walks.
        self.app_path = os.path.join(self.workspace, "carddemo")
        os.makedirs(os.path.join(self.app_path, "jcl"), exist_ok=True)
        os.makedirs(os.path.join(self.app_path, "csd"), exist_ok=True)
        os.makedirs(os.path.join(self.app_path, "cbl"), exist_ok=True)
        # A graphs/<app>.db file must EXIST for run_detection to proceed
        # (content is irrelevant — the fake helper never opens it).
        self.graphs_dir = os.path.join(self.workspace, dde.GRAPHS_DIR)
        os.makedirs(self.graphs_dir, exist_ok=True)
        self._old_cwd = os.getcwd()
        os.chdir(self.workspace)

    def tearDown(self):
        os.chdir(self._old_cwd)
        shutil.rmtree(self.workspace, ignore_errors=True)
        dde._emitted_questions.clear()

    def _touch_db(self, app_name):
        open(os.path.join(self.graphs_dir, f"{app_name}.db"), "w").close()

    def _write(self, rel, content):
        path = os.path.join(self.app_path, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(content)

    # ---- helper-level predicates ------------------------------------------

    def test_program_callers_excludes_self_and_jcl(self):
        # CBACT01C: dependents are only its own File + a JCL Module/step => no
        # program-to-program caller => in-degree-0 (uncalled).
        fe = FakeEstate(deps={
            "CBACT01C": {
                "file": "app/cbl/CBACT01C.cbl",
                "dependents": [
                    _node("CBACT01C", "File", "app/cbl/CBACT01C.cbl"),
                    _node("READACCT", "Module", "app/jcl/READACCT.jcl"),
                    _node("STEP05", 'Other("step")', "app/jcl/READACCT.jcl", 32),
                ],
            }
        })
        self.assertEqual(dde.program_callers(fe, "db", "CBACT01C"), [])

    def test_program_callers_counts_real_caller(self):
        # CSUTLDTC is called by another COBOL Module in a different file.
        fe = FakeEstate(deps={
            "CSUTLDTC": {
                "file": "app/cbl/CSUTLDTC.cbl",
                "dependents": [
                    _node("CSUTLDTC", "File", "app/cbl/CSUTLDTC.cbl"),
                    _node("CORPT00C", "Module", "app/cbl/CORPT00C.cbl"),
                ],
            }
        })
        callers = dde.program_callers(fe, "db", "CSUTLDTC")
        self.assertEqual([c["name"] for c in callers], ["CORPT00C"])

    # ---- COBOL dead-end questions -----------------------------------------

    def test_jcl_referenced_uncalled_program_emits_question(self):
        self._write("jcl/READACCT.jcl", "//STEP05 EXEC PGM=CBACT01C\n")
        fe = FakeEstate(
            nodes=[_node("CBACT01C", "Module", "app/cbl/CBACT01C.cbl", 22)],
            deps={"CBACT01C": {
                "file": "app/cbl/CBACT01C.cbl",
                "dependents": [_node("CBACT01C", "File", "app/cbl/CBACT01C.cbl")],
            }},
        )
        out = io.StringIO()
        with redirect_stdout(out):
            dde.analyze_app(fe, "carddemo", self.app_path,
                            os.path.join(self.graphs_dir, "carddemo.db"))
        text = out.getvalue()
        self.assertIn("DEAD_END_COBOL_JCL_CARDDEMO", text)
        self.assertIn("CBACT01C", text)
        self.assertIn("READACCT.jcl", text)
        self.assertIn("Spring Batch", text)

    def test_cics_csd_bms_referenced_program_emits_question(self):
        self._write("csd/CARDDEMO.csd", "DEFINE PROGRAM(COSGN00C)\n")
        self._write("csd/COSGN00.bms", "COSGN00C SCREEN MAP\n")
        fe = FakeEstate(
            nodes=[_node("COSGN00C", "Module", "app/cbl/COSGN00C.cbl", 22)],
            deps={"COSGN00C": {
                "file": "app/cbl/COSGN00C.cbl",
                "dependents": [_node("COSGN00C", "File", "app/cbl/COSGN00C.cbl")],
            }},
        )
        out = io.StringIO()
        with redirect_stdout(out):
            dde.analyze_app(fe, "carddemo", self.app_path,
                            os.path.join(self.graphs_dir, "carddemo.db"))
        text = out.getvalue()
        self.assertIn("DEAD_END_COBOL_CICS_CARDDEMO", text)
        self.assertIn("REST API", text)

    def test_mq_program_emits_question(self):
        self._write("cbl/CBMQHND.cbl", "CALL 'MQOPEN' USING HCONN.\n")
        fe = FakeEstate(
            nodes=[_node("CBMQHND", "Module", "app/cbl/CBMQHND.cbl", 10)],
            deps={"CBMQHND": {
                "file": "app/cbl/CBMQHND.cbl",
                "dependents": [_node("CBMQHND", "File", "app/cbl/CBMQHND.cbl")],
            }},
            sources={"CBMQHND": "PROCEDURE DIVISION.\nCALL 'MQOPEN' USING HCONN.\n"},
        )
        out = io.StringIO()
        with redirect_stdout(out):
            dde.analyze_app(fe, "carddemo", self.app_path,
                            os.path.join(self.graphs_dir, "carddemo.db"))
        text = out.getvalue()
        self.assertIn("DEAD_END_COBOL_MQ_CARDDEMO", text)
        self.assertIn("JMS/ActiveMQ", text)

    def test_called_program_emits_no_question(self):
        # A program WITH a real caller is not a dead-end even if it is in JCL.
        self._write("jcl/READACCT.jcl", "//STEP05 EXEC PGM=CSUTLDTC\n")
        fe = FakeEstate(
            nodes=[_node("CSUTLDTC", "Module", "app/cbl/CSUTLDTC.cbl", 19)],
            deps={"CSUTLDTC": {
                "file": "app/cbl/CSUTLDTC.cbl",
                "dependents": [
                    _node("CSUTLDTC", "File", "app/cbl/CSUTLDTC.cbl"),
                    _node("CORPT00C", "Module", "app/cbl/CORPT00C.cbl"),
                ],
            }},
        )
        out = io.StringIO()
        with redirect_stdout(out):
            dde.analyze_app(fe, "carddemo", self.app_path,
                            os.path.join(self.graphs_dir, "carddemo.db"))
        text = out.getvalue()
        self.assertNotIn("DEAD_END_COBOL", text)

    # ---- Java isolated interface ------------------------------------------

    def test_isolated_interface_emits_question(self):
        iface_file = "src/.../reader/IReaderStrategy.java"
        impl_file = "src/.../reader/CSVReader.java"
        fe = FakeEstate(
            nodes=[_node("IReaderStrategy", "Interface", iface_file, 7)],
            deps={"IReaderStrategy": {
                "file": iface_file,
                "dependents": [
                    _node("IReaderStrategy", "File", iface_file),
                    _node("CSVReader", "Class", impl_file, 10),
                ],
            }},
            sources={"CSVReader": "public class CSVReader implements IReaderStrategy {}"},
        )
        out = io.StringIO()
        with redirect_stdout(out):
            dde.analyze_app(fe, "ccps", None,
                            os.path.join(self.graphs_dir, "ccps.db"))
        text = out.getvalue()
        self.assertIn("DEAD_END_JAVA_INTERFACE_IREADERSTRATEGY", text)
        self.assertIn("preserve", text.lower())

    def test_referenced_interface_not_isolated(self):
        iface_file = "src/.../svc/IService.java"
        impl_file = "src/.../svc/ServiceImpl.java"
        client_file = "src/.../main/Client.java"
        fe = FakeEstate(
            nodes=[_node("IService", "Interface", iface_file, 5)],
            deps={"IService": {
                "file": iface_file,
                "dependents": [
                    _node("IService", "File", iface_file),
                    _node("ServiceImpl", "Class", impl_file, 8),
                    # A non-implementer client referencing the interface type.
                    _node("Client", "Class", client_file, 3),
                ],
            }},
            sources={
                "ServiceImpl": "public class ServiceImpl implements IService {}",
                "Client": "public class Client { IService svc; }",
            },
        )
        out = io.StringIO()
        with redirect_stdout(out):
            dde.analyze_app(fe, "ccps", None,
                            os.path.join(self.graphs_dir, "ccps.db"))
        text = out.getvalue()
        self.assertNotIn("DEAD_END_JAVA_INTERFACE", text)

    # ---- run_detection orchestration --------------------------------------

    def test_run_detection_skips_app_without_db(self):
        config = {"source_apps": [
            {"name": "carddemo", "path": self.app_path, "language": "cobol"},
        ]}
        fe = FakeEstate()
        out = io.StringIO()
        with redirect_stdout(out):
            dde.run_detection(config, we=fe)
        text = out.getvalue()
        self.assertIn("not found", text)
        self.assertIn("Dead-end analysis complete.", text)

    def test_run_detection_iterates_apps_with_db(self):
        self._touch_db("carddemo")
        self._write("jcl/READACCT.jcl", "//STEP05 EXEC PGM=CBACT01C\n")
        config = {"source_apps": [
            {"name": "carddemo", "path": self.app_path, "language": "cobol"},
        ]}
        fe = FakeEstate(
            nodes=[_node("CBACT01C", "Module", "app/cbl/CBACT01C.cbl", 22)],
            deps={"CBACT01C": {
                "file": "app/cbl/CBACT01C.cbl",
                "dependents": [_node("CBACT01C", "File", "app/cbl/CBACT01C.cbl")],
            }},
        )
        out = io.StringIO()
        with redirect_stdout(out):
            dde.run_detection(config, we=fe)
        text = out.getvalue()
        self.assertIn("Analyzing app 'carddemo'", text)
        self.assertIn("DEAD_END_COBOL_JCL_CARDDEMO", text)
        self.assertIn("Dead-end analysis complete.", text)

    def test_real_helper_shape_blast_radius_without_own_file(self):
        # The real wicked_estate.blast_radius returns {name, dependents} with NO
        # own-file key; _own_file_of must fall back to query() to self-exclude.
        self._write("jcl/READACCT.jcl", "//STEP05 EXEC PGM=CBACT01C\n")
        fe = FakeEstate(
            nodes=[_node("CBACT01C", '"module"', "app/cbl/CBACT01C.cbl", 22)],
            deps={"CBACT01C": {
                "file": "app/cbl/CBACT01C.cbl",
                "dependents": [_node("CBACT01C", "File", "app/cbl/CBACT01C.cbl")],
            }},
            omit_own_file=True,  # force the query() fallback path
        )
        out = io.StringIO()
        with redirect_stdout(out):
            dde.analyze_app(fe, "carddemo", self.app_path,
                            os.path.join(self.graphs_dir, "carddemo.db"))
        text = out.getvalue()
        # Self-File excluded -> uncalled -> JCL question fires.
        self.assertIn("DEAD_END_COBOL_JCL_CARDDEMO", text)

    def test_db_verbatim_kind_normalizes(self):
        # list_nodes hands back DB-verbatim kinds like '"module"' (quoted) and
        # estate object-kinds; _norm_kind must reduce both.
        self.assertEqual(dde._norm_kind('"module"'), "module")
        self.assertEqual(dde._norm_kind("Module"), "module")
        self.assertEqual(dde._norm_kind('{"other":"step"}'), "step")
        self.assertEqual(dde._norm_kind('Other("step")'), "step")
        self.assertEqual(dde._norm_kind('"interface"'), "interface")
        self.assertEqual(dde._norm_kind(None), "")

    def test_no_longer_reads_legacy_graph_json(self):
        # Regression guard: the deleted JSON intermediate must not be a
        # dependency. run_detection must work purely off config + helper.
        self._touch_db("carddemo")
        config = {"source_apps": [
            {"name": "carddemo", "path": self.app_path, "language": "cobol"},
        ]}
        self.assertFalse(os.path.exists(".anti-legacy/legacy_graph.json"))
        fe = FakeEstate(nodes=[], deps={})
        out = io.StringIO()
        with redirect_stdout(out):
            dde.run_detection(config, we=fe)
        self.assertIn("Dead-end analysis complete.", out.getvalue())


if __name__ == "__main__":
    unittest.main()
