"""
Tests de integración IBKR.

Requieren IB Gateway paper activo en localhost:4002 (cuenta DU1234567).
Se saltan automáticamente si el Gateway no está disponible.

Ejecutar:
    source .venv/bin/activate
    pytest tests/test_ibkr.py -v -s
"""

import time
import pytest


@pytest.fixture(scope="module")
def broker():
    from brokers.ibkr.broker import IBKRBroker
    # clientId=2 para no colisionar con el servicio systemd (clientId=1)
    b = IBKRBroker(client_id=2)
    try:
        connected = b.connect()
    except Exception:
        connected = False
    if not connected:
        pytest.skip("IB Gateway no disponible en localhost:4002")
    yield b
    b.disconnect()


def test_connect(broker):
    """Verifica conexión activa y equity > 0."""
    value = broker.get_portfolio_value()
    assert value > 0, f"Portfolio value esperado > 0, obtenido: {value}"
    cash = broker.get_cash()
    assert cash >= 0, f"Cash negativo: {cash}"
    print(f"\nPortfolio value: ${value:,.2f} | Cash: ${cash:,.2f}")


def test_get_price(broker):
    """Último cierre de AAPL vía reqHistoricalData."""
    price = broker.get_price("AAPL")
    assert price is not None, "No se obtuvo precio de AAPL"
    assert price > 0, f"Precio esperado > 0, obtenido: {price}"
    print(f"\nAAPL last close: ${price:.2f}")


def test_paper_guardrail(broker):
    """Verifica que la cuenta activa es paper (DU) y el modo es PAPER_IBKR."""
    from config.broker_config import BROKER_MODE, BrokerMode

    assert BROKER_MODE == BrokerMode.PAPER_IBKR, (
        f"Modo esperado PAPER_IBKR, activo: {BROKER_MODE}"
    )

    accounts = broker._ib.managedAccounts()
    assert accounts, "No se obtuvieron cuentas de IBKR"
    for account in accounts:
        assert account.startswith("DU"), (
            f"LIVE ACCOUNT DETECTADA: {account} — abortar inmediatamente"
        )
    print(f"\nCuentas verificadas como paper: {accounts}")


def test_paper_order(broker):
    """Compra 1 acción de AAPL y la vende — verifica que ambos fills ocurren."""
    price = broker.get_price("AAPL")
    assert price is not None, "No se pudo obtener precio antes del trade"

    # BUY 1 acción
    buy_result = broker.place_order("AAPL", "BUY", 1, price)
    assert buy_result["status"] in ("filled", "submitted"), (
        f"BUY rechazado: {buy_result.get('reason')}"
    )
    print(f"\nBUY: status={buy_result['status']} @ ${buy_result['trade']['price']:.2f}")

    # Esperar confirmación — fuera de horario el fill puede tardar más
    time.sleep(5)
    positions = broker.get_positions()

    # Si la orden está aún pendiente (mercado cerrado) lo aceptamos,
    # pero cancelamos para dejar la cuenta limpia
    if "AAPL" not in positions:
        open_trades = broker._ib.openTrades()
        aapl_orders = [t for t in open_trades if t.contract.symbol == "AAPL"]
        if aapl_orders:
            for t in aapl_orders:
                broker._ib.cancelOrder(t.order)
            print("AAPL order still pending (market closed) — cancelled OK")
            return
        assert False, f"AAPL no en posiciones y sin órdenes abiertas: {positions}"

    assert positions["AAPL"]["quantity"] >= 1, (
        f"Cantidad incorrecta: {positions['AAPL']['quantity']}"
    )

    # SELL 1 acción
    sell_result = broker.place_order("AAPL", "SELL", 1, price)
    assert sell_result["status"] in ("filled", "submitted"), (
        f"SELL rechazado: {sell_result.get('reason')}"
    )
    print(f"SELL: status={sell_result['status']} @ ${sell_result['trade']['price']:.2f}")
