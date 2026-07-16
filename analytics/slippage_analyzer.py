"""
Slippage Analyzer — registra y analiza el deslizamiento entre precio de señal
y precio de ejecución real en IBKR.

Alerta si slippage > 200 bps (2%) — señal de problema de liquidez.
"""

import csv
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from core.config import PROJECT_ROOT

log = logging.getLogger(__name__)

SLIPPAGE_CSV = PROJECT_ROOT / "data" / "slippage_log.csv"
_ALERT_THRESHOLD_BPS = 200


def _csv_headers() -> list[str]:
    return ["ticker", "signal_price", "fill_price", "slippage_bps",
            "quantity", "timestamp", "market_hour_cet"]


def _market_hour_bucket(dt: datetime) -> str:
    """Clasifica la hora CET en un bucket de liquidez."""
    try:
        from zoneinfo import ZoneInfo
        cet = dt.astimezone(ZoneInfo("Europe/Madrid"))
        h   = cet.hour
        if 15 <= h < 17:
            return "15:30-17:00"
        elif 17 <= h < 19:
            return "17:00-19:00"
        else:
            return "19:00-22:00"
    except Exception:
        return "unknown"


class SlippageAnalyzer:

    def log_execution(
        self,
        ticker:          str,
        signal_price:    float,
        fill_price:      float,
        quantity:        int,
        timestamp:       Optional[datetime] = None,
        market_hour_cet: Optional[str]      = None,
    ) -> float:
        """
        Registra una ejecución en el CSV de slippage.
        Devuelve slippage_bps. Envía alerta Telegram si > 200 bps.
        """
        if timestamp is None:
            timestamp = datetime.now()
        if market_hour_cet is None:
            market_hour_cet = _market_hour_bucket(timestamp)

        slippage_bps = 0.0
        if signal_price and signal_price > 0:
            slippage_bps = round((fill_price - signal_price) / signal_price * 10000, 2)

        SLIPPAGE_CSV.parent.mkdir(parents=True, exist_ok=True)
        file_exists = SLIPPAGE_CSV.exists()

        with open(SLIPPAGE_CSV, "a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=_csv_headers())
            if not file_exists:
                w.writeheader()
            w.writerow({
                "ticker":          ticker,
                "signal_price":    signal_price,
                "fill_price":      fill_price,
                "slippage_bps":    slippage_bps,
                "quantity":        quantity,
                "timestamp":       timestamp.isoformat(),
                "market_hour_cet": market_hour_cet,
            })

        if abs(slippage_bps) > _ALERT_THRESHOLD_BPS:
            log.warning(f"[slippage] HIGH SLIPPAGE: {ticker} {slippage_bps:.0f}bps")
            self._alert_high_slippage(ticker, slippage_bps)

        return slippage_bps

    def get_stats(self) -> dict:
        """Lee el CSV y calcula estadísticas de slippage."""
        if not SLIPPAGE_CSV.exists():
            return {"total_executions": 0}

        rows = []
        with open(SLIPPAGE_CSV, newline="") as f:
            for row in csv.DictReader(f):
                try:
                    rows.append({
                        "ticker":          row["ticker"],
                        "slippage_bps":    float(row["slippage_bps"]),
                        "quantity":        int(row["quantity"]),
                        "market_hour_cet": row["market_hour_cet"],
                    })
                except Exception:
                    continue

        if not rows:
            return {"total_executions": 0}

        all_bps = [r["slippage_bps"] for r in rows]
        mean_bps = sum(all_bps) / len(all_bps)

        # Por ticker
        by_ticker: dict[str, list[float]] = {}
        for r in rows:
            by_ticker.setdefault(r["ticker"], []).append(r["slippage_bps"])
        ticker_stats = {
            t: {"mean_bps": round(sum(v) / len(v), 1), "n": len(v)}
            for t, v in by_ticker.items()
        }

        # Por bucket horario
        by_hour: dict[str, list[float]] = {}
        for r in rows:
            by_hour.setdefault(r["market_hour_cet"], []).append(r["slippage_bps"])
        hour_stats = {
            h: {"mean_bps": round(sum(v) / len(v), 1), "n": len(v)}
            for h, v in by_hour.items()
        }

        # Peores 5 casos
        worst = sorted(rows, key=lambda r: abs(r["slippage_bps"]), reverse=True)[:5]

        return {
            "total_executions": len(rows),
            "mean_bps_overall": round(mean_bps, 1),
            "by_ticker":        ticker_stats,
            "by_hour":          hour_stats,
            "worst_5":          worst,
        }

    def format_for_report(self) -> str:
        stats = self.get_stats()
        if stats.get("total_executions", 0) == 0:
            return "Sin datos de slippage aún."
        worst = stats.get("worst_5", [])
        worst_str = f"{worst[0]['ticker']} {worst[0]['slippage_bps']:.0f}bps" if worst else "—"
        return (
            f"Avg slippage: {stats['mean_bps_overall']:.0f}bps | "
            f"Worst: {worst_str}"
        )

    def _alert_high_slippage(self, ticker: str, bps: float) -> None:
        try:
            from scheduler.notifier import send_notification
            send_notification(
                f"⚠️ *HIGH SLIPPAGE*: `{ticker}` {abs(bps):.0f}bps\n"
                f"Puede indicar un problema de liquidez. Revisar orden."
            )
        except Exception:
            pass


# ── Singleton ─────────────────────────────────────────────────────────────────

_analyzer_instance: Optional[SlippageAnalyzer] = None


def get_slippage_analyzer() -> SlippageAnalyzer:
    global _analyzer_instance
    if _analyzer_instance is None:
        _analyzer_instance = SlippageAnalyzer()
    return _analyzer_instance
