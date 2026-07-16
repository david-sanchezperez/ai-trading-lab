"""
Vigila movimientos de precio intraday para posiciones abiertas y el universo.

Genera PriceAlert cuando:
- Una posición cae > 1.5×ATR desde apertura (señal defensiva)
- Cualquier ticker del universo sube o baja > 1.0×ATR (señal oportunista)
"""

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd
import yfinance as yf

from config.monitor_config import MONITOR_CONFIG

log = logging.getLogger(__name__)

_DEF_CFG = MONITOR_CONFIG["defensive"]


@dataclass
class PriceAlert:
    ticker:          str
    severity:        str        # "HIGH" | "MEDIUM" | "INFO"
    direction:       str        # "UP" | "DOWN"
    move_pct:        float      # fracción, e.g. -0.032
    atr_multiple:    float      # cuántos ATRs representa el movimiento
    current_price:   float
    open_price:      float
    volume_anomaly:  bool
    timestamp:       str = field(default_factory=lambda: datetime.now().isoformat())


def _get_intraday(ticker: str) -> dict | None:
    """
    Obtiene datos intraday de yfinance: precio actual, apertura, volumen.
    Devuelve None si falla.
    """
    try:
        t = yf.Ticker(ticker)
        # Datos del día (intervalos de 5 min)
        hist_1d = t.history(period="1d", interval="5m")
        if hist_1d.empty:
            return None
        current_price = float(hist_1d["Close"].iloc[-1])
        open_price    = float(hist_1d["Open"].iloc[0])
        volume_today  = float(hist_1d["Volume"].sum())

        # Volumen promedio 20 días (datos diarios)
        hist_30d = t.history(period="30d")
        avg_volume = float(hist_30d["Volume"].mean()) if not hist_30d.empty else 0.0

        # ATR14 desde los últimos 20 días de datos diarios
        atr_14 = 0.0
        if len(hist_30d) >= 14:
            high = hist_30d["High"]
            low  = hist_30d["Low"]
            close_prev = hist_30d["Close"].shift(1)
            tr = pd.concat([
                high - low,
                (high - close_prev).abs(),
                (low  - close_prev).abs(),
            ], axis=1).max(axis=1)
            atr_14 = float(tr.rolling(14).mean().iloc[-1])

        return {
            "current_price": current_price,
            "open_price":    open_price,
            "volume_today":  volume_today,
            "avg_volume":    avg_volume,
            "atr_14":        atr_14,
        }
    except Exception as e:
        log.warning(f"[price_watcher] Error obteniendo datos de {ticker}: {e}")
        return None


def _build_alert(ticker: str, data: dict, threshold_atr: float) -> PriceAlert | None:
    """Construye PriceAlert si el movimiento supera el umbral dado en ATRs."""
    current = data["current_price"]
    open_p  = data["open_price"]
    atr     = data["atr_14"]

    if open_p <= 0 or atr <= 0:
        return None

    move_pct   = (current - open_p) / open_p
    atr_move   = abs(current - open_p)
    atr_mult   = atr_move / atr if atr > 0 else 0.0

    if atr_mult < threshold_atr:
        return None

    direction = "UP" if move_pct > 0 else "DOWN"
    vol_multiplier = _DEF_CFG["volume_anomaly_multiplier"]
    volume_anomaly = (
        data["avg_volume"] > 0
        and data["volume_today"] > vol_multiplier * data["avg_volume"]
    )

    severity = "HIGH" if atr_mult >= _DEF_CFG["price_move_atr_multiplier"] else "MEDIUM"

    return PriceAlert(
        ticker=ticker,
        severity=severity,
        direction=direction,
        move_pct=round(move_pct, 4),
        atr_multiple=round(atr_mult, 2),
        current_price=round(current, 4),
        open_price=round(open_p, 4),
        volume_anomaly=volume_anomaly,
    )


class PriceWatcher:

    def check_positions(self, positions: dict) -> list[PriceAlert]:
        """
        Comprueba las posiciones abiertas del portfolio.
        Genera HIGH/MEDIUM alerts cuando caen > defensive threshold.
        """
        alerts: list[PriceAlert] = []
        threshold = _DEF_CFG["price_move_atr_multiplier"]

        for ticker in positions:
            data = _get_intraday(ticker)
            if data is None:
                continue

            alert = _build_alert(ticker, data, threshold_atr=threshold)
            if alert and alert.direction == "DOWN":
                log.info(
                    f"[price_watcher] POSICIÓN {ticker}: "
                    f"{alert.move_pct:+.2%} ({alert.atr_multiple:.1f}×ATR) — {alert.severity}"
                )
                alerts.append(alert)
            time.sleep(0.5)

        return alerts

    def check_universe(self, tickers: list[str]) -> list[PriceAlert]:
        """
        Escanea el universo completo buscando movimientos excepcionales.
        Umbral: 1.0×ATR en cualquier dirección.
        """
        alerts: list[PriceAlert] = []

        for ticker in tickers:
            data = _get_intraday(ticker)
            if data is None:
                time.sleep(0.5)
                continue

            alert = _build_alert(ticker, data, threshold_atr=1.0)
            if alert:
                log.info(
                    f"[price_watcher] UNIVERSO {ticker}: "
                    f"{alert.move_pct:+.2%} ({alert.atr_multiple:.1f}×ATR) {alert.direction}"
                )
                alerts.append(alert)
            time.sleep(0.5)

        return alerts
