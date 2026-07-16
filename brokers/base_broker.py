"""
Interfaz abstracta para brokers.
Ningún nodo de LangGraph importa ib_insync directamente — solo usan BaseBroker.
"""

from abc import ABC, abstractmethod


class BaseBroker(ABC):

    @abstractmethod
    def connect(self) -> bool:
        """Establece conexión. Devuelve True si OK."""

    @abstractmethod
    def disconnect(self) -> None:
        """Cierra la conexión."""

    @abstractmethod
    def get_price(self, ticker: str) -> float | None:
        """Precio de mercado actual. None si falla."""

    @abstractmethod
    def get_cash(self) -> float:
        """Cash disponible (USD para cuentas IBKR)."""

    @abstractmethod
    def get_portfolio_value(self) -> float:
        """Valor total del portfolio: cash + posiciones a precio de mercado."""

    @abstractmethod
    def get_positions(self) -> dict:
        """Posiciones abiertas: {ticker: {"quantity": int, "avg_price": float}}"""

    @abstractmethod
    def place_bracket_order(
        self,
        ticker: str,
        action: str,
        qty: int,
        limit_price: float,
        stop_price: float,
        tp_price: float,
    ) -> dict:
        """
        Coloca una orden bracket (entrada limit + stop loss + take profit).

        Returns:
            {
                "status":    "submitted" | "filled" | "rejected",
                "order_ids": {"entry": int, "stop": int, "tp": int},
                "trade":     {"action", "ticker", "price", "quantity"},
                "reason":    str (solo si rejected),
            }
        """

    @abstractmethod
    def place_order(self, ticker: str, action: str, qty: int, price: float) -> dict:
        """
        Orden de mercado simple.
        Usada para TP1 (venta parcial) y cierres manuales.

        Returns: {"status": "filled"|"rejected", "trade": {...}, "reason": str}
        """

    @abstractmethod
    def modify_stop(self, ticker: str, new_stop: float) -> bool:
        """
        Modifica el precio del stop loss de una posición abierta.
        En PAPER_LOCAL es no-op (el stop se calcula dinámicamente).
        Returns True si la modificación tuvo éxito.
        """

    @abstractmethod
    def cancel_order(self, order_id: int) -> bool:
        """Cancela una orden pendiente por ID. True si OK o ya no existía."""
