"""Tests for the ngspice simulation step (circuit_data_gen/simulate.py).

TestTranslate works on captured ngspice 34 output and needs no simulator;
TestSimulation runs real ngspice and is skipped when PySpice or the ngspice
library is missing (the ``ngspice`` fixture).
"""

import pytest

from circuit_data_gen.simulate import SimResult, run_ngspice, simulation_validation, translate_errors

IGNORED = [r"no DC value, transient time 0 value used", r"has no value, DC 0 assumed"]


def _report(log: str, vectors: dict | None = None, exception: str | None = None) -> dict:
    return {"exception": exception, "plot": "op1", "vectors": vectors or {}, "log": log}


class TestTranslate:
    """ngspice's own words, as captured from ngspice 34, turned into
    instructions with the netlist's spelling."""

    def test_singular_node(self):
        log = "DC solution failed -\nWarning: singular matrix:  check nodes mid and mid\n"
        errors, _ = translate_errors(_report(log, exception="x"), "C1 in Mid 1u\n", IGNORED, 10)
        assert errors == [
            "Node Mid has no DC path to ground: at DC capacitors are open and current sources only set a "
            "current, so its voltage is undefined. Connect it to ground or to another node through a "
            "resistor (or another element that conducts at DC)."
        ]

    def test_singular_branch_names_the_element(self):
        log = "Warning: singular matrix:  check nodes lx#branch and lx#branch\n"
        errors, _ = translate_errors(_report(log, exception="x"), "Lx a 0 1u\n", IGNORED, 10)
        assert errors[0].startswith("Lx is part of a loop made only of voltage sources and/or inductors")

    def test_subcircuit_internal_node(self):
        log = "Warning: singular matrix:  check nodes x1.m and x1.m\n"
        errors, _ = translate_errors(_report(log, exception="x"), "X1 a 0 blk\n", IGNORED, 10)
        assert errors[0].startswith("Node m inside subcircuit instance X1 has no DC path")

    def test_rejected_line(self):
        log = "Error on line 4 :\nr1 a 0 2k\ndevice already exists, existing one being used\n"
        errors, _ = translate_errors(_report(log, exception="x"), "R1 a 0 1k\nr1 a 0 2k\n", IGNORED, 10)
        assert errors == [
            "ngspice rejected the line `r1 a 0 2k`: device already exists, existing one being used."
        ]

    def test_undefined_number(self):
        log = "Original line no.: 3, new internal line no.: 4:\nUndefined number [rx]\n"
        errors, _ = translate_errors(_report(log, exception="x"), "R1 a 0 {Rx}\n", IGNORED, 10)
        assert errors == ["ngspice: 'Rx' is undefined; define it with .param or write a number."]

    def test_listed_warnings_are_ignored(self):
        log = "Warning: v1: no DC value, transient time 0 value used\n"
        errors, ignored = translate_errors(_report(log, {"in": [1.0]}), "V1 in 0 SIN(1 2 1k)\n", IGNORED, 10)
        assert errors == []
        assert ignored == ["v1: no DC value, transient time 0 value used"]

    def test_unknown_warnings_reject(self):
        log = "Warning: something new happened\n"
        errors, _ = translate_errors(_report(log, {"in": [1.0]}), "V1 in 0 1\n", IGNORED, 10)
        assert errors == ["ngspice warning: something new happened"]

    def test_non_convergence(self):
        log = "doAnalyses: Too many iterations without convergence\n"
        errors, _ = translate_errors(
            _report(log, exception="NgSpiceCommandError"), "V1 a 0 1k\n", IGNORED, 10
        )
        assert errors[0].startswith("ngspice could not find the DC operating point")

    def test_timeout_and_crash(self):
        errors, _ = translate_errors(None, "", IGNORED, 7)
        assert errors == [
            "The simulation did not finish within 7 s. "
            "Simplify the circuit and use realistic component values."
        ]
        errors, _ = translate_errors({"crashed": 3, "log": ""}, "", IGNORED, 7)
        assert errors == ["The simulator process exited unexpectedly (exit code 3)."]

    def test_singular_node_ngspice_41_wording(self):
        # ngspice 41 names a single node ("check node mid"), 34 a pair
        log = "Warning: singular matrix:  check node mid\n"
        errors, _ = translate_errors(_report(log), "C1 in Mid 1u\n", IGNORED, 10)
        assert errors[0].startswith("Node Mid has no DC path to ground")

    def test_convergence_aid_rejects_a_rescued_solution(self):
        # ngspice 37+ falls back to a transient operating point and succeeds:
        # the operating point exists, but the circuit did not solve directly
        log = "Note: Transient op started\nNote: Transient op finished successfully\n"
        errors, _ = translate_errors(_report(log, {"in": [5.0], "mid": [2.5]}), "C1 in mid 1u\n", IGNORED, 10)
        assert errors[0].startswith("ngspice could only find the DC operating point with a convergence aid")

    def test_empty_vectors_are_no_operating_point(self):
        # ngspice 41 can leave an op plot whose vectors hold no values
        errors, _ = translate_errors(_report("", {"in": [], "v1#branch": []}), "V1 in 0 1k\n", IGNORED, 10)
        assert errors[0].startswith("ngspice could not find the DC operating point")

    def test_pyspice_false_alarm_does_not_fail_a_good_run(self):
        # PySpice raised on a harmless "Note:" line, but the operating point is there
        report = {**_report("Note: v1: has no value, DC 0 assumed\n", {"in": [1.0]}), "run_error": "x"}
        assert translate_errors(report, "V1 in 0 1\n", IGNORED, 10)[0] == []

    def test_fatal_device_error(self):
        # ngspice 32/34: an LTRA card with G != 0
        log = "Fatal error: om: Nonzero G (except RG) line not supported yet\n"
        errors, _ = translate_errors(_report(log), "O1 b 0 c 0 Om\n", IGNORED, 10)
        assert errors == ["ngspice rejected the circuit: om: Nonzero G (except RG) line not supported yet."]

    def test_duplicate_messages_are_merged(self):
        log = "Warning: singular matrix:  check nodes m and m\n" * 3
        errors, _ = translate_errors(_report(log, exception="x"), "C1 a m 1u\n", IGNORED, 10)
        assert len(errors) == 1

    def test_result_json(self):
        result = SimResult(ok=True, node_voltages={"a": 1.0}, branch_currents={"V1#branch": -1e-3})
        assert result.to_json() == {
            "analysis": "op",
            "ngspice_version": None,
            "node_voltages": {"a": 1.0},
            "branch_currents": {"V1#branch": -1e-3},
            "warnings": [],
        }


class TestSimulation:
    """Real ngspice runs of the cases the spike found."""

    def test_divider(self, ngspice):
        result = simulation_validation("V1 In 0 6\nR1 In Out 1k\nR2 Out 0 2k\n", timeout=30)
        assert result.ok, result.errors
        # names keep the netlist's spelling (ngspice lower-cases them)
        assert result.node_voltages == pytest.approx({"In": 6.0, "Out": 4.0})
        assert result.branch_currents["V1#branch"] == pytest.approx(-2e-3)

    @pytest.mark.parametrize(
        "netlist, message",
        [
            ("V1 in 0 5\nC1 in mid 1u\nC2 mid 0 1u\n", "Node mid has no DC path to ground"),
            ("I1 0 a 1m\nC1 a 0 1u\nR1 b 0 1k\nV1 b 0 1\n", "Node a has no DC path to ground"),
            ("V1 a 0 5\nV2 a 0 3\nR1 a 0 1k\n", "V1 is part of a loop"),
            ("V1 a 0 5\nL1 a 0 1u\nR1 a 0 1k\n", "L1 is part of a loop"),
            # ngspice 34 gives up ("could not find ..."); ngspice 41 first tries
            # its transient-op fallback ("could only find ... with a convergence aid")
            ("V1 in 0 1000\nD1 in 0 Dm\n.model Dm D\n", "find the DC operating point"),
        ],
    )
    def test_invalid_circuits_rejected(self, ngspice, netlist, message):
        result = simulation_validation(netlist, timeout=30)
        assert not result.ok
        assert any(message in e for e in result.errors), result.errors

    def test_gmin_stepping_does_not_rescue(self, ngspice):
        # without the exporter's options ngspice would gmin-step this node
        # to a "solution"; with them it is rejected
        result = simulation_validation("V1 in 0 5\nC1 in mid 1u\nC2 mid 0 1u\n", timeout=30)
        assert not result.ok and not result.node_voltages

    def test_undriven_circuit_rejected(self, ngspice):
        # an AC-only source is 0 V at DC: valid, but nothing is driven
        result = simulation_validation("V1 in 0 AC 1\nR1 in 0 1k\n", timeout=30)
        assert not result.ok
        assert result.errors[0].startswith("Every node voltage is 0 V")
        # ngspice 34 reports "has no value, DC 0 assumed" as an (ignored)
        # warning, ngspice 41 as a note; either way it must not reject the run
        assert not any(e.startswith("ngspice warning") for e in result.errors)

    def test_sin_source_uses_its_offset(self, ngspice):
        result = simulation_validation("V1 in 0 SIN(1 2 1k)\nR1 in 0 1k\n", timeout=30)
        assert result.ok, result.errors
        assert result.node_voltages == pytest.approx({"in": 1.0})

    def test_subcircuit(self, ngspice):
        text = (
            "V1 in 0 4\nR1 in a 1k\nX1 a 0 half\n"
            ".subckt half p n params: r=1k\nR9 p m {r}\nR8 m n {r}\n.ends\n"
        )
        result = simulation_validation(text, timeout=30)
        assert result.ok, result.errors
        # 4 V over R1 (1k) in series with the 2k inside X1: m sits at 4 * 1k / 3k
        assert result.node_voltages["m inside subcircuit instance X1"] == pytest.approx(4 / 3)

    def test_pinned_version_is_loaded_and_recorded(self, ngspice):
        from circuit_data_gen.simulate import NGSPICE_VERSION  # noqa: PLC0415

        result = simulation_validation("V1 a 0 1\nR1 a 0 1k\n", timeout=30)
        assert result.ngspice_version == NGSPICE_VERSION
        assert result.to_json()["ngspice_version"] == NGSPICE_VERSION

    def test_other_version_fails_fast(self, ngspice, monkeypatch):
        from circuit_data_gen import simulate  # noqa: PLC0415

        monkeypatch.setattr(simulate, "NGSPICE_VERSION", 99)
        with pytest.raises(RuntimeError, match=r"needs ngspice 99, but ngspice \d+ was loaded"):
            simulate.check_ngspice()

    def test_timeout_kills_the_child(self, ngspice):
        # far too short for a process to even start: reported as a timeout
        assert run_ngspice("* t\nV1 a 0 1\nR1 a 0 1\n.op\n.end\n", timeout=0.001) is None
        result = simulation_validation("V1 a 0 1\nR1 a 0 1\n", timeout=0.001)
        assert result.timed_out and not result.ok
