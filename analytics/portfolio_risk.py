"""
Portfolio Risk Monitor — correlaciones, beta y concentración (HHI).

compute_daily_risk():
  - Descarga retornos 60 días para todas las posiciones + SPY
  - Calcula correlación, beta, HHI
  - Genera warnings si beta > 1.3, correlación > 0.80, HHI > 0.35

projected_beta_after_entry():
  - Estima la beta del portfolio tras añadir una posición nueva
  - Usada en execution_node para el cap de beta (1.4 reduce, 1.6 bloquea)
"""

import logging
from dataclasses import dataclass, field

import pandas as pd
import yfinance as yf

log = logging.getLogger(__name__)


@dataclass
class PortfolioRisk:
    portfolio_beta:     float
    beta_per_ticker:    dict
    max_corr_pair:      tuple
    max_corr_value:     float
    hhi_concentration:  float
    warnings:           list[str] = field(default_factory=list)
    n_positions:        int = 0


class PortfolioRiskMonitor:

    def compute_daily_risk(self, positions: dict) -> PortfolioRisk:
        """
        Calcula métricas de riesgo para las posiciones abiertas.
        Si hay < 2 posiciones devuelve un objeto mínimo sin warnings.
        """
        if len(positions) < 2:
            return PortfolioRisk(
                portfolio_beta=1.0, beta_per_ticker={},
                max_corr_pair=("", ""), max_corr_value=0.0,
                hhi_concentration=1.0 / max(len(positions), 1),
                n_positions=len(positions),
            )

        tickers = list(positions.keys())
        returns_df = self._download_returns(tickers + ["SPY"])
        if returns_df.empty or "SPY" not in returns_df.columns:
            log.warning("[risk] No se pudieron descargar retornos para el cálculo de riesgo")
            return PortfolioRisk(
                portfolio_beta=1.0, beta_per_ticker={},
                max_corr_pair=("", ""), max_corr_value=0.0,
                hhi_concentration=0.0, n_positions=len(positions),
            )

        spy_var = returns_df["SPY"].var()
        if spy_var == 0:
            spy_var = 1e-9

        # Beta por ticker
        beta_per_ticker: dict[str, float] = {}
        for t in tickers:
            if t not in returns_df.columns:
                beta_per_ticker[t] = 1.0
                continue
            cov = returns_df[t].cov(returns_df["SPY"])
            beta_per_ticker[t] = round(cov / spy_var, 3)

        # Pesos de posición
        ticker_prices = {}
        for t in tickers:
            pos = positions[t]
            ticker_prices[t] = pos.get("avg_price", 1.0)

        total_value = sum(
            positions[t].get("quantity", 0) * ticker_prices[t]
            for t in tickers
        )
        if total_value <= 0:
            total_value = 1.0

        weights = {
            t: (positions[t].get("quantity", 0) * ticker_prices[t]) / total_value
            for t in tickers
        }

        # Portfolio beta ponderada
        portfolio_beta = sum(weights[t] * beta_per_ticker.get(t, 1.0) for t in tickers)

        # Correlación máxima
        ticker_cols = [t for t in tickers if t in returns_df.columns]
        corr_matrix = returns_df[ticker_cols].corr() if len(ticker_cols) >= 2 else pd.DataFrame()

        max_corr_pair  = ("", "")
        max_corr_value = 0.0
        if not corr_matrix.empty:
            for i in range(len(ticker_cols)):
                for j in range(i + 1, len(ticker_cols)):
                    val = abs(corr_matrix.iloc[i, j])
                    if val > max_corr_value:
                        max_corr_value = val
                        max_corr_pair  = (ticker_cols[i], ticker_cols[j])

        # HHI (concentración)
        hhi = sum(w ** 2 for w in weights.values())

        risk = PortfolioRisk(
            portfolio_beta=round(portfolio_beta, 3),
            beta_per_ticker=beta_per_ticker,
            max_corr_pair=max_corr_pair,
            max_corr_value=round(max_corr_value, 3),
            hhi_concentration=round(hhi, 3),
            n_positions=len(positions),
        )
        risk.warnings = self.generate_warnings(risk)
        return risk

    def generate_warnings(self, risk: PortfolioRisk) -> list[str]:
        warnings = []
        if risk.portfolio_beta > 1.3:
            warnings.append(
                f"⚠️ Portfolio beta {risk.portfolio_beta:.2f} > 1.3 — sobreexposición al mercado"
            )
        if risk.max_corr_value > 0.80:
            t1, t2 = risk.max_corr_pair
            warnings.append(
                f"⚠️ Alta correlación: {t1}/{t2} = {risk.max_corr_value:.2f} — diversificación reducida"
            )
        if risk.hhi_concentration > 0.35:
            warnings.append(
                f"⚠️ Portfolio concentrado: HHI={risk.hhi_concentration:.2f} (>0.35)"
            )
        return warnings

    def projected_beta_after_entry(
        self,
        new_ticker:        str,
        new_quantity:      int,
        current_positions: dict,
        new_price:         float | None = None,
        portfolio_value:   float | None = None,
    ) -> float:
        """
        Estima la beta del portfolio si se añade new_ticker × new_quantity.
        Usado en execution_node para el cap de beta.

        portfolio_value debe ser el valor TOTAL del portfolio (cash + posiciones).
        Sin él, el cálculo solo usa el valor de las posiciones abiertas como
        denominador, lo que infla artificialmente el peso de cada nueva posición
        cuando el portfolio tiene mucho cash y pocas posiciones.
        """
        all_tickers = list(current_positions.keys()) + [new_ticker]
        returns_df  = self._download_returns(all_tickers + ["SPY"])

        spy_var = returns_df["SPY"].var() if "SPY" in returns_df.columns else 1e-9
        if spy_var == 0:
            spy_var = 1e-9

        def _beta(t: str) -> float:
            if t not in returns_df.columns or "SPY" not in returns_df.columns:
                return 1.0
            cov = returns_df[t].cov(returns_df["SPY"])
            return cov / spy_var

        if new_price is None:
            try:
                new_price = float(
                    yf.Ticker(new_ticker).fast_info.get("lastPrice", 100)
                )
            except Exception:
                new_price = 100.0

        all_pos = dict(current_positions)
        if new_ticker in all_pos:
            old = all_pos[new_ticker]
            new_qty = old.get("quantity", 0) + new_quantity
            old_avg = old.get("avg_price", new_price)
            new_avg = (old.get("quantity", 0) * old_avg + new_quantity * new_price) / new_qty
            all_pos[new_ticker] = {"quantity": new_qty, "avg_price": new_avg}
        else:
            all_pos[new_ticker] = {"quantity": new_quantity, "avg_price": new_price}

        # Valor total de las posiciones proyectadas (sin cash)
        invested_val = sum(
            p.get("quantity", 0) * p.get("avg_price", 1.0)
            for p in all_pos.values()
        )

        # Usar portfolio_value (cash + posiciones) como denominador si está disponible.
        # Esto evita inflar el peso de posiciones nuevas cuando hay mucho cash:
        # ej. $80 de ANET sobre $250k portfolio = 0.03%, no el 100% de $80 invertidos.
        total_val = portfolio_value if (portfolio_value and portfolio_value > invested_val) else invested_val
        if total_val <= 0:
            return _beta(new_ticker)

        portfolio_beta = sum(
            (p.get("quantity", 0) * p.get("avg_price", 1.0)) / total_val * _beta(t)
            for t, p in all_pos.items()
        )
        return round(portfolio_beta, 3)

    def format_for_report(self, risk: PortfolioRisk) -> str:
        beta_icon = "⚠️" if risk.portfolio_beta > 1.3 else "✅"
        t1, t2    = risk.max_corr_pair
        lines = [
            "📐 *PORTFOLIO RISK*",
            f"Beta agregada: {risk.portfolio_beta:.2f} {beta_icon}",
            f"Mayor correlación: {t1}/{t2} = {risk.max_corr_value:.2f}" if t1 else "Correlaciones: N/A",
            f"Concentración HHI: {risk.hhi_concentration:.2f}",
        ]
        for w in risk.warnings:
            lines.append(w)
        return "\n".join(lines)

    def _download_returns(self, tickers: list[str]) -> pd.DataFrame:
        """Descarga 60 días de retornos diarios. Devuelve DataFrame vacío si falla."""
        try:
            hist = yf.download(
                tickers,
                period="62d",
                auto_adjust=True,
                progress=False,
            )
            if hist.empty:
                return pd.DataFrame()

            # yf.download devuelve MultiIndex si >1 ticker
            if len(tickers) == 1:
                prices = hist["Close"].rename(columns={tickers[0]: tickers[0]}) if isinstance(hist["Close"], pd.DataFrame) else hist["Close"].to_frame(name=tickers[0])
            else:
                prices = hist["Close"] if "Close" in hist else hist.xs("Close", axis=1, level=0)

            return prices.pct_change().dropna()
        except Exception as e:
            log.warning(f"[risk] Error descargando retornos: {e}")
            return pd.DataFrame()
