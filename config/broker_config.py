"""
Configuración del broker. Cambiar BROKER_MODE para activar IBKR paper o live.
"""

from enum import Enum
from pathlib import Path


class BrokerMode(Enum):
    PAPER_LOCAL = "paper_local"  # simulación local, sin IBKR (fallback)
    PAPER_IBKR  = "paper_ibkr"  # cuenta paper IBKR (default)
    LIVE        = "live"         # cuenta real IBKR — requiere LIVE_ENABLED=True


# ── Guardrail: modo live deshabilitado hasta revisión manual ─────────────────
# Cambiar a True solo tras auditoría completa del sistema y aprobación explícita.
LIVE_ENABLED = False

# ── Modo activo ──────────────────────────────────────────────────────────────
_REQUESTED_MODE = BrokerMode.PAPER_IBKR

if _REQUESTED_MODE == BrokerMode.LIVE and not LIVE_ENABLED:
    raise NotImplementedError(
        "Modo LIVE deshabilitado. Cambiar LIVE_ENABLED=True en "
        "broker_config.py para habilitar (requiere revisión manual)."
    )

BROKER_MODE = _REQUESTED_MODE

# ── Conexión IB Gateway ──────────────────────────────────────────────────────
IBKR_HOST      = "127.0.0.1"
IBKR_PORT      = 4002          # IB Gateway paper (live = 4001)
IBKR_CLIENT_ID = 1
IBKR_ACCOUNT   = "DU1234567"
IBKR_CURRENCY  = "EUR"         # moneda base del portfolio

# ── Capital inicial IBKR paper ────────────────────────────────────────────────
# Usado para calcular pnl_total y pnl_total_pct en pnl_history cuando el modo
# es PAPER_IBKR o LIVE. Ajustar si la cuenta se recargó manualmente.
IBKR_INITIAL_CAPITAL = 250_000.0

# ── Persistencia local ────────────────────────────────────────────────────────
# Mapeo ticker → order IDs, entry_price, stop/TP precios — sobrevive reinicios
PROJECT_ROOT     = Path(__file__).resolve().parent.parent
LOCAL_STATE_PATH = PROJECT_ROOT / "data" / "ibkr_local_state.json"
