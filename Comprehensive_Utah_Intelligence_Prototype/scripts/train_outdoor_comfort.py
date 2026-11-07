#!/usr/bin/env python3
"""Train a leakage-aware outdoor-comfort nowcast from real station history."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

import joblib
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import accuracy_score, f1_score, log_loss

from backend.intelligence.features import MODEL_COLUMNS, make_nowcast_training_frame
from backend.intelligence.model import write_metrics


def chronological_split(frame: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    frame = frame.sort_values("observed_at").reset_index(drop=True)
    train_end = int(len(frame) * 0.6)
    valid_end = int(len(frame) * 0.8)
    return frame.iloc[:train_end], frame.iloc[train_end:valid_end], frame.iloc[valid_end:]


def evaluate(y_true: pd.Series, probabilities, classes, persistence: pd.Series) -> dict:
    prediction = [classes[index] for index in probabilities.argmax(axis=1)]
    return {
        "macro_f1": round(float(f1_score(y_true, prediction, average="macro", zero_division=0)), 4),
        "accuracy": round(float(accuracy_score(y_true, prediction)), 4),
        "log_loss": round(float(log_loss(y_true, probabilities, labels=classes)), 4),
        "persistence_accuracy": round(float(accuracy_score(y_true, persistence)), 4),
        "rows": int(len(y_true)),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--horizon-hours", type=int, default=3, choices=range(1, 25))
    args = parser.parse_args()

    raw = pd.read_csv(args.input)
    frame = make_nowcast_training_frame(raw, args.horizon_hours)
    if len(frame) < 200:
        raise SystemExit("Need at least 200 usable historical rows; refusing to train a demo-quality model.")
    if frame["target_class"].nunique() < 2:
        raise SystemExit("Need at least two target classes; choose a longer/more varied historical period.")

    train, valid, test = chronological_split(frame)
    if min(len(train), len(valid), len(test)) < 25:
        raise SystemExit("Chronological folds are too small; provide more history.")
    model = HistGradientBoostingClassifier(
        learning_rate=0.06,
        max_leaf_nodes=20,
        l2_regularization=1.0,
        random_state=42,
    )
    model.fit(train[MODEL_COLUMNS], train["target_class"])
    test_probabilities = model.predict_proba(test[MODEL_COLUMNS])
    metrics = evaluate(
        test["target_class"],
        test_probabilities,
        model.classes_,
        test["pm25_24hr_avg"].map(lambda value: "good" if value <= 12 else "caution" if value <= 35.4 else "poor"),
    )
    metrics["validation_rows"] = int(len(valid))
    metrics["passes_persistence_baseline"] = metrics["accuracy"] > metrics["persistence_accuracy"]
    if not metrics["passes_persistence_baseline"]:
        raise SystemExit(f"Model did not beat persistence baseline: {metrics}")

    artifact = {
        "model": model,
        "horizon_hours": args.horizon_hours,
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "feature_columns": MODEL_COLUMNS,
        "metrics": metrics,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(artifact, args.out)
    write_metrics(args.out, artifact)
    print(f"Saved validated model to {args.out}")
    print(metrics)


if __name__ == "__main__":
    main()
