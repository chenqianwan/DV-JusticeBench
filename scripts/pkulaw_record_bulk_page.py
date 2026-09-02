#!/usr/bin/env python3
"""Validate and checkpoint one PKULaw bulk-download page."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import tempfile
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = ROOT / "data" / "pkulaw_fulltext_bulk"
PROGRESS_PATH = DATA_ROOT / "progress.json"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("download", type=Path)
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--page", type=int, required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--expected", type=int, required=True)
    parser.add_argument("--correction", action="store_true")
    args = parser.parse_args()

    if not args.download.is_file():
        raise SystemExit(f"download not found: {args.download}")
    if not zipfile.is_zipfile(args.download):
        raise SystemExit(f"not a valid ZIP: {args.download}")

    progress = json.loads(PROGRESS_PATH.read_text(encoding="utf-8"))
    existing = [
        item
        for item in progress.get("bulk50_completed", [])
        if item.get("year") == args.year and item.get("page") == args.page
    ]
    if existing and not args.correction:
        raise SystemExit(f"page already checkpointed: {existing}")
    if args.correction and any(item.get("correction") for item in existing):
        raise SystemExit(f"page correction already checkpointed: {existing}")

    suffix = "_corrected" if args.correction else ""
    rel_zip = (
        Path("raw")
        / str(args.year)
        / "bulk50"
        / f"page_{args.page:03d}{suffix}.zip"
    )
    dst_zip = DATA_ROOT / rel_zip
    extract_dir = (
        DATA_ROOT
        / "extracted"
        / str(args.year)
        / f"bulk50_page_{args.page:03d}{suffix}"
    )
    if dst_zip.exists() or extract_dir.exists():
        raise SystemExit(f"target already exists: {dst_zip} or {extract_dir}")

    dst_zip.parent.mkdir(parents=True, exist_ok=True)
    extract_dir.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(args.download, dst_zip)
    extract_dir.mkdir()
    with zipfile.ZipFile(dst_zip) as archive:
        archive.extractall(extract_dir)

    text_files = sorted(extract_dir.rglob("*.txt"))
    if len(text_files) != args.expected:
        raise SystemExit(
            f"unexpected text count for {args.year} page {args.page}: "
            f"{len(text_files)} != {args.expected}"
        )
    empty_files = [str(path) for path in text_files if path.stat().st_size == 0]
    if empty_files:
        raise SystemExit(f"empty text files found: {empty_files[:5]}")

    record = {
        "year": args.year,
        "page": args.page,
        "file_count": len(text_files),
        "task_id": args.task_id,
        "zip": rel_zip.as_posix(),
    }
    if args.correction:
        record["correction"] = True
    progress.setdefault("bulk50_completed", []).append(record)
    payload = json.dumps(progress, ensure_ascii=False, indent=2) + "\n"
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=PROGRESS_PATH.parent, delete=False
    ) as tmp:
        tmp.write(payload)
        tmp_path = Path(tmp.name)
    os.replace(tmp_path, PROGRESS_PATH)

    print(
        json.dumps(
            {
                "year": args.year,
                "page": args.page,
                "text_count": len(text_files),
                "zip": str(dst_zip),
                "extract_dir": str(extract_dir),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
