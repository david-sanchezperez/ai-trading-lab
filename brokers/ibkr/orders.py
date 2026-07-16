"""
Helpers para crear órdenes ib_insync.
No importa IB — trabaja con objetos Order puros para mantener la lógica separada.
"""

from ib_insync import LimitOrder, StopOrder, MarketOrder


def make_limit_entry(action: str, qty: int, limit_price: float) -> LimitOrder:
    order = LimitOrder(action, qty, limit_price)
    order.tif = "GTC"
    order.transmit = False  # no transmitir hasta adjuntar hijos
    return order


def make_stop_child(action: str, qty: int, stop_price: float, parent_id: int, oca_group: str = "") -> StopOrder:
    order = StopOrder(action, qty, stop_price)
    order.tif = "GTC"
    order.parentId = parent_id
    order.transmit = False
    if oca_group:
        order.ocaGroup = oca_group
        order.ocaType = 1  # cancelar la otra al hacer fill completo
    return order


def make_tp_child(action: str, qty: int, tp_price: float, parent_id: int, oca_group: str = "") -> LimitOrder:
    order = LimitOrder(action, qty, tp_price)
    order.tif = "GTC"
    order.parentId = parent_id
    order.transmit = True  # último hijo transmite el bracket completo
    if oca_group:
        order.ocaGroup = oca_group
        order.ocaType = 1  # cancelar la otra al hacer fill completo
    return order


def make_market_order(action: str, qty: int) -> MarketOrder:
    order = MarketOrder(action, qty)
    order.tif = "GTC"
    order.transmit = True
    return order
