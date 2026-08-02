import csv
import statistics

from core.config import PROJECT_ROOT

FILE_PATH = PROJECT_ROOT / "data" / "processed" / "sentiment.csv"


def save_sentiment(date, ticker, sentiment, confidence):
    FILE_PATH.parent.mkdir(parents=True, exist_ok=True)

    file_exists = FILE_PATH.exists()

    with open(FILE_PATH, mode="a", newline="") as f:
        writer = csv.writer(f)

        if not file_exists:
            writer.writerow(["date", "ticker", "sentiment", "confidence"])

        writer.writerow([date, ticker, sentiment, confidence])


def get_recent_sentiment(ticker, n=5):
    if not FILE_PATH.exists():
        return 0.0

    sentiments = []

    with open(FILE_PATH, mode="r") as f:
        reader = csv.DictReader(f)

        for row in reader:
            if row["ticker"] == ticker:
                try:
                    sentiments.append(float(row["sentiment"]))
                except ValueError:
                    continue

    if len(sentiments) == 0:
        return 0.0

    recent = sentiments[-n:]

    return statistics.mean(recent)
