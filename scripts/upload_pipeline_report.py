#!/usr/bin/env python3
"""
scripts/upload_pipeline_report.py
==================================
Uploads logs/pipeline_report.txt to the Supabase pipeline_reports table
after every GitHub Actions pipeline run.

Called from the workflow:
  python3 scripts/upload_pipeline_report.py \
      --run-id "${{ github.run_id }}" \
      --conclusion "${{ job.status }}" \
      --workflow "Daily Signal Pipeline"

The report content is pure pipeline stdout — no CI runner noise.
The dashboard reads it via GET /api/workflows/report/text.
"""

import argparse
import os
import sys
from pathlib import Path

# Allow running from repo root (GitHub Actions cwd) or scripts/
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv
load_dotenv()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id",    required=True, help="GitHub Actions run ID")
    parser.add_argument("--conclusion", default="unknown", help="Job conclusion (success/failure/etc.)")
    parser.add_argument("--workflow",  default="Signal Pipeline", help="Workflow display name")
    parser.add_argument("--report",    default="logs/pipeline_report.txt", help="Path to report file")
    args = parser.parse_args()

    report_path = Path(args.report)
    if not report_path.exists():
        print(f"[upload_report] Report file not found: {report_path} — skipping upload", file=sys.stderr)
        sys.exit(0)

    content = report_path.read_text(encoding="utf-8", errors="replace").strip()
    if not content:
        print("[upload_report] Report file is empty — skipping upload", file=sys.stderr)
        sys.exit(0)

    print(f"[upload_report] Uploading {len(content):,} chars to Supabase pipeline_reports …")

    try:
        from utils.db import get_connection
        conn = get_connection()
        cur = conn.cursor()

        cur.execute("""
            CREATE TABLE IF NOT EXISTS pipeline_reports (
                id            BIGSERIAL PRIMARY KEY,
                run_at        TIMESTAMPTZ DEFAULT NOW(),
                run_id        TEXT,
                workflow_name TEXT,
                conclusion    TEXT,
                content       TEXT
            )
        """)

        cur.execute(
            """
            INSERT INTO pipeline_reports (run_id, workflow_name, conclusion, content)
            VALUES (%s, %s, %s, %s)
            """,
            (str(args.run_id), args.workflow, args.conclusion, content),
        )
        conn.commit()
        conn.close()
        print(f"[upload_report] Done — run_id={args.run_id}, conclusion={args.conclusion}")
    except Exception as exc:
        print(f"[upload_report] Upload failed: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
