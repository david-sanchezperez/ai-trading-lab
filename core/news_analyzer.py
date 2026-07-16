"""
Extrae el evento más relevante de los titulares de un ticker usando Qwen3.6.

Complementa a FinBERT — no lo reemplaza:
  FinBERT  → clasifica cada headline rápido (~20ms/headline)
  Qwen3.6  → entiende contexto, extrae evento material si lo hay (~1-2s)

Solo se llama cuando hay titulares suficientes Y el ticker pasa el fast path
del critic (para no desperdiciar llamadas en señales débiles).
"""

import requests

from core.config import CRITIC_LLM_URL, CRITIC_LLM_MODEL


def extract_key_event(ticker: str, raw_results: list) -> str | None:
    """
    Dado el output de FinBERT (lista de dicts con 'title'), usa Qwen3.6
    para extraer el evento más relevante en una frase.

    Devuelve None si no hay evento material o si la llamada falla.
    """
    headlines = [r["title"] for r in raw_results if r.get("title")]
    if len(headlines) < 2:
        return None

    text = "\n".join(f"- {h}" for h in headlines[:10])
    prompt = (
        f"/no_think\n"
        f"For {ticker}, identify the single most market-moving event from these "
        f"headlines in ONE sentence. Focus on: earnings results/guidance, M&A, "
        f"regulatory decisions, major product launches, analyst upgrades/downgrades. "
        f"If nothing material, respond exactly: NONE\n\n"
        f"Headlines:\n{text}"
    )

    try:
        resp = requests.post(
            CRITIC_LLM_URL,
            json={
                "model":       CRITIC_LLM_MODEL,
                "messages":    [{"role": "user", "content": prompt}],
                "stream":      False,
                "temperature": 0.1,
                "max_tokens":  80,
            },
            timeout=25,
        )
        result = resp.json()["choices"][0]["message"]["content"].strip()
        return None if result.upper().startswith("NONE") else result
    except Exception:
        return None
