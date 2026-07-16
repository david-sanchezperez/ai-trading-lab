"""
Priority LLM Queue — serializa peticiones a Qwen3.6 con prioridad.

Dos colas internas:
  urgent — acciones defensivas sobre posiciones abiertas (siempre primero)
  normal  — noticias, transcripts, entradas oportunistas

Un único worker thread procesa las tareas de una en una.
Backward-compatible: call_llm() funciona igual que antes.

Timeouts:
  urgent: si espera > 2 min → alerta Telegram
  normal: si espera > 10 min → skip, usa fallback FinBERT
"""

import logging
import queue
import threading
import time
from datetime import datetime
from typing import Callable, Optional

import requests

from core.config import CRITIC_LLM_URL
from config.monitor_config import MONITOR_CONFIG

log = logging.getLogger(__name__)

_URGENT_WARN_SECS  = 120    # 2 min → alerta Telegram
_NORMAL_SKIP_SECS  = 600    # 10 min → skip

_cfg = MONITOR_CONFIG["llm"]


# ── PriorityLLMQueue (singleton) ───────────────────────────────────────────────

class PriorityLLMQueue:
    """
    Cola de prioridad para peticiones LLM.
    Un único worker — Qwen3.6 no admite concurrencia real.
    """

    def __init__(self):
        self.urgent: queue.PriorityQueue = queue.PriorityQueue()
        self.normal: queue.PriorityQueue = queue.PriorityQueue()
        self._running = False
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread  = threading.Thread(target=self._worker, daemon=True, name="llm-queue")
        self._thread.start()
        log.info("[llm_queue] Worker iniciado")

    def stop(self) -> None:
        self._running = False

    def submit_urgent(
        self,
        prompt:   str,
        callback: Callable[[Optional[str], dict], None],
        ticker:   str = "",
        context:  dict | None = None,
    ) -> None:
        """Acciones defensivas — procesadas antes que todo lo normal."""
        self.urgent.put((0, datetime.now(), prompt, callback, context or {}))

    def submit_normal(
        self,
        prompt:   str,
        callback: Callable[[Optional[str], dict], None],
        ticker:   str = "",
        context:  dict | None = None,
    ) -> None:
        """Noticias, transcripts, oportunistas."""
        self.normal.put((1, datetime.now(), prompt, callback, context or {}))

    def _worker(self) -> None:
        while self._running:
            task = None
            try:
                if not self.urgent.empty():
                    task = self.urgent.get_nowait()
                elif not self.normal.empty():
                    task = self.normal.get_nowait()
                else:
                    time.sleep(0.5)
                    continue

                priority, ts, prompt, callback, context = task
                wait_secs = (datetime.now() - ts).total_seconds()

                if priority == 0 and wait_secs > _URGENT_WARN_SECS:
                    log.warning(f"[llm_queue] URGENT task delayed {wait_secs:.0f}s")
                    try:
                        from scheduler.notifier import send_notification
                        send_notification(f"⚠️ URGENT LLM task delayed {wait_secs:.0f}s")
                    except Exception:
                        pass

                if priority == 1 and wait_secs > _NORMAL_SKIP_SECS:
                    log.warning(f"[llm_queue] NORMAL task expired after {wait_secs:.0f}s — skipping")
                    callback(None, context)
                    continue

                result = _call_llm_direct(prompt)
                callback(result, context)

            except Exception as e:
                log.error(f"[llm_queue] Error en worker: {e}")
                if task:
                    try:
                        _, _, _, callback, context = task
                        callback(None, context)
                    except Exception:
                        pass


_queue_instance: Optional[PriorityLLMQueue] = None
_queue_lock = threading.Lock()


def get_llm_queue() -> PriorityLLMQueue:
    global _queue_instance
    with _queue_lock:
        if _queue_instance is None:
            _queue_instance = PriorityLLMQueue()
            _queue_instance.start()
    return _queue_instance


# ── Backward-compatible synchronous API ───────────────────────────────────────

def call_llm(
    prompt:      str,
    max_tokens:  int | None   = None,
    temperature: float | None = None,
    urgent:      bool         = False,
) -> str | None:
    """
    Llamada síncrona a Qwen3.6 via la cola de prioridad.
    Bloquea hasta obtener resultado o timeout (5 min urgent / 10 min normal).
    Backward-compatible con el código existente en monitor/.
    """
    event   = threading.Event()
    result  = [None]

    def _cb(res: Optional[str], ctx: dict) -> None:
        result[0] = res
        event.set()

    q       = get_llm_queue()
    timeout = _URGENT_WARN_SECS * 2.5 if urgent else _NORMAL_SKIP_SECS + 60

    if urgent:
        q.submit_urgent(prompt, _cb)
    else:
        q.submit_normal(prompt, _cb)

    event.wait(timeout=timeout)
    return result[0]


# ── Direct LLM call (used by worker) ─────────────────────────────────────────

def _call_llm_direct(
    prompt:      str,
    max_tokens:  int   = None,
    temperature: float = None,
) -> str | None:
    max_tok = max_tokens  if max_tokens  is not None else _cfg["max_tokens"]
    temp    = temperature if temperature is not None else _cfg["temperature"]
    try:
        resp = requests.post(
            CRITIC_LLM_URL,
            json={
                "model":       _cfg["model"],
                "messages":    [{"role": "user", "content": prompt}],
                "stream":      False,
                "temperature": temp,
                "max_tokens":  max_tok,
            },
            timeout=120,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"].get("content", "")
    except Exception as e:
        log.warning(f"[llm_queue] Error LLM directo: {e}")
        return None
