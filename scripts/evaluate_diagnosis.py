"""
Evaluate Diagnosis Agent
========================

Evaluates the DiagnosisAgent on labeled development and held-out datasets.
Reports:
  - Accuracy
  - Per-class Precision / Recall / F1
  - Unknown rate
  - Low-confidence rate (< 0.60)
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from backend.agents.diagnosis import DiagnosisAgent
from backend.agents.evaluation_dataset import (
    DEV_DATASET,
    HELDOUT_EVALUATION_DATASET,
    LabeledErrorCase,
)
from backend.models.schemas import FailureClass


def evaluate_dataset(
    agent: DiagnosisAgent,
    cases: list[LabeledErrorCase],
    dataset_name: str = "Evaluation",
) -> dict[str, Any]:
    total = len(cases)
    correct = 0
    unknown_count = 0
    low_confidence_count = 0

    class_tp: dict[FailureClass, int] = defaultdict(int)
    class_fp: dict[FailureClass, int] = defaultdict(int)
    class_fn: dict[FailureClass, int] = defaultdict(int)
    class_total: dict[FailureClass, int] = defaultdict(int)

    for case in cases:
        result = agent.diagnose_raw(case.raw_error)
        predicted = result.normalized_failure_class
        actual = case.expected_class
        class_total[actual] += 1

        if result.confidence < 0.60:
            low_confidence_count += 1

        if predicted == FailureClass.UNKNOWN:
            unknown_count += 1

        if predicted == actual:
            correct += 1
            class_tp[actual] += 1
        else:
            class_fp[predicted] += 1
            class_fn[actual] += 1

    accuracy = correct / total if total else 0.0
    unknown_rate = unknown_count / total if total else 0.0
    low_conf_rate = low_confidence_count / total if total else 0.0

    # Per-class metrics
    class_metrics: dict[str, dict[str, float]] = {}
    for fc in FailureClass:
        tp = class_tp[fc]
        fp = class_fp[fc]
        fn = class_fn[fc]
        prec = tp / (tp + fp) if (tp + fp) > 0 else (1.0 if class_total[fc] == 0 else 0.0)
        rec = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1 = (2 * prec * rec) / (prec + rec) if (prec + rec) > 0 else 0.0
        if class_total[fc] > 0 or fp > 0:
            class_metrics[fc.value] = {
                "support": class_total[fc],
                "precision": round(prec, 4),
                "recall": round(rec, 4),
                "f1": round(f1, 4),
            }

    return {
        "dataset_name": dataset_name,
        "total_cases": total,
        "correct": correct,
        "accuracy": round(accuracy, 4),
        "unknown_rate": round(unknown_rate, 4),
        "low_confidence_rate": round(low_conf_rate, 4),
        "class_metrics": class_metrics,
    }


def format_evaluation_report(dev_report: dict[str, Any], heldout_report: dict[str, Any]) -> str:
    lines = [
        "╔═══════════════════════════════════════════════════════════════════════════╗",
        "║                     Diagnosis Agent Evaluation Report                     ║",
        "╠═══════════════════════════════════════════════════════════════════════════╣",
        f"║  Development Set Accuracy : {dev_report['accuracy']*100:>6.2f}% ({dev_report['correct']}/{dev_report['total_cases']})                                    ║",
        f"║  Development Unknown Rate : {dev_report['unknown_rate']*100:>6.2f}%                                            ║",
        f"║  Development Low-Conf Rate: {dev_report['low_confidence_rate']*100:>6.2f}%                                            ║",
        "╟───────────────────────────────────────────────────────────────────────────╢",
        f"║  Held-Out Set Accuracy    : {heldout_report['accuracy']*100:>6.2f}% ({heldout_report['correct']}/{heldout_report['total_cases']})                                    ║",
        f"║  Held-Out Unknown Rate    : {heldout_report['unknown_rate']*100:>6.2f}%                                            ║",
        f"║  Held-Out Low-Conf Rate   : {heldout_report['low_confidence_rate']*100:>6.2f}%                                            ║",
        "╠═══════════════════════════════════════════════════════════════════════════╣",
        "║  Held-Out Per-Class Breakdown:                                            ║",
        "║  Class                     │ Support │ Precision │  Recall  │    F1     ║",
        "║────────────────────────────┼─────────┼───────────┼──────────┼───────────║",
    ]

    for cls_name, m in heldout_report["class_metrics"].items():
        lines.append(
            f"║  {cls_name:<26}│   {m['support']:<6}│   {m['precision']*100:>5.1f}%  │   {m['recall']*100:>5.1f}% │   {m['f1']:>5.2f}   ║"
        )

    lines.append("╚═══════════════════════════════════════════════════════════════════════════╝")
    return "\n".join(lines)


def main() -> int:
    agent = DiagnosisAgent()
    dev_res = evaluate_dataset(agent, DEV_DATASET, "Development Set")
    heldout_res = evaluate_dataset(agent, HELDOUT_EVALUATION_DATASET, "Held-Out Evaluation Set")
    print("\n" + format_evaluation_report(dev_res, heldout_res) + "\n")
    return 0


if __name__ == "__main__":
    main()
