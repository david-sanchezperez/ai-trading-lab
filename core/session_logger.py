"""
Session logger — logging estructurado JSONL por sesión diaria.

Cada nodo del pipeline escribe una línea JSON con timestamp ISO8601,
duración en ms y campos específicos del nodo. Ficheros en:
  logs/YYYY-MM-DD/session_{session_id}.jsonl

Uso:
    from core.session_logger import set_session_logger, get_session_logger, SessionLogger
    set_session_logger(SessionLogger("20260607_2030"))

    logger = get_session_logger()
    if logger:
        t0 = time.monotonic()
        ...
        logger.log_node("technical_node", ticker, data, (time.monotonic()-t0)*1000)
"""

import json
import logging
import threading
from datetime import datetime, timezone

from core.config import LOGS_DIR

log = logging.getLogger(__name__)

_lock = threading.Lock()
_current_session: "SessionLogger | None" = None


class SessionLogger:
    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        date_str = datetime.now().strftime("%Y-%m-%d")
        session_dir = LOGS_DIR / date_str
        session_dir.mkdir(parents=True, exist_ok=True)
        self._path = session_dir / f"session_{session_id}.jsonl"
        self._file_lock = threading.Lock()
        log.info(f"[session_logger] Sesión iniciada → {self._path}")

    def _ts(self) -> str:
        return (
            datetime.now(timezone.utc)
            .strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        )

    def _write(self, record: dict) -> None:
        try:
            with self._file_lock:
                with open(self._path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        except Exception as exc:
            log.warning(f"[session_logger] Error escribiendo log: {exc}")

    def log_node(
        self,
        node: str,
        ticker: str,
        data: dict,
        duration_ms: float,
        status: str = "ok",
    ) -> None:
        """
        Escribe una línea JSONL con el resultado de un nodo.
        status: "ok" | "error" | "fast_path"
        """
        self._write({
            "ts":          self._ts(),
            "node":        node,
            "ticker":      ticker,
            "duration_ms": round(duration_ms, 1),
            "status":      status,
            "data":        data,
        })

    def log_critic(
        self,
        ticker: str,
        prompt_excerpt: str,
        response: str,
        rag_docs: list,
        fast_path: bool,
        verdict: str,
        duration_ms: float,
        error: bool = False,
    ) -> None:
        """
        Versión extendida para critic_node.
        Guarda IDs de los docs RAG recuperados, tamaño de prompt/respuesta,
        y los primeros 300 chars del prompt para trazabilidad.
        No guarda el prompt completo ni el thinking del LLM.
        """
        rag_doc_ids = []
        for doc in rag_docs:
            if not isinstance(doc, dict):
                continue
            m = doc.get("metadata", {})
            doc_date   = str(m.get("date", ""))[:10]
            doc_ticker = m.get("ticker", ticker)
            rag_doc_ids.append(f"{doc_date}_{doc_ticker}")

        status = "fast_path" if fast_path else ("error" if error else "ok")
        self.log_node(
            node="critic_node",
            ticker=ticker,
            data={
                "verdict":        verdict,
                "fast_path":      fast_path,
                "error":          error,
                "prompt_chars":   len(prompt_excerpt),
                "prompt_excerpt": prompt_excerpt[:300],
                "response_chars": len(response),
                "rag_docs_count": len(rag_docs),
                "rag_doc_ids":    rag_doc_ids,
            },
            duration_ms=duration_ms,
            status=status,
        )


def get_session_logger() -> "SessionLogger | None":
    """Devuelve el logger de la sesión activa, o None si no hay sesión."""
    return _current_session


def set_session_logger(logger: "SessionLogger | None") -> None:
    """Establece el logger de sesión. Llamar con None al terminar el ciclo."""
    global _current_session
    with _lock:
        _current_session = logger
