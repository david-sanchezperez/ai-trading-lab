import chromadb
from sentence_transformers import SentenceTransformer
from core.config import CHROMA_DIR

_client = None
_collection = None
_embedder = None

COLLECTION_NAME = "market_situations"
EMBEDDING_MODEL = "all-MiniLM-L6-v2"


def _get_collection():
    global _client, _collection, _embedder

    if _collection is None:
        _client = chromadb.PersistentClient(path=str(CHROMA_DIR))
        _collection = _client.get_or_create_collection(
            name=COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )
        _embedder = SentenceTransformer(EMBEDDING_MODEL)

    return _collection, _embedder


def _build_text(ticker, indicators, signal):
    """Texto para el embedding (solo setup — para similarity search)."""
    rsi          = indicators.get("rsi", 50.0)
    sma20        = indicators.get("sma20", 0.0)
    sma50        = indicators.get("sma50", 0.0)
    momentum     = indicators.get("momentum", 0.0)
    confidence   = indicators.get("confidence", 0.5)
    pct_52w      = indicators.get("pct_52w", 0.5)
    rs_spy       = indicators.get("rs_spy", 0.0)
    volume_ratio = indicators.get("volume_ratio", 1.0)
    sma_trend    = "bullish" if sma20 > sma50 else "bearish"

    return (
        f"ticker={ticker} rsi={rsi:.1f} signal={signal} "
        f"sma_trend={sma_trend} momentum={momentum:.2f} confidence={confidence:.2f} "
        f"pct_52w={pct_52w:.2f} rs_spy={rs_spy:+.3f} volume={volume_ratio:.1f}"
    )


def _build_display_doc(ticker, indicators, signal, outcome_5d=None, outcome_10d=None, outcome=None):
    """Documento visible al critic — setup + outcome cuando está disponible."""
    rsi      = indicators.get("rsi", 50.0)
    sma20    = indicators.get("sma20", 0.0)
    sma50    = indicators.get("sma50", 0.0)
    momentum = indicators.get("momentum", 0.0)
    vol      = indicators.get("volume_ratio", 1.0)
    rs_spy   = indicators.get("rs_spy", 0.0)
    trend    = "uptrend" if sma20 > sma50 else "downtrend"
    rsi_zone = "oversold" if rsi < 35 else ("overbought" if rsi > 65 else "neutral")

    base = (
        f"{ticker} {signal} | RSI={rsi:.1f} ({rsi_zone}) | {trend} | "
        f"momentum={momentum:+.2f} | vol={vol:.1f}x | rs_spy={rs_spy:+.3f}"
    )

    if outcome_5d is not None and outcome_10d is not None:
        label = "WIN" if (
            (signal == "BUY" and outcome_5d >= 2.0) or
            (signal == "SELL" and outcome_5d <= -2.0)
        ) else ("LOSS" if (
            (signal == "BUY" and outcome_5d <= -2.0) or
            (signal == "SELL" and outcome_5d >= 2.0)
        ) else "NEUTRAL")
        return f"{base} | → {outcome_5d:+.1f}% 5d, {outcome_10d:+.1f}% 10d [{label}]"

    if outcome and outcome != "unknown":
        return f"{base} | → {outcome}"

    return base


def store_situation(ticker, date, indicators, signal, outcome=None,
                    outcome_5d=None, outcome_10d=None, extra_metadata=None,
                    status="active", universe_entry_date=None):
    """
    Guarda una situación de mercado como embedding.
    - El embedding se genera del setup (indicadores) — para similarity search.
    - El documento almacenado incluye el outcome — visible al critic.
    outcome_5d / outcome_10d: retorno % a 5 y 10 días (float).
    outcome: etiqueta legacy "bullish"/"bearish"/"neutral".
    extra_metadata: dict adicional (thesis, risk, type, etc.)
    """
    collection, embedder = _get_collection()

    embed_text   = _build_text(ticker, indicators, signal)
    display_text = _build_display_doc(ticker, indicators, signal,
                                      outcome_5d=outcome_5d,
                                      outcome_10d=outcome_10d,
                                      outcome=outcome)
    embedding = embedder.encode(embed_text).tolist()

    doc_id = f"{ticker}_{str(date)[:10]}"
    metadata = {
        "ticker":     ticker,
        "date":       str(date),
        "signal":     signal,
        "rsi":        float(indicators.get("rsi", 0)),
        "sma_trend":  "bullish" if indicators.get("sma20", 0) > indicators.get("sma50", 0) else "bearish",
        "momentum":   float(indicators.get("momentum", 0)),
        "confidence": float(indicators.get("confidence", 0.5)),
        "outcome":    outcome or "unknown",
        "outcome_5d": float(outcome_5d) if outcome_5d is not None else 0.0,
        "outcome_10d": float(outcome_10d) if outcome_10d is not None else 0.0,
        "status":     status,
    }
    if universe_entry_date is not None:
        metadata["universe_entry_date"] = str(universe_entry_date)
    if extra_metadata:
        metadata.update(extra_metadata)

    collection.upsert(
        ids=[doc_id],
        embeddings=[embedding],
        documents=[display_text],
        metadatas=[metadata],
    )

    return doc_id


def get_similar_situations(ticker, indicators, signal, n=3, thesis=None):
    """
    Recupera las n situaciones más similares a la actual.
    Devuelve lista de dicts con la situación y su outcome.
    """
    collection, embedder = _get_collection()

    if collection.count() == 0:
        return []

    text = _build_text(ticker, indicators, signal)
    embedding = embedder.encode(text).tolist()

    try:
        results = collection.query(
            query_embeddings=[embedding],
            n_results=n,
            include=["documents", "metadatas", "distances"],
            where={"ticker": ticker},
        )
    except Exception:
        # Fallback: el ticker no tiene suficientes situaciones en el RAG.
        # Si conocemos la thesis, recuperamos precedentes del mismo tema.
        # Nunca hacemos query sin filtro — evita contaminar con tickers
        # de otro universo (SMCI, NVDA, PLTR, etc.).
        if thesis is None:
            return []
        try:
            results = collection.query(
                query_embeddings=[embedding],
                n_results=min(n, collection.count()),
                include=["documents", "metadatas", "distances"],
                where={"thesis": thesis},
            )
        except Exception:
            return []

    situations = []
    for doc, meta, dist in zip(
        results["documents"][0],
        results["metadatas"][0],
        results["distances"][0],
    ):
        situations.append({
            "text":       doc,
            "metadata":   meta,
            "similarity": round(1 - dist, 4),
        })

    return situations


# ---------------------------------------------------------------------------
# Company context — descripción fundamental por ticker
# ---------------------------------------------------------------------------

COMPANY_COLLECTION = "company_context"
_company_collection = None


def _get_company_collection():
    global _company_collection
    if _company_collection is None:
        client = chromadb.PersistentClient(path=str(CHROMA_DIR))
        _company_collection = client.get_or_create_collection(
            name=COMPANY_COLLECTION,
            metadata={"hnsw:space": "cosine"},
        )
    return _company_collection


def store_company_context(ticker: str, text: str, metadata: dict | None = None) -> None:
    """Guarda o actualiza el contexto fundamental de una empresa."""
    _, embedder = _get_collection()  # inicializa _embedder como efecto secundario
    collection  = _get_company_collection()
    assert embedder is not None
    embedding   = embedder.encode(text).tolist()
    collection.upsert(
        ids=[ticker],
        embeddings=[embedding],
        documents=[text],
        metadatas=[{**(metadata or {}), "ticker": ticker}],
    )


def get_company_context(ticker: str) -> str | None:
    """Devuelve el contexto fundamental del ticker, o None si no existe."""
    collection = _get_company_collection()
    if collection.count() == 0:
        return None
    try:
        result = collection.get(ids=[ticker], include=["documents"])
        docs = result.get("documents", [])
        return docs[0] if docs else None
    except Exception:
        return None
