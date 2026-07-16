"""
Notificaciones Telegram para el scheduler.
Envía mensajes directamente a la API HTTP de Telegram,
independientemente del bot principal (app/telegram_bot.py).
"""

import os
import httpx
from core.config import PROJECT_ROOT


TELEGRAM_TOKEN   = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"


def send_notification(text: str) -> bool:
    """
    Envía un mensaje al chat configurado.
    Devuelve True si el envío fue exitoso, False si hubo error.
    """
    try:
        response = httpx.post(
            TELEGRAM_API_URL,
            json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": text,
                "parse_mode": "Markdown",
            },
            timeout=10,
        )
        response.raise_for_status()
        return True
    except Exception as e:
        print(f"[notifier] Error enviando notificación: {e}")
        return False
