"""Dependency-free evaluator for an exported Outdoor Comfort model ("hgb-portable-v1").

Training fits scikit-learn's HistGradientBoostingClassifier (optionally with sigmoid
calibration) and exports its trees and calibrators to plain JSON. This module
reproduces ``predict_proba`` with the standard library only, so the deployed API
needs no numpy/scipy/scikit-learn (they would push the Vercel bundle from about 52 MB
to about 327 MB) and never unpickles a file. Training refuses to write an artifact
unless this evaluator matches scikit-learn's probabilities on the whole test fold.

Mirrors scikit-learn 1.6: ``_predictor.pyx`` (tree traversal), ``HalfBinomialLoss`` /
``HalfMultinomialLoss.predict_proba`` (links), and ``_CalibratedClassifier.predict_proba``.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

PORTABLE_FORMAT = "hgb-portable-v1"
LEAF = -1


def _expit(value: float) -> float:
    if value >= 0:
        return 1.0 / (1.0 + math.exp(-value))
    exp = math.exp(value)
    return exp / (1.0 + exp)


def _tree_value(tree: dict, row: Sequence[float]) -> float:
    """Walk one tree. Columnar arrays: f=feature, t=threshold, m=missing-goes-left, l/r=children, v=leaf value."""
    feature, threshold, missing_left, left, right = tree["f"], tree["t"], tree["m"], tree["l"], tree["r"]
    node = 0
    while left[node] != LEAF:
        value = row[feature[node]]
        if value != value:  # NaN: route by the learned missing-value direction
            node = left[node] if missing_left[node] else right[node]
        elif value <= threshold[node]:
            node = left[node]
        else:
            node = right[node]
    return tree["v"][node]


def raw_scores(ensemble: dict, row: Sequence[float]) -> list[float]:
    """Baseline plus the sum of every iteration's trees, one score per tree-per-iteration."""
    scores = list(ensemble["baseline"])
    for iteration in ensemble["trees"]:
        for k, tree in enumerate(iteration):
            scores[k] += _tree_value(tree, row)
    return scores


def _link(scores: list[float]) -> list[float]:
    if len(scores) == 1:  # binary: one score for classes[1]
        positive = _expit(scores[0])
        return [1.0 - positive, positive]
    top = max(scores)
    exps = [math.exp(score - top) for score in scores]
    total = sum(exps)
    return [exp / total for exp in exps]


def _calibrated(member: dict, scores: list[float], n_classes: int) -> list[float]:
    proba = [0.0] * n_classes
    for score, class_index, a, b in zip(scores, member["class_indices"], member["a"], member["b"]):
        proba[class_index + (1 if n_classes == 2 else 0)] = _expit(-(a * score + b))
    if n_classes == 2:
        proba[0] = 1.0 - proba[1]
    else:
        total = sum(proba)
        proba = [p / total for p in proba] if total != 0 else [1.0 / n_classes] * n_classes
    return [1.0 if 1.0 < p <= 1.0 + 1e-5 else p for p in proba]


def predict_proba(model: dict, row: Sequence[float]) -> list[float]:
    """Class probabilities in ``model["classes"]`` order for one feature row (NaN = missing)."""
    if model.get("format") != PORTABLE_FORMAT:
        raise ValueError(f"Unsupported model format: {model.get('format')}")
    n_classes = len(model["classes"])
    calibration = model.get("calibration")
    if not calibration:
        return _link(raw_scores(model["ensembles"][0], row))
    totals = [0.0] * n_classes
    cache: dict[int, list[float]] = {}
    for member in calibration:
        index = member["ensemble"]
        if index not in cache:
            cache[index] = raw_scores(model["ensembles"][index], row)
        for k, p in enumerate(_calibrated(member, cache[index], n_classes)):
            totals[k] += p
    return [total / len(calibration) for total in totals]
