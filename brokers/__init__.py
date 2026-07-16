"""
Factory de broker. Importar get_broker() en lugar de instanciar directamente.
"""

from brokers.base_broker import BaseBroker


def get_broker() -> BaseBroker:
    from config.broker_config import BROKER_MODE, BrokerMode
    if BROKER_MODE == BrokerMode.PAPER_LOCAL:
        from brokers.paper_broker import PaperBroker
        return PaperBroker()
    else:
        from brokers.ibkr.broker import IBKRBroker
        return IBKRBroker()
