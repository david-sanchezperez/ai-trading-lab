import feedparser
from transformers import pipeline

# Carga única al importar — no en cada llamada
_finbert = pipeline(
    "sentiment-analysis",
    model="ProsusAI/finbert",
    tokenizer="ProsusAI/finbert",
)


def get_news_headlines(ticker, max_items=10):
    """
    Descarga titulares desde Yahoo Finance RSS.
    Devuelve lista de dicts: title, published, link.
    """
    url = f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={ticker}&region=US&lang=en-US"
    feed = feedparser.parse(url)

    headlines = []
    for entry in feed.entries[:max_items]:
        headlines.append({
            "title": entry.get("title", ""),
            "published": entry.get("published", ""),
            "link": entry.get("link", ""),
        })

    return headlines


def get_ticker_sentiment(ticker, max_items=10):
    """
    Descarga titulares de ticker y los pasa por FinBERT.
    Devuelve media ponderada por confidence.
    """
    headlines = get_news_headlines(ticker, max_items=max_items)

    if not headlines:
        return {
            "sentiment": 0.0,
            "confidence": 0.0,
            "headlines": 0,
            "raw_results": [],
        }

    raw_results = []
    weighted_sum = 0.0
    weight_total = 0.0

    for item in headlines:
        title = item["title"]
        if not title:
            continue

        result = _finbert(title)[0]
        label = result["label"]
        score = result["score"]

        if label == "positive":
            numeric = score
        elif label == "negative":
            numeric = -score
        else:
            numeric = 0.0

        weighted_sum += numeric * score
        weight_total += score

        raw_results.append({
            "title": title,
            "label": label,
            "score": round(numeric, 4),
            "confidence": round(score, 4),
        })

    sentiment = weighted_sum / weight_total if weight_total > 0 else 0.0

    return {
        "sentiment": round(sentiment, 4),
        "confidence": round(weight_total / len(raw_results), 4) if raw_results else 0.0,
        "headlines": len(raw_results),
        "raw_results": raw_results,
    }
