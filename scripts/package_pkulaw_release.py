#!/usr/bin/env python3
"""Build the canonical PKULaw 2023-2025 engineering handoff package."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import tarfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DOC_ID_RE = re.compile(r"\((FBMCLI\.[A-Z]\.[0-9]+)\)", re.IGNORECASE)
SUPERSEDED_DIRS = {
    "2024/bulk50_page_017",
    "2025/bulk50_page_002",
    "2025/bulk50_page_011",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_raw_files(extracted_root: Path) -> list[Path]:
    files: list[Path] = []
    for path in sorted(extracted_root.glob("*/*/*.txt")):
        relative = path.relative_to(extracted_root)
        directory = relative.parent.as_posix()
        if not relative.parts[1].startswith("bulk50_page_"):
            continue
        if directory in SUPERSEDED_DIRS:
            continue
        files.append(path)
    return files


def record_id(path: Path) -> str:
    match = DOC_ID_RE.search(path.name)
    return match.group(1).upper() if match else path.stem


def write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--data-root",
        type=Path,
        default=Path("data/pkulaw_fulltext_bulk"),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/pkulaw_2023_2025_release"),
    )
    args = parser.parse_args()

    data_root = args.data_root.resolve()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    pipeline_dir = data_root / "pipeline_ready"
    all_cases = json.loads((pipeline_dir / "cases.json").read_text(encoding="utf-8"))
    runnable_cases = json.loads(
        (pipeline_dir / "pipeline_ready_cases.json").read_text(encoding="utf-8")
    )
    excluded_cases = {
        case_id: case
        for case_id, case in all_cases.items()
        if case_id not in runnable_cases
    }

    raw_files = canonical_raw_files(data_root / "extracted")
    raw_ids = [record_id(path) for path in raw_files]
    if len(raw_files) != 4224 or len(set(raw_ids)) != 4224:
        raise SystemExit(
            "canonical raw validation failed: "
            f"files={len(raw_files)}, unique_ids={len(set(raw_ids))}"
        )
    if len(runnable_cases) != 4193 or len(excluded_cases) != 31:
        raise SystemExit(
            "pipeline split validation failed: "
            f"runnable={len(runnable_cases)}, excluded={len(excluded_cases)}"
        )

    runnable_path = output_dir / "pipeline_ready_cases.json"
    excluded_path = output_dir / "excluded_missing_judgment.json"
    raw_archive_path = output_dir / "pkulaw_raw_canonical_4224.tgz"
    shutil.copy2(pipeline_dir / "pipeline_ready_cases.json", runnable_path)
    write_json(excluded_path, excluded_cases)

    with tarfile.open(raw_archive_path, "w:gz") as archive:
        for path in raw_files:
            archive.add(
                path,
                arcname=path.relative_to(data_root / "extracted").as_posix(),
                recursive=False,
            )

    year_counts = Counter(
        case["metadata"]["decision_year"] for case in all_cases.values()
    )
    artifacts = {}
    for path in (runnable_path, excluded_path, raw_archive_path):
        artifacts[path.name] = {
            "bytes": path.stat().st_size,
            "sha256": sha256(path),
        }

    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_database": "北大法宝",
        "query": {
            "full_text": "家庭暴力",
            "decision_date": ["2023-01-01", "2025-12-31"],
            "case_types": ["刑事案件", "民事案件"],
            "document_type": "判决书",
        },
        "counts": {
            "canonical_raw": len(raw_files),
            "canonical_unique_record_ids": len(set(raw_ids)),
            "pipeline_ready": len(runnable_cases),
            "excluded_missing_judgment": len(excluded_cases),
            "by_decision_year": {str(key): value for key, value in sorted(year_counts.items())},
        },
        "superseded_audit_directories_not_packaged": sorted(SUPERSEDED_DIRS),
        "artifacts": artifacts,
    }
    manifest_path = output_dir / "manifest.json"
    write_json(manifest_path, manifest)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
