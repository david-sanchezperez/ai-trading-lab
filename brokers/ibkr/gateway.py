"""
Gestión de conexión a IB Gateway. Reconexión automática si se pierde.
"""

import asyncio
import logging
import random
import time
from typing import Callable

from ib_insync import IB

from config.broker_config import IBKR_HOST, IBKR_PORT, IBKR_CLIENT_ID

log = logging.getLogger(__name__)


def _ensure_event_loop() -> None:
    """APScheduler/ThreadPoolExecutor threads no tienen event loop por defecto."""
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())


class IBGateway:

    # Puertos live de IBKR — nunca permitir conexión a estos
    _LIVE_PORTS = {4001, 7496}

    def __init__(self, client_id: int | None = None, on_disconnect: Callable | None = None):
        self._ib: IB | None = None
        self._client_id = client_id if client_id is not None else IBKR_CLIENT_ID
        self._on_disconnect = on_disconnect  # callback si la conexión se pierde inesperadamente

    # ── Acceso seguro ─────────────────────────────────────────────────────────

    @property
    def ib(self) -> IB:
        """Devuelve la conexión activa. Reconecta si se ha perdido."""
        if not self.is_connected():
            log.warning("[IBGateway] Conexión perdida — intentando reconexión automática")
            ok = self.connect()
            if not ok:
                raise ConnectionError(
                    "[IBGateway] No se pudo reconectar a IB Gateway tras múltiples intentos"
                )
        return self._ib

    def is_connected(self) -> bool:
        return self._ib is not None and self._ib.isConnected()

    # ── Conexión ──────────────────────────────────────────────────────────────

    def connect(self, max_retries: int = 3, retry_delay: float = 5.0) -> bool:
        """
        Conecta a IB Gateway con backoff exponencial entre reintentos.
        retry_delay es el delay base; cada intento duplica el tiempo de espera.
        """
        if IBKR_PORT in self._LIVE_PORTS:
            raise ConnectionError(
                f"Puerto {IBKR_PORT} es un puerto LIVE de IBKR — conexión bloqueada. "
                f"Solo se permiten puertos paper (4002, 7497)."
            )

        _ensure_event_loop()

        for attempt in range(1, max_retries + 1):
            try:
                # Limpiar conexión previa si existe
                if self._ib is not None:
                    try:
                        self._ib.disconnectedEvent -= self._handle_disconnect
                        if self._ib.isConnected():
                            self._ib.disconnect()
                    except Exception:
                        pass
                    self._ib = None

                self._ib = IB()
                self._ib.connect(IBKR_HOST, IBKR_PORT, clientId=self._client_id, readonly=False)
                self._ib.disconnectedEvent += self._handle_disconnect
                log.info(
                    f"[IBGateway] Conectado a {IBKR_HOST}:{IBKR_PORT} "
                    f"(clientId={self._client_id})"
                )
                return True

            except Exception as e:
                self._ib = None
                base  = retry_delay * (2 ** (attempt - 1))       # 5s, 10s, 20s…
                delay = base * random.uniform(0.75, 1.25)         # ±25% jitter
                if attempt < max_retries:
                    log.warning(
                        f"[IBGateway] Intento {attempt}/{max_retries} fallido: {e} "
                        f"— reintentando en {delay:.0f}s"
                    )
                    time.sleep(delay)
                else:
                    log.error(
                        f"[IBGateway] No se pudo conectar tras {max_retries} intentos: {e}"
                    )

        return False

    # ── Desconexión ───────────────────────────────────────────────────────────

    def disconnect(self) -> None:
        if self._ib is not None:
            try:
                self._ib.disconnectedEvent -= self._handle_disconnect
                if self._ib.isConnected():
                    self._ib.disconnect()
                    log.info("[IBGateway] Desconectado")
            except Exception:
                pass
            finally:
                self._ib = None

    # ── Eventos ───────────────────────────────────────────────────────────────

    def _handle_disconnect(self) -> None:
        """
        Callback de ib_insync cuando la conexión se pierde inesperadamente
        (no cuando se llama disconnect() manualmente).
        """
        log.error(
            f"[IBGateway] Conexión perdida inesperadamente "
            f"(clientId={self._client_id}, {IBKR_HOST}:{IBKR_PORT})"
        )
        if self._on_disconnect:
            try:
                self._on_disconnect()
            except Exception as e:
                log.warning(f"[IBGateway] Error en callback on_disconnect: {e}")

    # ── Context manager ───────────────────────────────────────────────────────

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *_args):
        self.disconnect()
