"""
Test de conectividad IBKR en modo headless (sin sesión de usuario).

Uso:
  1. Aplica el sudoers: sudo cp config/sudoers-trading /etc/sudoers.d/trading && sudo chmod 440 /etc/sudoers.d/trading
  2. Reinicia el scheduler: sudo systemctl restart trading-scheduler.service
  3. Ejecuta este script: python3 scripts/test_headless.py &
  4. Deslogéate
  5. Espera 5 minutos y vuelve a loguearte
  6. Revisa: cat /tmp/headless_test.log

El script comprueba cada 30s durante 10 minutos si el gateway responde.
"""

import asyncio
import logging
import socket
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

LOG_FILE = Path("/tmp/headless_test.log")
log = logging.getLogger("headless_test")

def _setup_log():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(message)s",
        handlers=[
            logging.FileHandler(LOG_FILE, mode="w"),
            logging.StreamHandler(),
        ],
    )

def _is_port_open(host="127.0.0.1", port=4002, timeout=3.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False

def _test_ibkr_api() -> tuple[bool, str]:
    """Prueba conexión ib_insync real. Retorna (ok, mensaje)."""
    import nest_asyncio
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    nest_asyncio.apply(loop)
    try:
        from brokers.ibkr.gateway import IBGateway
        gw = IBGateway(client_id=98)
        ok = gw.connect(max_retries=1, retry_delay=0)
        if ok:
            gw.disconnect()
            return True, "ib_insync conectó OK (clientId=98)"
        return False, "ib_insync retornó False (gateway no listo)"
    except Exception as e:
        return False, f"Excepción ib_insync: {e}"

def _xvfb_status() -> str:
    try:
        r = subprocess.run(["systemctl", "is-active", "xvfb.service"],
                           capture_output=True, text=True)
        return r.stdout.strip()
    except Exception:
        return "unknown"

def _ibgateway_status() -> str:
    try:
        r = subprocess.run(["systemctl", "is-active", "ibgateway.service"],
                           capture_output=True, text=True)
        return r.stdout.strip()
    except Exception:
        return "unknown"

def main():
    _setup_log()
    log.info("=" * 60)
    log.info("TEST HEADLESS INICIADO")
    log.info(f"DISPLAY: {__import__('os').environ.get('DISPLAY', 'NOT SET')}")
    log.info(f"xvfb: {_xvfb_status()} | ibgateway: {_ibgateway_status()}")
    log.info("=" * 60)

    checks = 10
    interval = 60  # segundos entre checks

    for i in range(1, checks + 1):
        log.info(f"--- Check {i}/{checks} ---")
        log.info(f"xvfb: {_xvfb_status()} | ibgateway: {_ibgateway_status()}")

        port_ok = _is_port_open()
        log.info(f"Puerto 4002: {'ABIERTO' if port_ok else 'CERRADO'}")

        if port_ok:
            ok, msg = _test_ibkr_api()
            log.info(f"ib_insync: {'OK' if ok else 'FAIL'} — {msg}")
            if ok:
                log.info("RESULTADO FINAL: GATEWAY DISPONIBLE EN MODO HEADLESS ✓")
                return
        else:
            log.info("ib_insync: SKIP (puerto cerrado)")

        if i < checks:
            log.info(f"Esperando {interval}s...")
            time.sleep(interval)

    log.info("RESULTADO FINAL: GATEWAY NO DISPONIBLE tras 10 intentos ✗")
    log.info(f"Revisa: journalctl -u ibgateway.service -n 30")

if __name__ == "__main__":
    main()
