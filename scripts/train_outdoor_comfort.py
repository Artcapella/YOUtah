#!/usr/bin/env python3
"""Train a leakage-aware Outdoor Comfort nowcast from real station history.

Pass quality-assured history first; when two inputs hold the same station-hour the
earlier file wins. The artifact (portable JSON, served without ML libraries) is
written only if the model beats the persistence baseline on the final chronological
test period and the export reproduces scikit-learn's probabilities.

    python scripts/train_outdoor_comfort.py \\
      --input data/utah_air_history.csv --input data/daq_snapshots.csv --horizon-hours 3
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.intelligence.model import write_artifact  # noqa: E402
from backend.intelligence.training import (  # noqa: E402
    TrainingRefused,
    load_history,
    make_nowcast_training_frame,
    train_outdoor_comfort,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--input", type=Path, action="append", required=True, help="history CSV; repeatable")
    parser.add_argument("--out", type=Path, default=ROOT / "backend" / "intelligence" / "artifacts" / "outdoor_comfort.json")
    parser.add_argument("--horizon-hours", type=int, default=3, choices=range(1, 25))
    args = parser.parse_args()

    frame = make_nowcast_training_frame(load_history(args.input), args.horizon_hours)
    print(f"{len(frame):,} usable rows from {frame['station_id'].nunique()} stations, "
          f"{frame['observed_at'].min():%Y-%m-%d} to {frame['observed_at'].max():%Y-%m-%d}")
    print("target classes:", frame["target_class"].value_counts().to_dict())
    try:
        artifact = train_outdoor_comfort(frame, args.horizon_hours)
    except TrainingRefused as refusal:
        raise SystemExit(f"Refused to train: {refusal}") from None

    write_artifact(args.out, artifact)
    print(f"Saved validated model {artifact['model_version']} to {args.out} ({args.out.stat().st_size / 1024:,.0f} KB)")
    print(json.dumps(artifact["metrics"], indent=2))


if __name__ == "__main__":
    main()
