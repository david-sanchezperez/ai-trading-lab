"""
IBKRBroker: implementa BaseBroker sobre ib_insync.

Ningún módulo externo importa ib_insync directamente — toda la interacción
con IB pasa por esta clase.

_local_state persiste en JSON para sobrevivir reinicios del proceso:
  {ticker: {entry_order_id, stop_order_id, tp_order_id,
            entry_price, stop_price, tp_price, quantity, tp1_triggered}}
"""

import json
import logging
from datetime import datetime

from ib_insync import Stock

from brokers.base_broker import BaseBroker
from brokers.ibkr.gateway import IBGateway
from brokers.ibkr.market_data import get_live_price
from brokers.ibkr.orders import make_limit_entry, make_stop_child, make_tp_child, make_market_order
from brokers.ibkr.portfolio import get_account_summary, get_open_positions
from config.broker_config import IBKR_ACCOUNT, LOCAL_STATE_PATH

log = logging.getLogger(__name__)


def _load_local_state() -> dict:
    if LOCAL_STATE_PATH.exists():
        with open(LOCAL_STATE_PATH) as f:
            return json.load(f)
    return {}


def _save_local_state(state: dict) -> None:
    LOCAL_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(LOCAL_STATE_PATH, "w") as f:
        json.dump(state, f, indent=2)


class IBKRBroker(BaseBroker):
    """
    Broker IBKR paper/live vía ib_insync.
    Instanciar → connect() → operar → disconnect().
    """

    def __init__(self, client_id: int | None = None):
        self._gateway = IBGateway(client_id=client_id)
        self._local_state: dict = _load_local_state()

    @property
    def _ib(self):
        return self._gateway.ib

    def connect(self) -> bool:
        ok = self._gateway.connect()
        if not ok:
            return False
        self._validate_paper_account()
        return True

    def ensure_connected(self) -> bool:
        """
        Garantiza que la conexión está activa antes de operar.
        Si se perdió, intenta reconectar. Devuelve False si no lo consigue.
        """
        if self._gateway.is_connected():
            return True
        log.warning("[IBKRBroker] Reconectando antes de operar...")
        ok = self._gateway.connect()
        if ok:
            self._validate_paper_account()
        return ok

    def _validate_paper_account(self) -> None:
        """
        Garantiza que la cuenta conectada sea paper (prefijo 'DU').
        Si se detecta una cuenta live, desconecta inmediatamente y lanza excepción.
        """
        try:
            accounts = self._ib.managedAccounts()
        except Exception as e:
            self._gateway.disconnect()
            raise RuntimeError(f"No se pudieron verificar las cuentas IBKR: {e}") from e

        live_accounts = [a for a in accounts if not a.startswith("DU")]
        if live_accounts:
            self._gateway.disconnect()
            raise RuntimeError(
                f"LIVE ACCOUNT DETECTED — conexión bloqueada. "
                f"Solo se permite operar en paper trading. "
                f"Cuentas detectadas: {accounts}"
            )

        account_id = accounts[0] if accounts else "UNKNOWN"
        log.warning(
            f"⚠️  PAPER TRADING MODE — cuenta {account_id} — "
            f"ninguna orden afecta dinero real"
        )

    def disconnect(self) -> None:
        self._gateway.disconnect()

    # ── Datos de mercado ──────────────────────────────────────────────────────

    def get_price(self, ticker: str) -> float | None:
        try:
            return get_live_price(self._ib, ticker)
        except Exception as e:
            log.warning(f"[IBKRBroker] get_price({ticker}): {e}")
            return None

    def get_cash(self) -> float:
        try:
            return get_account_summary(self._ib)["cash_usd"]
        except Exception:
            return 0.0

    def get_portfolio_value(self) -> float:
        try:
            return get_account_summary(self._ib)["net_liq_usd"]
        except Exception:
            return 0.0

    def get_positions(self) -> dict:
        try:
            return get_open_positions(self._ib)
        except Exception as e:
            log.warning(f"[IBKRBroker] get_positions: {e}")
            return {}

    def get_pending_buy_tickers(self) -> set:
        """Devuelve set de tickers con órdenes BUY limit activas y sin rellenar."""
        try:
            return {
                t.contract.symbol
                for t in self._ib.openTrades()
                if t.order.action == "BUY"
                and t.contract.secType == "STK"
                and t.orderStatus.status not in ("Filled", "Cancelled", "Inactive")
            }
        except Exception as e:
            log.warning(f"[IBKRBroker] get_pending_buy_tickers: {e}")
            return set()

    # ── Órdenes ───────────────────────────────────────────────────────────────

    def place_bracket_order(self, ticker, action, qty, limit_price, stop_price, tp_price) -> dict:
        """
        Coloca bracket: limit entry (padre) + stop loss + take profit (hijos).
        Los tres se transmiten juntos al activar el TP (transmit=True).
        """
        if not self.ensure_connected():
            return {"status": "rejected", "reason": "No hay conexión con IB Gateway", "order_ids": {}}
        try:
            contract = Stock(ticker, "SMART", "USD")
            self._ib.qualifyContracts(contract)

            child_action = "SELL" if action == "BUY" else "BUY"

            parent = make_limit_entry(action, qty, limit_price)
            trade_parent = self._ib.placeOrder(contract, parent)
            parent_id = trade_parent.order.orderId

            oca_group = f"{ticker}_{parent_id}_exit"
            stop = make_stop_child(child_action, qty, stop_price, parent_id, oca_group=oca_group)
            tp   = make_tp_child(child_action, qty, tp_price, parent_id, oca_group=oca_group)

            trade_stop = self._ib.placeOrder(contract, stop)
            trade_tp   = self._ib.placeOrder(contract, tp)

            stop_id = trade_stop.order.orderId
            tp_id   = trade_tp.order.orderId

            self._local_state[ticker] = {
                "entry_order_id": parent_id,
                "stop_order_id":  stop_id,
                "tp_order_id":    tp_id,
                "entry_price":    limit_price,
                "stop_price":     stop_price,
                "tp_price":       tp_price,
                "quantity":       qty,
                "tp1_triggered":  False,
                "placed_at":      datetime.now().isoformat(),
            }
            _save_local_state(self._local_state)

            log.info(
                f"[IBKRBroker] Bracket {action} {ticker} {qty}@{limit_price:.2f} "
                f"stop={stop_price:.2f} tp={tp_price:.2f} "
                f"ids=({parent_id},{stop_id},{tp_id})"
            )
            return {
                "status":    "submitted",
                "order_ids": {"entry": parent_id, "stop": stop_id, "tp": tp_id},
                "trade": {
                    "action": action, "ticker": ticker,
                    "price": limit_price, "quantity": qty,
                },
            }
        except Exception as e:
            log.error(f"[IBKRBroker] place_bracket_order({ticker}): {e}")
            return {"status": "rejected", "reason": str(e), "order_ids": {}}

    def place_order(self, ticker, action, qty, price) -> dict:
        """
        Orden de mercado. Espera hasta 5s para confirmación de fill.
        Usada para TP1 (venta parcial) y cierre manual de posiciones.
        """
        if not self.ensure_connected():
            return {"status": "rejected", "reason": "No hay conexión con IB Gateway"}
        try:
            contract = Stock(ticker, "SMART", "USD")
            self._ib.qualifyContracts(contract)
            order = make_market_order(action, qty)
            trade = self._ib.placeOrder(contract, order)
            self._ib.sleep(5)

            filled_price = trade.orderStatus.avgFillPrice or price
            status = "filled" if trade.orderStatus.status in ("Filled", "Submitted") else "submitted"

            log.info(f"[IBKRBroker] Order {action} {ticker} {qty} → {status} @ {filled_price:.2f}")
            return {
                "status": status,
                "trade": {
                    "action": action, "ticker": ticker,
                    "price": filled_price, "quantity": qty,
                },
            }
        except Exception as e:
            log.error(f"[IBKRBroker] place_order({ticker}): {e}")
            return {"status": "rejected", "reason": str(e)}

    def modify_stop(self, ticker: str, new_stop: float) -> bool:
        """
        Mueve el stop loss de una posición abierta al nuevo precio.
        Localiza la orden por stop_order_id guardado en _local_state.
        """
        if not self.ensure_connected():
            log.error(f"[IBKRBroker] modify_stop({ticker}): sin conexión con IB Gateway")
            return False
        local = self._local_state.get(ticker)
        if not local or not local.get("stop_order_id"):
            log.warning(f"[IBKRBroker] modify_stop: sin estado local para {ticker}")
            return False

        stop_id = local["stop_order_id"]
        try:
            open_trades = {t.order.orderId: t for t in self._ib.openTrades()}
            if stop_id not in open_trades:
                log.warning(f"[IBKRBroker] Stop order {stop_id} no encontrada para {ticker}")
                return False

            trade = open_trades[stop_id]
            trade.order.auxPrice = new_stop  # StopOrder: precio en auxPrice
            self._ib.placeOrder(trade.contract, trade.order)  # modifica por mismo orderId

            self._local_state[ticker]["stop_price"] = new_stop
            _save_local_state(self._local_state)

            log.info(f"[IBKRBroker] Stop modificado: {ticker} → {new_stop:.2f}")
            return True
        except Exception as e:
            log.error(f"[IBKRBroker] modify_stop({ticker}): {e}")
            return False

    def cancel_order(self, order_id: int) -> bool:
        try:
            open_trades = {t.order.orderId: t for t in self._ib.openTrades()}
            if order_id not in open_trades:
                return True  # ya no existe o ya ejecutada
            self._ib.cancelOrder(open_trades[order_id].order)
            return True
        except Exception as e:
            log.error(f"[IBKRBroker] cancel_order({order_id}): {e}")
            return False
