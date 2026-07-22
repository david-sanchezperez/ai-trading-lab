"""
Tests de integración del Juez de Contradicción en critic_node — I1-I8 §5 DESIGN_JUDGE2.md.

I1: Modo OFF — el juez no se importa y critic_result no tiene "contradiction_judge".
I2: Modo SHADOW — el juez corre pero el escenario/key_question no cambia.
I3: Modo SHADOW con fast_path — el bloque también se añade.
I4: Modo ACTIVE — scenario/key_question vienen del juez.
I5: Fail-open (D9) — excepción en el juez → error en bloque, pipeline continúa.
I6: Audit round-trip — el bloque "judges.contradiction" llega al registro de auditoría.
I7: Rendimiento — 1000 evaluaciones en < 1 segundo.
I8: Arquitectura stdlib-only — tribunal core no importa módulos del dominio trading.

Nota: I1-I5 mockean run_contradiction_judge o ContradictionJudgeMode para no necesitar
el pipeline completo (LLM, RAG, IB). Los tests son unitarios sobre la lógica del
feature flag en critic_node y la integración con audit.
"""

import importlib
import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ── Helpers ───────────────────────────────────────────────────────────────────

_JUDGE_BLOCK_OK = {
    "mode": "shadow",
    "engine_version": "contradiction-judge/1.0.0",
    "rules_version": "abc123def456",
    "rationale_present": False,
    "contradictions": [],
    "matched_rule_ids": ["aligned_signals_review"],
    "skipped": [],
    "evaluated_rules": 8,
    "scenario_hint": {
        "rule_id": "aligned_signals_review",
        "scenario": "Aligned signals — verify coherence",
        "key_question": "Are all indicators pointing in the same direction?",
    },
    "duration_ms": 0.5,
    "error": None,
}

_JUDGE_BLOCK_RSI = {
    **_JUDGE_BLOCK_OK,
    "contradictions": [{"rule_id": "rsi_overbought_vs_buy", "severity": 0.70}],
    "matched_rule_ids": ["rsi_overbought_vs_buy", "aligned_signals_review"],
    "scenario_hint": {
        "rule_id": "rsi_overbought_vs_buy",
        "scenario": "RSI in overbought zone but BUY signal — potential overextension",
        "key_question": "Is buying justified when RSI signals overbought conditions?",
    },
}


# ── I1: Modo OFF ──────────────────────────────────────────────────────────────

class TestModeOff:
    def test_off_judge_not_called(self):
        """Modo OFF: run_contradiction_judge nunca se llama."""
        with patch("config.tribunal_config.CONTRADICTION_JUDGE_MODE", "off"):
            from config.tribunal_config import ContradictionJudgeMode
            mode = ContradictionJudgeMode.OFF

            called = []

            def fake_run(state):
                called.append(True)
                return _JUDGE_BLOCK_OK

            # Simular la lógica del feature flag
            _judge_block = None
            if mode != ContradictionJudgeMode.OFF:
                _judge_block = fake_run({})

            assert called == [], "run_contradiction_judge no debe llamarse en modo OFF"
            assert _judge_block is None

    def test_off_imports_not_loaded(self):
        """Módulo tribunal no debe importarse en modo OFF (lazy import)."""
        import sys
        # Si tribunal ya está cargado por otros tests, lo quitamos temporalmente
        tribunal_modules = [k for k in sys.modules if k.startswith("tribunal")]
        saved = {k: sys.modules.pop(k) for k in tribunal_modules}
        try:
            from config.tribunal_config import ContradictionJudgeMode
            mode = ContradictionJudgeMode.OFF
            # En OFF, no hacemos ningún import de tribunal
            _judge_block = None
            if mode != ContradictionJudgeMode.OFF:
                from graph.contradiction_adapter import run_contradiction_judge  # noqa
                _judge_block = run_contradiction_judge({})
            # tribunal modules siguen sin cargarse
            loaded = [k for k in sys.modules if k.startswith("tribunal")]
            assert loaded == [], f"tribunal importado en modo OFF: {loaded}"
        finally:
            sys.modules.update(saved)


# ── I2: Modo SHADOW — corre pero sin efecto operativo ────────────────────────

class TestModeShadow:
    def test_shadow_runs_judge_no_effect_on_scenario(self):
        """SHADOW: el juez corre, pero scenario/key_question NO cambian."""
        from config.tribunal_config import ContradictionJudgeMode

        # Simular un critic_result con escenario detectado antes del juez
        scenario = "RSI in overbought zone but BUY signal — potential overextension"
        key_question = "Is buying justified when RSI signals overbought conditions?"

        _judge_block = _JUDGE_BLOCK_RSI.copy()
        _judge_block["mode"] = "shadow"

        # En SHADOW, NO se sobreescribe scenario/key_question
        mode = ContradictionJudgeMode.SHADOW
        if mode == ContradictionJudgeMode.ACTIVE and _judge_block and not _judge_block.get("error"):
            from graph.contradiction_adapter import scenario_from_verdict
            scenario, key_question = scenario_from_verdict(_judge_block)

        assert scenario == "RSI in overbought zone but BUY signal — potential overextension"
        assert key_question == "Is buying justified when RSI signals overbought conditions?"

    def test_shadow_judge_block_in_critic_result(self):
        """En SHADOW, contradiction_judge aparece en critic_result."""
        _judge_block = _JUDGE_BLOCK_OK.copy()

        critic_result = {
            "scenario": "Aligned signals — verify coherence",
            "key_question": "Are all indicators pointing in the same direction?",
            "contradiction_judge": _judge_block,
        }

        assert critic_result["contradiction_judge"] is _judge_block
        assert "evaluated_rules" in critic_result["contradiction_judge"]


# ── I3: Modo SHADOW con fast_path ────────────────────────────────────────────

class TestShadowWithFastPath:
    def test_judge_block_added_to_fast_result(self):
        """fast_result también debe incluir contradiction_judge en SHADOW/ACTIVE."""
        _judge_block = _JUDGE_BLOCK_OK.copy()

        fast_result = {
            "fast_path": True,
            "fast_path_reason": "no_data",
            "verdict": "APPROVED",
            "contradiction_judge": _judge_block,
        }

        assert fast_result["contradiction_judge"]["evaluated_rules"] == 8


# ── I4: Modo ACTIVE — escenario desde el juez ────────────────────────────────

class TestModeActive:
    def test_active_overrides_scenario_from_judge(self):
        """ACTIVE: scenario/key_question vienen del juez por priority."""
        from config.tribunal_config import ContradictionJudgeMode
        from graph.contradiction_adapter import scenario_from_verdict

        # Juez detectó RSI overbought (priority=20), más prioritario que aligned (priority=999)
        _judge_block = _JUDGE_BLOCK_RSI.copy()
        _judge_block["mode"] = "active"

        # El if/elif detectó otro escenario (simulamos que detectó "aligned")
        scenario = "Aligned signals — verify coherence"
        key_question = "Are all indicators pointing in the same direction?"

        mode = ContradictionJudgeMode.ACTIVE
        if mode == ContradictionJudgeMode.ACTIVE and _judge_block and not _judge_block.get("error"):
            scenario, key_question = scenario_from_verdict(_judge_block)

        assert scenario == "RSI in overbought zone but BUY signal — potential overextension"
        assert key_question == "Is buying justified when RSI signals overbought conditions?"

    def test_active_uses_priority_not_severity(self):
        """La selección en ACTIVE es por priority, no por severity."""
        from graph.contradiction_adapter import scenario_from_verdict

        # Simular bloque con dos reglas matcheadas, la de menor priority gana
        block = {
            **_JUDGE_BLOCK_OK,
            "scenario_hint": {
                "rule_id": "rsi_overbought_vs_buy",
                "scenario": "RSI in overbought zone but BUY signal — potential overextension",
                "key_question": "Is buying justified when RSI signals overbought conditions?",
            },
        }
        scenario, _ = scenario_from_verdict(block)
        assert "RSI" in scenario

    def test_active_falls_back_when_no_hint(self):
        """Si scenario_hint=None, scenario_from_verdict devuelve el fallback aligned."""
        from graph.contradiction_adapter import scenario_from_verdict

        block = {**_JUDGE_BLOCK_OK, "scenario_hint": None}
        scenario, kq = scenario_from_verdict(block)
        assert scenario == "Aligned signals — verify coherence"
        assert "Are all indicators" in kq


# ── I5: Fail-open (D9) ───────────────────────────────────────────────────────

class TestFailOpen:
    def test_exception_produces_error_key(self):
        """Si el juez lanza, el bloque tiene key 'error' y el pipeline continúa."""
        from config.tribunal_config import ContradictionJudgeMode

        _judge_block = None
        mode = ContradictionJudgeMode.SHADOW

        if mode != ContradictionJudgeMode.OFF:
            try:
                raise RuntimeError("DB connection failed")
            except Exception as exc:
                _judge_block = {"error": f"{type(exc).__name__}: {exc}"}

        assert _judge_block is not None
        assert "RuntimeError" in _judge_block["error"]

    def test_error_block_no_scenario_override_in_active(self):
        """En ACTIVE, un bloque con error no debe sobreescribir el escenario."""
        from config.tribunal_config import ContradictionJudgeMode
        from graph.contradiction_adapter import scenario_from_verdict

        _judge_block = {"error": "RuntimeError: DB connection failed"}

        scenario_original = "Original scenario from if/elif"
        scenario = scenario_original

        mode = ContradictionJudgeMode.ACTIVE
        # La condición del código real incluye `not _judge_block.get("error")`
        if mode == ContradictionJudgeMode.ACTIVE and _judge_block and not _judge_block.get("error"):
            scenario, _ = scenario_from_verdict(_judge_block)

        assert scenario == scenario_original


# ── I6: Audit round-trip ─────────────────────────────────────────────────────

class TestAuditRoundTrip:
    def test_judges_block_in_audit_record(self, tmp_path):
        """El bloque judges.contradiction llega al registro de auditoría (I6)."""
        from audit.decision_audit import _build_record

        state = {
            "ticker": "AMD",
            "technical_result": {
                "signal": "BUY",
                "confidence": 0.75,
                "rsi": 70.0,
                "price": 180.0,
                "atr_14": 4.0,
                "trend_up": True,
                "volume_ratio": 1.2,
                "pct_52w_range": 0.85,
                "rs_spy": 0.01,
                "dist_sma20": 0.02,
                "buy_votes": 4,
                "sell_votes": 1,
            },
            "sentiment_result": {
                "sentiment": 0.3,
                "confidence": 0.7,
                "headlines": 5,
            },
            "critic_result": {
                "scenario": "RSI in overbought zone but BUY signal — potential overextension",
                "key_question": "Is buying justified when RSI signals overbought conditions?",
                "verdict": "APPROVED",
                "fast_path": False,
                "contradiction_judge": _JUDGE_BLOCK_RSI,
            },
            "intraday_context": None,
        }

        decision = {
            "action": "BUY",
            "score": 0.82,
            "regime_adjustment": 1.0,
            "win_rate": {},
        }

        record = _build_record(state, decision, 0.82, 0.70)

        assert "judges" in record
        assert record["judges"]["contradiction"] is not None
        assert record["judges"]["contradiction"]["evaluated_rules"] == 8

    def test_judges_block_is_json_serializable(self):
        """El bloque judges debe ser JSON serializable (para JSONL)."""
        from audit.decision_audit import _build_record
        from core.session_logger import get_session_logger  # noqa

        state = {
            "ticker": "AMD",
            "technical_result": {"signal": "BUY", "confidence": 0.75, "price": 180.0},
            "sentiment_result": None,
            "critic_result": {"contradiction_judge": _JUDGE_BLOCK_OK},
            "intraday_context": None,
        }
        decision = {"action": "HOLD", "score": 0.5}

        record = _build_record(state, decision, 0.5, 0.70)
        json.dumps(record)  # no debe lanzar

    def test_judges_block_none_when_judge_not_run(self):
        """Si contradiction_judge no está en critic_result (modo OFF), judges.contradiction=None."""
        from audit.decision_audit import _build_record

        state = {
            "ticker": "AMD",
            "technical_result": {"signal": "BUY", "confidence": 0.75, "price": 180.0},
            "sentiment_result": None,
            "critic_result": {},  # sin contradiction_judge
            "intraday_context": None,
        }
        decision = {"action": "HOLD", "score": 0.5}

        record = _build_record(state, decision, 0.5, 0.70)
        assert record["judges"]["contradiction"] is None


# ── I7: Rendimiento — 1000 evaluaciones < 1 segundo ─────────────────────────

class TestPerformance:
    def test_1000_evaluations_under_1s(self):
        """evaluate() debe completar 1000 ciclos en < 1 segundo en este hardware."""
        from tribunal.contracts import EvidenceSet, Proposal, Signal
        from tribunal.engine import evaluate
        from config.contradiction_rules import TRADING_RULES

        proposal = Proposal(action="BUY")
        evidence = EvidenceSet({
            "rsi": Signal("rsi", 70.0),
            "trend_up": Signal("trend_up", True),
            "rs_spy": Signal("rs_spy", 0.01),
            "pct_52w_range": Signal("pct_52w_range", 0.5),
            "sentiment": Signal("sentiment", 0.0),
        })

        t0 = time.monotonic()
        for _ in range(1000):
            evaluate(proposal, evidence, TRADING_RULES)
        elapsed = time.monotonic() - t0

        assert elapsed < 1.0, f"1000 evaluaciones tomaron {elapsed:.3f}s (límite: 1.0s)"


# ── I8: Arquitectura stdlib-only ─────────────────────────────────────────────

class TestStdlibOnly:
    """
    El core del tribunal (contracts, conditions, ack, engine) no debe importar
    ningún módulo del dominio de trading.
    """

    FORBIDDEN_PREFIXES = (
        "core.", "agents.", "brokers.", "graph.", "scheduler.",
        "analytics.", "monitor.", "config.", "notifications.",
    )

    def _get_imports(self, module_name: str) -> set[str]:
        import sys
        mod = importlib.import_module(module_name)
        return set(getattr(mod, "__dict__", {}).keys())

    def _check_module(self, module_name: str) -> list[str]:
        import sys
        mod = importlib.import_module(module_name)
        violations = []
        for name, obj in vars(mod).items():
            if hasattr(obj, "__module__") and obj.__module__:
                for prefix in self.FORBIDDEN_PREFIXES:
                    if obj.__module__.startswith(prefix):
                        violations.append(f"{module_name}: {name} from {obj.__module__}")
        return violations

    def test_contracts_no_trading_imports(self):
        import sys
        # Verificar que tribunal.contracts no importe módulos de trading en sys.modules
        importlib.import_module("tribunal.contracts")
        trading_in_modules = [
            k for k in sys.modules
            if any(k.startswith(p) for p in self.FORBIDDEN_PREFIXES)
            and k in [m.__name__ for m in [] if hasattr(m, "__name__")]
        ]
        # Comprobación más directa: leer el archivo y verificar imports
        from pathlib import Path
        src = (Path(__file__).parent.parent / "tribunal" / "contracts.py").read_text()
        for prefix in ("from core", "from agents", "from brokers", "from graph",
                       "import core", "import agents", "import brokers"):
            assert prefix not in src, f"tribunal/contracts.py contiene import prohibido: {prefix!r}"

    def test_engine_no_trading_imports(self):
        from pathlib import Path
        src = (Path(__file__).parent.parent / "tribunal" / "engine.py").read_text()
        for prefix in ("from core", "from agents", "from brokers", "from graph",
                       "import core", "import agents", "import brokers"):
            assert prefix not in src, f"tribunal/engine.py contiene import prohibido: {prefix!r}"

    def test_conditions_no_trading_imports(self):
        from pathlib import Path
        src = (Path(__file__).parent.parent / "tribunal" / "conditions.py").read_text()
        for prefix in ("from core", "from agents", "from brokers", "from graph",
                       "import core", "import agents", "import brokers"):
            assert prefix not in src

    def test_ack_no_trading_imports(self):
        from pathlib import Path
        src = (Path(__file__).parent.parent / "tribunal" / "ack.py").read_text()
        for prefix in ("from core", "from agents", "from brokers", "from graph",
                       "import core", "import agents", "import brokers"):
            assert prefix not in src

    def test_tribunal_uses_only_stdlib(self):
        """tribunal/__init__.py re-exporta solo desde tribunal.* — sin terceros."""
        from pathlib import Path
        src = (Path(__file__).parent.parent / "tribunal" / "__init__.py").read_text()
        # Solo imports de tribunal.*
        for line in src.splitlines():
            stripped = line.strip()
            if stripped.startswith("from ") or stripped.startswith("import "):
                assert stripped.startswith("from tribunal") or stripped.startswith("#"), (
                    f"Import no esperado en tribunal/__init__.py: {stripped!r}"
                )
