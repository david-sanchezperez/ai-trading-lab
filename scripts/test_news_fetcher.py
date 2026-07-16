from core.news_fetcher import get_ticker_sentiment

for ticker in ["NVDA", "VST", "RXRX"]:
    result = get_ticker_sentiment(ticker)
    print(f"\n{'='*40}")
    print(f"{ticker} — {result['headlines']} headlines")
    print(f"Sentiment: {result['sentiment']:+.4f}")
    print(f"Confidence: {result['confidence']:.4f}")
    for r in result['raw_results'][:3]:
        print(f"  {r['label']:10} {r['score']:+.2f}  {r['title'][:60]}")
