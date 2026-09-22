"""Hospital 1: measuring the audit.  The **only** module that reads labels.

Nothing in the prediction path -- ``shared/`` and ``hospital_1/{contract,
matcher,audit}.py`` -- imports this module, and this module imports the
prediction path only to run it.  That one-way dependency is what makes "the
predictor never reads labels" a property of the code rather than a promise;
``tests/hospital_1/test_evaluation.py`` checks it mechanically.

Contents
    1. Locations          labels, the locked split, the freeze record
    2. The locked split   created once and retained as a fixed development/holdout
                          partition; never re-drawn
    3. Metrics            detection, category attribution, money -- separately
    4. Freeze             source hashes; the holdout is scored only on frozen code
    5. Reports            the development + holdout evaluation
    6. Research           temporal (prospective) evaluation and ablations

Sections 2 and 6 are development evidence rather than runtime machinery.  They
are kept so that the figures in ``outputs/hospital_1/generalization_report.md``
can be re-checked.  The complete H1 labels were visible during development, so
the holdout is a post-hoc measurement, not independent validation.
"""

from __future__ import annotations

import csv
import datetime as _dt
import hashlib
import json
import random
import tempfile
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

from ..shared.models import AuditResult, InvoiceOccurrence
from ..shared.submission import write_predictions
from . import matcher as matcher_mod
from .audit import REPO_ROOT, AuditContext, Pipeline, PricingOptions
from .matcher import ABBREVIATIONS, ServiceMatcher


# ==========================================================================
# 1. Locations
# ==========================================================================

LABELS_CSV = REPO_ROOT / "labels" / "hospital_1_labels.csv"
ARTIFACTS = REPO_ROOT / "artifacts" / "hospital_1"
SPLIT_DIR = ARTIFACTS / "split"
SPLIT_MANIFEST = ARTIFACTS / "split_manifest.json"
DEV_LABELS = SPLIT_DIR / "dev_labels.csv"
HOLDOUT_LABELS = SPLIT_DIR / "holdout_labels.csv"
FREEZE_FILE = ARTIFACTS / "freeze.json"
RESEARCH_DIR = ARTIFACTS / "research"

#: The prediction path.  ``freeze`` hashes exactly these files, and the holdout
#: is only scored while they are unchanged.  This module and ``main.py`` are
#: deliberately absent: report wording can be fixed without invalidating the
#: frozen holdout result, but prediction logic cannot.
FROZEN_SOURCES = (
    "src/shared/models.py",
    "src/shared/data.py",
    "src/shared/money.py",
    "src/shared/submission.py",
    "src/hospital_1/contract.py",
    "src/hospital_1/matcher.py",
    "src/hospital_1/audit.py",
)


def _rel(path: Path) -> str:
    return str(path.relative_to(REPO_ROOT)).replace("\\", "/")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ==========================================================================
# 2. The locked split
# ==========================================================================

SPLIT_SEED = 20240517
DEV_FRACTION = 0.70


def create_split(
    *,
    labels_csv: Path = LABELS_CSV,
    split_dir: Path = SPLIT_DIR,
    manifest_path: Path = SPLIT_MANIFEST,
    force: bool = False,
) -> dict:
    """Divide the Hospital 1 labels into development (70%) and holdout (30%).

    Deterministic: invoice ids are sorted, then shuffled with a fixed seed, so
    re-running reproduces the identical split.  Prints nothing about label
    contents -- only counts -- so running it cannot leak the holdout.

    Refuses to overwrite an existing split unless ``force``: the manifest's
    creation time records when the partition was fixed, and silently
    re-stamping it would destroy that record.
    """
    if manifest_path.exists() and not force:
        raise FileExistsError(
            f"{manifest_path} already exists; the split is locked. Pass force=True "
            "only to reproduce it somewhere else."
        )

    with labels_csv.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)

    # One label row per invoice id is expected; group defensively so that a
    # duplicated id can never straddle the split boundary.
    by_id: dict[str, list[dict]] = {}
    for row in rows:
        by_id.setdefault(row["invoice_id"], []).append(row)

    invoice_ids = sorted(by_id)
    shuffled = list(invoice_ids)
    random.Random(SPLIT_SEED).shuffle(shuffled)
    n_dev = int(round(len(shuffled) * DEV_FRACTION))
    dev_ids = sorted(shuffled[:n_dev])
    holdout_ids = sorted(shuffled[n_dev:])
    assert not (set(dev_ids) & set(holdout_ids))
    assert len(dev_ids) + len(holdout_ids) == len(invoice_ids)

    split_dir.mkdir(parents=True, exist_ok=True)
    dev_path = split_dir / "dev_labels.csv"
    holdout_path = split_dir / "holdout_labels.csv"
    for path, ids in ((dev_path, dev_ids), (holdout_path, holdout_ids)):
        with path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames)
            writer.writeheader()
            for invoice_id in ids:
                writer.writerows(by_id[invoice_id])

    def rel(p: Path) -> str:
        try:
            return _rel(p)
        except ValueError:
            return str(p)

    manifest = {
        "hospital": "hospital_1",
        "created_utc": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "source_labels": rel(labels_csv),
        "source_labels_sha256": _sha256(labels_csv),
        "seed": SPLIT_SEED,
        "dev_fraction": DEV_FRACTION,
        "method": "sorted unique invoice_ids -> random.Random(seed).shuffle -> first 70% dev",
        "n_invoice_ids": len(invoice_ids),
        "n_dev": len(dev_ids),
        "n_holdout": len(holdout_ids),
        "dev_file": rel(dev_path),
        "holdout_file": rel(holdout_path),
        "dev_file_sha256": _sha256(dev_path),
        "holdout_file_sha256": _sha256(holdout_path),
        # Which invoices are held out is not a leak; their labels would be.
        "dev_invoice_ids": dev_ids,
        "holdout_invoice_ids": holdout_ids,
        "policy": (
            "development code may read dev_file only; holdout_file is evaluated "
            "once, after the implementation is frozen"
        ),
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


# ==========================================================================
# 3. Metrics
# ==========================================================================
#
# Three separate questions, deliberately never combined into one number:
# is the invoice wrong, which category is it, and what should it have cost.

@dataclass(frozen=True)
class Label:
    invoice_id: str
    is_erroneous: bool
    error_categories: tuple[str, ...]
    expected_total_cents: Optional[int]
    ambiguity_sensitive: bool


def load_labels(path: str | Path) -> dict[str, Label]:
    out: dict[str, Label] = {}
    with Path(path).open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            cats = tuple(
                c.strip() for c in (row.get("error_categories") or "").split("|") if c.strip()
            )
            raw_total = (row.get("expected_total_cents") or "").strip()
            out[row["invoice_id"]] = Label(
                invoice_id=row["invoice_id"],
                is_erroneous=row["is_erroneous"] == "1",
                error_categories=cats,
                expected_total_cents=int(raw_total) if raw_total else None,
                ambiguity_sensitive=(row.get("ambiguity_sensitive") or "0") == "1",
            )
    return out


@dataclass
class PRF:
    tp: int = 0
    fp: int = 0
    fn: int = 0

    @property
    def precision(self) -> float:
        return self.tp / (self.tp + self.fp) if self.tp + self.fp else 0.0

    @property
    def recall(self) -> float:
        return self.tp / (self.tp + self.fn) if self.tp + self.fn else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if p + r else 0.0

    @property
    def support(self) -> int:
        return self.tp + self.fn


@dataclass
class Evaluation:
    """Three separate questions, deliberately not combined into one number."""

    name: str
    n: int = 0

    # 1. invoice-level error detection
    tp: int = 0
    fp: int = 0
    tn: int = 0
    fn: int = 0

    # 2. category attribution
    per_category: dict[str, PRF] = field(default_factory=dict)

    # 3. monetary reconstruction
    total_exact: int = 0
    total_compared: int = 0
    total_declined: int = 0
    absolute_errors: list[int] = field(default_factory=list)

    # calibration / triage
    by_band: dict[str, tuple[int, int]] = field(default_factory=dict)  # band -> (correct, n)

    @property
    def accuracy(self) -> float:
        return (self.tp + self.tn) / self.n if self.n else 0.0

    @property
    def precision(self) -> float:
        return self.tp / (self.tp + self.fp) if self.tp + self.fp else 0.0

    @property
    def recall(self) -> float:
        return self.tp / (self.tp + self.fn) if self.tp + self.fn else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if p + r else 0.0

    @property
    def exact_total_rate(self) -> float:
        return self.total_exact / self.total_compared if self.total_compared else 0.0

    @property
    def mae_cents(self) -> float:
        return sum(self.absolute_errors) / len(self.absolute_errors) if self.absolute_errors else 0.0

    @property
    def pricing_completeness(self) -> float:
        n = self.total_compared + self.total_declined
        return self.total_compared / n if n else 0.0


def evaluate(
    results: Iterable[AuditResult],
    labels: dict[str, Label],
    name: str,
    *,
    bands: Optional[dict[str, str]] = None,
) -> Evaluation:
    ev = Evaluation(name=name)
    for result in results:
        label = labels.get(result.invoice_id)
        if label is None:
            continue
        ev.n += 1

        if result.flagged and label.is_erroneous:
            ev.tp += 1
        elif result.flagged:
            ev.fp += 1
        elif label.is_erroneous:
            ev.fn += 1
        else:
            ev.tn += 1

        predicted = set(result.error_categories)
        actual = set(label.error_categories)
        for cat in predicted | actual:
            prf = ev.per_category.setdefault(cat, PRF())
            if cat in predicted and cat in actual:
                prf.tp += 1
            elif cat in predicted:
                prf.fp += 1
            else:
                prf.fn += 1

        if label.expected_total_cents is not None:
            if result.expected_total_cents is None:
                ev.total_declined += 1
            else:
                ev.total_compared += 1
                delta = abs(result.expected_total_cents - label.expected_total_cents)
                ev.absolute_errors.append(delta)
                if delta == 0:
                    ev.total_exact += 1

        if bands is not None:
            band = bands.get(result.invoice_id, result.confidence_band)
            correct, n = ev.by_band.get(band, (0, 0))
            hit = int(result.flagged == label.is_erroneous)
            ev.by_band[band] = (correct + hit, n + 1)

    return ev


def format_report(ev: Evaluation, *, title: Optional[str] = None) -> str:
    """A Markdown section.  Keeps the three questions visually separate."""
    lines: list[str] = []
    lines.append(f"## {title or ev.name}")
    lines.append("")
    lines.append(f"Invoices scored: **{ev.n}**")
    lines.append("")

    lines.append("### 1. Invoice-level error detection")
    lines.append("")
    lines.append("| metric | value |")
    lines.append("|---|---|")
    lines.append(f"| accuracy | {ev.accuracy:.4f} |")
    lines.append(f"| precision | {ev.precision:.4f} |")
    lines.append(f"| recall | {ev.recall:.4f} |")
    lines.append(f"| F1 | {ev.f1:.4f} |")
    lines.append(f"| TP / FP / TN / FN | {ev.tp} / {ev.fp} / {ev.tn} / {ev.fn} |")
    lines.append("")

    lines.append("### 2. Category attribution")
    lines.append("")
    lines.append("Support is the number of labelled invoices carrying the category. ")
    lines.append("A category with single-digit support cannot be read as a rate.")
    lines.append("")
    lines.append("| category | support | precision | recall | F1 | TP | FP | FN |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for cat in sorted(ev.per_category, key=lambda c: (-ev.per_category[c].support, c)):
        p = ev.per_category[cat]
        flag = " *" if 0 < p.support < 10 else ""
        lines.append(
            f"| `{cat}`{flag} | {p.support} | {p.precision:.3f} | {p.recall:.3f} | "
            f"{p.f1:.3f} | {p.tp} | {p.fp} | {p.fn} |"
        )
    lines.append("")
    lines.append("`*` = fewer than 10 labelled examples; treat the rate as indicative only.")
    lines.append("")

    lines.append("### 3. Monetary reconstruction")
    lines.append("")
    lines.append("| metric | value |")
    lines.append("|---|---|")
    lines.append(
        f"| corrected total offered | {ev.total_compared} of "
        f"{ev.total_compared + ev.total_declined} ({ev.pricing_completeness:.4f}) |"
    )
    lines.append(
        f"| exact match, where offered | {ev.total_exact} / {ev.total_compared} "
        f"({ev.exact_total_rate:.4f}) |"
    )
    lines.append(f"| MAE, where offered (cents) | {ev.mae_cents:.2f} |")
    lines.append(
        f"| declined as unreconstructable | {ev.total_declined} |"
    )
    lines.append("")

    if ev.by_band:
        lines.append("### Detection accuracy by stated confidence band")
        lines.append("")
        lines.append("| band | invoices | detection correct |")
        lines.append("|---|---|---|")
        for band in ("high", "medium", "low"):
            if band in ev.by_band:
                correct, n = ev.by_band[band]
                lines.append(f"| {band} | {n} | {correct}/{n} ({correct / n:.4f}) |")
        lines.append("")
        lines.append(
            "These are observed accuracies, not calibrated probabilities. The "
            "confidence value is a review priority; see section 6 of `src/hospital_1/audit.py`."
        )
        lines.append("")
    return "\n".join(lines)


# ==========================================================================
# 4. Freeze
# ==========================================================================

def source_hashes() -> dict[str, str]:
    return {rel: _sha256(REPO_ROOT / rel) for rel in FROZEN_SOURCES}


def _combined(sources: dict[str, str]) -> str:
    return hashlib.sha256(
        "".join(f"{k}:{v}" for k, v in sorted(sources.items())).encode()
    ).hexdigest()


def predictions_sha256(results: Iterable[AuditResult]) -> str:
    """Hash of ``predictions.csv`` as ``write_predictions`` would write it.

    A behavioural fingerprint: two implementations with the same hash made the
    same claim, with the same wording, about every invoice.
    """
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "predictions.csv"
        write_predictions(results, path)
        return _sha256(path)


def freeze(*, reason: Optional[str] = None) -> dict:
    """Record the prediction path as it stands.

    If a freeze already exists it is kept in ``history`` rather than
    overwritten, and a ``reason`` is required: re-freezing after the holdout
    has been read is only defensible when the reason is on the record and the
    behaviour demonstrably did not change.
    """
    previous = json.loads(FREEZE_FILE.read_text(encoding="utf-8")) if FREEZE_FILE.exists() else None
    if previous is not None and not reason:
        raise ValueError("a freeze already exists; re-freezing requires a reason")

    pipeline = Pipeline()
    results = pipeline.run()
    sources = source_hashes()
    record = {
        "frozen_utc": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "reason": reason or "initial freeze, before the holdout was read",
        "sources": sources,
        "combined_sha256": _combined(sources),
        "predictions_sha256": predictions_sha256(results),
        "matcher_thresholds": {
            "MIN_PLAUSIBLE": ServiceMatcher.MIN_PLAUSIBLE,
            "MIN_MATCH": ServiceMatcher.MIN_MATCH,
            "MIN_MARGIN": ServiceMatcher.MIN_MARGIN,
            "CONTRADICTION_DECAY": ServiceMatcher.CONTRADICTION_DECAY,
        },
        "abbreviation_count": len(ABBREVIATIONS),
        "contract": {
            "number": pipeline.rules.contract_number,
            "services": len(pipeline.rules.services),
            "threshold_premiums": len(pipeline.rules.threshold_premiums),
            "non_business_day_uplifts": len(pipeline.rules.non_business_day_uplifts),
            "volume_discount_tiers": sum(len(v) for v in pipeline.rules.volume_discounts.values()),
            "daily_caps": len(pipeline.rules.daily_caps),
            "bundles": len(pipeline.rules.bundles),
            "exclusion_windows": len(pipeline.rules.exclusion_windows),
        },
        "history": [],
    }
    if previous is not None:
        earlier = previous.pop("history", [])
        record["history"] = earlier + [
            {k: previous.get(k) for k in (
                "frozen_utc", "reason", "combined_sha256", "predictions_sha256", "sources"
            )}
        ]
    FREEZE_FILE.parent.mkdir(parents=True, exist_ok=True)
    FREEZE_FILE.write_text(json.dumps(record, indent=2), encoding="utf-8")
    return record


def frozen_state() -> tuple[Optional[dict], list[str]]:
    """``(freeze record, files changed since it)``.  No record -> ``(None, [])``."""
    if not FREEZE_FILE.exists():
        return None, []
    frozen = json.loads(FREEZE_FILE.read_text(encoding="utf-8"))
    drift = [f for f, h in source_hashes().items() if frozen["sources"].get(f) != h]
    return frozen, drift


# ==========================================================================
# 5. Reports
# ==========================================================================

def _score(results: list[AuditResult], labels_path: Path, name: str) -> Evaluation:
    labels = load_labels(labels_path)
    subset = [r for r in results if r.invoice_id in labels]
    bands = {r.invoice_id: r.confidence_band for r in subset}
    return evaluate(subset, labels, name, bands=bands)


def evaluation_report(results: list[AuditResult]) -> str:
    """Development split always; the locked holdout only while frozen.

    The holdout section is produced only when the prediction sources are
    byte-identical to the freeze record.  Change any of them and this report
    says so instead of printing a number that no longer corresponds to the
    frozen holdout result.  (The holdout is a post-hoc measurement: the full
    H1 labels were visible during development.)
    """
    dev = _score(results, DEV_LABELS, "Development split (70%)")
    parts = [
        "# Hospital 1 - evaluation\n\n"
        "Three questions, reported separately and never combined: is the invoice "
        "wrong (detection), what is wrong with it (category attribution), and "
        "what should it have cost (monetary reconstruction).\n\n"
        f"The development split is `{_rel(DEV_LABELS)}`. The split was created "
        "before the final engine implementation, but because the full H1 labels "
        "had been visible during development, the holdout is reported only as a "
        "post-hoc check, not as an untouched validation set.\n\n",
        format_report(dev),
    ]

    frozen, drift = frozen_state()
    if frozen is None:
        parts.append("\n## Locked holdout split (30%)\n\nNot evaluated: no freeze record.\n")
    elif drift:
        parts.append(
            "\n## Locked holdout split (30%)\n\nNot evaluated: prediction sources "
            f"changed since the freeze ({', '.join(drift)}). A holdout number from "
            "unfrozen code would no longer be the frozen post-hoc holdout measurement.\n"
        )
    else:
        holdout = _score(results, HOLDOUT_LABELS, "Locked holdout split (30%)")
        first = (frozen.get("history") or [frozen])[0]
        parts.append(
            "\n---\n\n"
            f"The holdout was first scored once, on implementation "
            f"`{first['combined_sha256'][:16]}` frozen {first['frozen_utc']}. No "
            "logic was changed in response to it. The current prediction sources "
            f"match freeze `{frozen['combined_sha256'][:16]}` "
            f"({frozen['reason']}).\n\n"
            + format_report(holdout)
        )
    return "\n".join(parts)


# ==========================================================================
# 6. Research: temporal evaluation and ablations
# ==========================================================================

def temporal_report() -> str:
    """Prospective evaluation: earlier invoices inform later ones, never the reverse.

    Each invoice is re-audited against a context built only from lines whose
    service date is on or before that invoice's own latest service date.  That
    is the information a payer would actually hold when the invoice arrives.
    """
    pipeline = Pipeline()
    labels = load_labels(DEV_LABELS)

    # Group invoices by their latest service date, then walk forward.  One
    # context per distinct cut-off keeps this tractable without ever letting a
    # later line influence an earlier decision.
    #
    # Grouping is by *identifier*, not by physical occurrence: a reused
    # identifier produces one claim, so it must be scored once, at the cut-off
    # of the record that is the subject of that claim.
    by_id: dict[str, list[InvoiceOccurrence]] = defaultdict(list)
    for occ in pipeline.occurrences:
        by_id[occ.invoice_id].append(occ)

    cutoffs: dict[_dt.date, list[InvoiceOccurrence]] = defaultdict(list)
    undated: list[InvoiceOccurrence] = []
    for invoice_id in sorted(by_id):
        subject = by_id[invoice_id][-1]
        dates = [li.service_date for li in subject.line_items if li.service_date]
        if dates:
            cutoffs[max(dates)].append(subject)
        else:
            undated.append(subject)

    total_lines = len(pipeline.resolved)
    withheld: list[float] = []

    results: list[AuditResult] = []
    for cutoff in sorted(cutoffs):
        ctx = AuditContext(pipeline.rules, pipeline.resolved, as_of=cutoff)
        visible = sum(
            1 for rl in pipeline.resolved if rl.service_date and rl.service_date <= cutoff
        )
        withheld.extend([1 - visible / total_lines] * len(cutoffs[cutoff]))
        results.extend(
            pipeline.auditor.audit_with_context(
                pipeline.occurrences, ctx, subjects=cutoffs[cutoff]
            )
        )
    if undated:
        ctx = AuditContext(pipeline.rules, pipeline.resolved)
        results.extend(
            pipeline.auditor.audit_with_context(pipeline.occurrences, ctx, subjects=undated)
        )
    pipeline.attach_confidence(results)

    subset = [r for r in results if r.invoice_id in labels]
    ev = evaluate(subset, labels, "Temporal (prospective) evaluation, development split",
                  bands={r.invoice_id: r.confidence_band for r in subset})
    header = (
        "# Hospital 1 - temporal (prospective) evaluation\n\n"
        "Every invoice is audited against a context containing only lines whose "
        "service date is on or before that invoice's own latest service date. "
        "Cumulative utilisation, exclusion history and duplicate detection "
        "therefore see the past only.\n\n"
        "Scored on the **development** split, so it is comparable with the "
        "retrospective development numbers and independent of the holdout.\n\n"
        f"The cut-off had teeth: averaged over the scored invoices, "
        f"**{sum(withheld) / len(withheld):.1%}** of all line items were hidden "
        f"from the context used to audit them (median "
        f"{sorted(withheld)[len(withheld) // 2]:.1%}, maximum "
        f"{max(withheld):.1%}). The earliest invoices were judged against "
        f"almost no history at all.\n\n"
    )
    return header + format_report(ev)


def ablation_report() -> str:
    """Turn one rule family off at a time and measure the damage, on dev only."""
    labels = load_labels(DEV_LABELS)
    configs: list[tuple[str, dict, dict]] = [
        ("full system", {}, {}),
        ("no description normalisation", {"normalisation": False}, {}),
        ("no bundles", {}, {"bundles": False}),
        ("no premiums / uplifts", {}, {"premiums": False}),
        ("no volume discounts", {}, {"volume_discounts": False}),
        ("no duplicate detection", {"enable_duplicate_detection": False}, {}),
        ("no date validation", {"enable_date_validation": False}, {}),
    ]

    rows = []
    for name, kwargs, price_kwargs in configs:
        normalisation = kwargs.pop("normalisation", True)
        pipeline = _build_pipeline(normalisation, PricingOptions(**price_kwargs), **kwargs)
        results = pipeline.run()
        subset = [r for r in results if r.invoice_id in labels]
        rows.append((name, evaluate(subset, labels, name)))

    lines = [
        "# Hospital 1 - ablation study",
        "",
        "Each row disables one component and re-runs the whole audit against the "
        "**development** split. A component that changes nothing when removed is "
        "either never exercised by this data or is not doing what it claims.",
        "",
        "| configuration | precision | recall | F1 | TP | FP | FN | exact corrected totals |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for name, ev in rows:
        lines.append(
            f"| {name} | {ev.precision:.3f} | {ev.recall:.3f} | {ev.f1:.3f} | "
            f"{ev.tp} | {ev.fp} | {ev.fn} | {ev.total_exact}/{ev.total_compared} |"
        )
    lines.append("")
    return "\n".join(lines)


def _build_pipeline(normalisation: bool, options: PricingOptions, **kwargs) -> Pipeline:
    """Build a pipeline, optionally crippling description normalisation.

    Disabling normalisation is done by replacing it with an identity function,
    so the ablation isolates normalisation: the matcher still runs, but it
    no longer strips reference suffixes, punctuation or casing.  The pipeline
    matches every line while it is being built, so restoring the function
    afterwards does not undo the ablation.
    """
    if not normalisation:
        original = matcher_mod.normalize_description
        matcher_mod.normalize_description = lambda d: d  # type: ignore[assignment]
        try:
            return Pipeline(options, **kwargs)
        finally:
            matcher_mod.normalize_description = original  # type: ignore[assignment]
    return Pipeline(options, **kwargs)
