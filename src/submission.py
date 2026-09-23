"""Hospital submission files and the combined challenge submission.

One serialisation path.  Each scored hospital's rows are written once, by
``shared.submission.write_submission``, to ``outputs/<hospital>/submission.csv``.
The combined ``outputs/submission.csv`` is built from those files by copying
their data rows unchanged.  So a hospital file and its rows in the combined
file cannot disagree.

Only *scored, implemented* hospitals appear here: Hospitals 2 and 4.
Hospital 1 is the labelled development hospital; the challenge does not score
it, so it never enters the combined file.  Hospitals 3 and 5 are not
implemented, and no file is invented for them.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Iterable

from .shared.submission import SUBMISSION_COLUMNS, write_submission

# Implemented hospitals whose rows belong in the scored submission, in output order.
SCORED_HOSPITALS: tuple[str, ...] = ("hospital_2", "hospital_4")
UNSCORED_HOSPITALS: tuple[str, ...] = ("hospital_1",)


def write_hospital_submission(results: Iterable, path: Path, *, template: Path) -> int:
    """Serialise one hospital's rows in the template format.  Returns the row count."""
    return write_submission(results, path, template=template)


def _read(path: Path) -> list[list[str]]:
    with path.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.reader(fh))
    if not rows or tuple(rows[0]) != SUBMISSION_COLUMNS:
        raise ValueError(f"{path} does not start with the submission header {SUBMISSION_COLUMNS}")
    return rows[1:]


def combine_submissions(hospital_files: dict[str, Path], path: Path) -> dict[str, int]:
    """Concatenate hospital submission files into the combined submission.

    Rows are copied unchanged, in ``SCORED_HOSPITALS`` order.  Within each
    hospital they keep that file's order (sorted by invoice id).  The call is
    refused if it is given an unscored or unknown hospital, a scored hospital
    is missing, or an invoice id appears twice.
    """
    unknown = set(hospital_files) - set(SCORED_HOSPITALS)
    if unknown:
        raise ValueError(f"not scored hospitals, refusing to include: {sorted(unknown)}")
    missing = set(SCORED_HOSPITALS) - set(hospital_files)
    if missing:
        raise ValueError(f"scored hospital file(s) missing: {sorted(missing)}")

    counts: dict[str, int] = {}
    seen: set[str] = set()
    combined: list[list[str]] = []
    for hospital in SCORED_HOSPITALS:
        rows = _read(hospital_files[hospital])
        for row in rows:
            if row[0] in seen:
                raise ValueError(f"invoice id {row[0]} appears more than once")
            seen.add(row[0])
        combined.extend(rows)
        counts[hospital] = len(rows)

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(SUBMISSION_COLUMNS)
        writer.writerows(combined)
    return counts
