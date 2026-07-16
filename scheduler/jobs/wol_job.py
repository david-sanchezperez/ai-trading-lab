import subprocess
import time
import logging
from wakeonlan import send_magic_packet

PC_MAC = "2c:f0:5d:ce:07:52"
PC_IP = "192.168.1.32"
PING_TIMEOUT_SECONDS = 120
PING_INTERVAL_SECONDS = 10

logger = logging.getLogger(__name__)


def is_pc_up() -> bool:
    result = subprocess.run(
        ["ping", "-c", "1", "-W", "2", PC_IP],
        capture_output=True
    )
    return result.returncode == 0


def wake_pc() -> bool:
    """
    Comprueba si el PC ya está encendido.
    Si no, envía magic packet WoL y espera respuesta.
    Devuelve True si el PC responde, False si timeout.
    """
    if is_pc_up():
        logger.info(f"PC {PC_IP} ya estaba encendido")
        return True

    logger.info(f"Enviando magic packet WoL a {PC_MAC}")
    send_magic_packet(PC_MAC)

    start = time.time()
    while time.time() - start < PING_TIMEOUT_SECONDS:
        time.sleep(PING_INTERVAL_SECONDS)
        if is_pc_up():
            elapsed = int(time.time() - start)
            logger.info(f"PC {PC_IP} respondió en {elapsed}s")
            return True

    logger.error(
        f"PC {PC_IP} no respondió tras {PING_TIMEOUT_SECONDS}s"
    )
    return False
