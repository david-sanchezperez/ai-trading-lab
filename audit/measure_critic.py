"""
Medición del valor añadido del analista crítico.

Ejecutable en cualquier momento con los datos acumulados:
  python -m audit.measure_critic            # imprime tablas + genera CSV
  python -m audit.measure_critic --no-csv   # solo imprime
  python -m audit.measure_critic --resolve  # fuerza resolución de outcomes nuevos

Pregunta central: ¿las evaluaciones CHALLENGED tuvieron peores retornos
que las APPROVED? (= el critic identifica correctamente señales problemáticas)

Salida:
  • Resumen por veredicto (APPROVED / CHALLENGED / APPROVED_ON_ERROR + fast-path)
  • Desglose por escenario del critic
  • Desglose por horizonte (T+1 / T+5 / T+20)
  • CSV: logs/decision_audit/critic_analysis_YYYY-MM-DD.csv
  • Aviso explícito cuando n < MIN_N_WARN para no concluir nada
"""

import argparse
import csv
import logging
import statistics
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Optional

from audit.counterfactual import get_enriched_evaluations, HORIZONS
from core.config import LOGS_DIR

log = logging.getLogger(__name__)

MIN_N_WARN    = 10   # n mínimo para extraer conclusiones
AUDIT_DIR     = LOGS_DIR / "decision_audit"
COL_WIDTH     = 14


# ── Clasificación de grupos ───────────────────────────────────────────────────

def _verdict_group(ev: dict) -> str:
    """
    Grupo primario para el análisis por veredicto.
    Distingue fast-path de LLM real dentro de APPROVED.
    """
    critic = ev.get("critic") or {}
    verdict = critic.get("verdict", "UNKNOWN")
    if critic.get("fast_path"):
        reason = critic.get("fast_path_reason") or ""
        if "SELL sin posición" in reason:
            return "fast_path:no_position"
        if "strong signal" in reason:
            return "fast_path:strong"
        if "weak signal" in reason:
            return "fast_path:weak"
        return "fast_path:other"
    return verdict  # APPROVED / CHALLENGED / APPROVED_ON_ERROR


def _scenario_group(ev: dict) -> str:
    critic = ev.get("critic") or {}
    return critic.get("scenario") or "unknown"


# ── Agregación de estadísticas ────────────────────────────────────────────────

class _Stats:
    def __init__(self):
        self.returns: list[float] = []
        self.wins: list[bool]     = []

    def add(self, ret: float, win: bool) -> None:
        self.returns.append(ret)
        self.wins.append(win)

    @property
    def n(self) -> int:
        return len(self.returns)

    @property
    def mean_return(self) -> Optional[float]:
        return statistics.mean(self.returns) if self.returns else None

    @property
    def median_return(self) -> Optional[float]:
        return statistics.median(self.returns) if self.returns else None

    @property
    def win_rate(self) -> Optional[float]:
        return sum(self.wins) / len(self.wins) if self.wins else None

    def low_sample_warning(self) -> bool:
        return self.n < MIN_N_WARN


def _collect(
    enriched: list[dict],
    group_fn,
    horizon: str,
) -> dict[str, _Stats]:
    """Agrupa evaluaciones por `group_fn` y acumula stats para `horizon`."""
    groups: dict[str, _Stats] = defaultdict(_Stats)
    for ev in enriched:
        out = (ev.get("outcomes") or {}).get(horizon)
        if out is None:
            continue
        signal = (ev.get("proposal") or {}).get("signal", "HOLD")
        if signal == "HOLD":
            continue  # sin dirección implícita — no contribuye al análisis
        group = group_fn(ev)
        groups[group].add(out["return"], out["win"])
    return dict(groups)


# ── Formateo ──────────────────────────────────────────────────────────────────

def _pct(v: Optional[float], decimals: int = 1) -> str:
    if v is None:
        return "—"
    return f"{v:+.{decimals}%}"


def _warn(stats: _Stats) -> str:
    return " ⚠ n<10" if stats.low_sample_warning() else ""


def _header(*cols: str) -> str:
    return "  ".join(c.ljust(COL_WIDTH) for c in cols)


def _row(*vals) -> str:
    return "  ".join(str(v).ljust(COL_WIDTH) for v in vals)


# ── Tablas de salida ──────────────────────────────────────────────────────────

def _print_verdict_table(enriched: list[dict]) -> list[dict]:
    """Imprime tabla por veredicto × horizonte. Devuelve filas para CSV."""
    print("\n" + "=" * 70)
    print("CRITIC VALUE — por veredicto × horizonte")
    print("=" * 70)
    print("(solo señales BUY/SELL con outcomes resueltos; HOLD excluido)")

    rows_csv = []
    for horizon, days in HORIZONS:
        groups = _collect(enriched, _verdict_group, horizon)
        if not groups:
            print(f"\n  T+{days}: sin datos aún")
            continue

        print(f"\n  T+{days} ({horizon})")
        print("  " + _header("Veredicto", "n", "ret_medio", "ret_mediana", "win_rate"))
        print("  " + "-" * 68)

        for group, s in sorted(groups.items()):
            warn = _warn(s)
            print("  " + _row(
                group[:COL_WIDTH],
                f"{s.n}{warn}",
                _pct(s.mean_return),
                _pct(s.median_return),
                _pct(s.win_rate),
            ))
            rows_csv.append({
                "tabla":        "veredicto",
                "horizonte":    horizon,
                "group":        group,
                "n":            s.n,
                "mean_return":  round(s.mean_return, 6) if s.mean_return is not None else "",
                "median_return": round(s.median_return, 6) if s.median_return is not None else "",
                "win_rate":     round(s.win_rate, 4) if s.win_rate is not None else "",
                "low_sample":   s.low_sample_warning(),
            })

    return rows_csv


def _print_scenario_table(enriched: list[dict]) -> list[dict]:
    """Imprime tabla por escenario del critic × horizonte T+5 (el más informativo)."""
    print("\n" + "=" * 70)
    print("CRITIC VALUE — por escenario del critic (T+5 únicamente)")
    print("=" * 70)
    print("(escenarios con n<3 omitidos)")

    rows_csv = []
    horizon, days = "t5", 5
    groups = _collect(enriched, _scenario_group, horizon)

    if not groups:
        print("  Sin datos T+5 aún.")
        return rows_csv

    print("  " + _header("Escenario", "n", "ret_medio", "win_rate", ""))
    print("  " + "-" * 68)

    for group, s in sorted(groups.items(), key=lambda x: -x[1].n):
        if s.n < 3:
            continue
        warn = _warn(s)
        print("  " + _row(
            group[:COL_WIDTH],
            f"{s.n}{warn}",
            _pct(s.mean_return),
            _pct(s.win_rate),
            "",
        ))
        rows_csv.append({
            "tabla":       "escenario",
            "horizonte":   horizon,
            "group":       group,
            "n":           s.n,
            "mean_return": round(s.mean_return, 6) if s.mean_return is not None else "",
            "win_rate":    round(s.win_rate, 4) if s.win_rate is not None else "",
            "low_sample":  s.low_sample_warning(),
        })

    return rows_csv


def _print_summary(enriched: list[dict]) -> None:
    """Resumen global del dataset: cobertura, señales evaluadas, resolved."""
    total     = len(enriched)
    with_outs = sum(1 for ev in enriched if (ev.get("outcomes") or {}).get("any_resolved"))
    buy_sell  = sum(
        1 for ev in enriched
        if (ev.get("proposal") or {}).get("signal") in ("BUY", "SELL")
    )
    challenged = sum(
        1 for ev in enriched
        if (ev.get("critic") or {}).get("verdict") == "CHALLENGED"
    )
    fast_path = sum(
        1 for ev in enriched
        if (ev.get("critic") or {}).get("fast_path")
    )

    print("\n" + "=" * 70)
    print("RESUMEN DEL DATASET")
    print("=" * 70)
    print(f"  Evaluaciones totales:   {total}")
    print(f"  BUY / SELL (analizables): {buy_sell}")
    print(f"  CHALLENGED:             {challenged}")
    print(f"  Fast-path:              {fast_path}")
    print(f"  Con ≥1 outcome resuelto: {with_outs}")

    if total == 0:
        print("\n  ⚠ Sin evaluaciones aún — el audit trail lleva acumulando "
              "desde el 2026-07-03. Ejecutar después de algunos ciclos de producción.")
        return

    if with_outs == 0:
        print("\n  ⚠ Sin outcomes resueltos aún — los horizontes mínimos son "
              "T+1 (≥3 días naturales), T+5 (≥7d), T+20 (≥22d).")

    if challenged < MIN_N_WARN:
        print(f"\n  ⚠ Solo {challenged} evaluaciones CHALLENGED (se necesitan "
              f"≥{MIN_N_WARN} para comparar con APPROVED con confianza).")


# ── CSV export ────────────────────────────────────────────────────────────────

def _export_csv(rows: list[dict], path: Path) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0].keys())
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)
    print(f"\n  CSV exportado → {path}")


# ── Entrypoint ────────────────────────────────────────────────────────────────

def run(resolve: bool = True, export_csv: bool = True) -> None:
    print("Cargando evaluaciones del audit trail...")
    enriched = get_enriched_evaluations(resolve=resolve)

    _print_summary(enriched)
    rows_verdict  = _print_verdict_table(enriched)
    rows_scenario = _print_scenario_table(enriched)

    if export_csv:
        csv_path = AUDIT_DIR / f"critic_analysis_{date.today()}.csv"
        _export_csv(rows_verdict + rows_scenario, csv_path)

    print()


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)

    parser = argparse.ArgumentParser(description="Mide el valor del analista crítico")
    parser.add_argument("--no-csv",   action="store_true", help="No exportar CSV")
    parser.add_argument("--resolve",  action="store_true",
                        help="Forzar resolución de outcomes antes del análisis "
                             "(por defecto True; --no-resolve desactiva)")
    parser.add_argument("--no-resolve", action="store_true",
                        help="No resolver — solo leer lo que ya está en disco")
    args = parser.parse_args()

    do_resolve = not args.no_resolve
    run(resolve=do_resolve, export_csv=not args.no_csv)
