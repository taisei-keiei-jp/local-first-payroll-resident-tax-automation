from __future__ import annotations

from pathlib import Path
from typing import Any

from accounting_csv import create_payslip_csv
from database import record_issued_file
from payslip_pdf import create_payslip_pdf
from payroll_core import PayrollResult


def generate_payroll_pdf_csv(
    history_id: int,
    result: PayrollResult,
    csv_metadata: dict[str, Any] | None = None,
) -> tuple[Path, Path, dict[str, Any]]:
    """保存済み履歴から正式給与PDF/CSVを同時生成し、発行監査を記録する唯一の入口。"""
    csv_metadata = csv_metadata or {}
    pdf_path = create_payslip_pdf(result)
    csv_path = create_payslip_csv(result, pdf_filename=pdf_path.name, **csv_metadata)
    pdf_issue = record_issued_file(history_id, "payslip_pdf", pdf_path)
    csv_issue = record_issued_file(history_id, "management_csv", csv_path)
    return pdf_path, csv_path, {"pdf": pdf_issue, "csv": csv_issue}
