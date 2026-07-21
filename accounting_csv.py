from __future__ import annotations

import csv
from calendar import monthrange
from datetime import datetime
from pathlib import Path
from typing import Any

from payslip_pdf import payslip_filename
from payroll_core import PayrollResult, load_json


BASE_DIR = Path(__file__).resolve().parent
CSV_DIR = BASE_DIR / "output" / "csv"
CSV_FORMAT_VERSION = "1.0"

CSV_COLUMNS = [
    "CSV形式バージョン",
    "会社コード",
    "会社名",
    "支給年月",
    "支給年",
    "支給月",
    "締日",
    "支給日",
    "従業員コード",
    "従業員名",
    "役職区分",
    "扶養人数",
    "勤務日数",
    "基本給",
    "役員報酬",
    "現場手当",
    "皆勤手当",
    "休日出勤日数",
    "休日出勤手当",
    "夜間日数",
    "夜間手当",
    "半徹日数",
    "半徹手当",
    "総支給額",
    "健康保険",
    "厚生年金",
    "雇用保険",
    "社会保険料合計",
    "源泉所得税",
    "住民税",
    "その他控除",
    "控除合計",
    "差引支給額",
    "発行状態",
    "再発行回数",
    "最終再発行日時",
    "PDFファイル名",
    "CSV作成日時",
    "取込用キー",
]


def csv_filename(result: PayrollResult) -> str:
    return Path(payslip_filename(result)).with_suffix(".csv").name


def employee_code(employee_name: str) -> str:
    employee = load_json("employee_master.json").get(employee_name, {})
    return str(employee.get("employee_id", "")).zfill(6)


def payment_period_label(result: PayrollResult) -> str:
    return f"令和{result.payment_date.year - 2018}年{result.payment_date.month}月"


def closing_date_iso(result: PayrollResult) -> str:
    last_day = monthrange(result.payroll_month.year, result.payroll_month.month)[1]
    return f"{result.payroll_month.year:04d}-{result.payroll_month.month:02d}-{last_day:02d}"


def int_value(value: Any) -> int:
    return int(value or 0)


def csv_row(
    result: PayrollResult,
    *,
    issue_status: str = "発行済",
    reissue_count: int = 0,
    latest_reissued_at: str = "",
    pdf_filename: str | None = None,
    created_at: datetime | None = None,
) -> dict[str, Any]:
    created_at = created_at or datetime.now()
    employee_code_value = employee_code(result.employee_name)
    pdf_name = pdf_filename or payslip_filename(result)
    # CSV列には介護保険料・子ども子育て支援金の単独列がないため、
    # 管理用CSVでは健保系控除として「健康保険」列に合算して出力する。
    health_related = int_value(result.health_insurance) + int_value(result.care_insurance) + int_value(result.child_support)
    social_total = health_related + int_value(result.pension_insurance) + int_value(result.employment_insurance)
    total_deductions = (
        social_total
        + int_value(result.withholding_income_tax)
        + int_value(result.resident_tax)
        + int_value(result.other_deduction)
    )
    net_pay = int_value(result.gross_pay) - total_deductions
    company = load_json("company_config.json")
    return {
        "CSV形式バージョン": CSV_FORMAT_VERSION,
        "会社コード": str(company.get("company_code", "")),
        "会社名": str(company.get("legal_company_name") or company.get("company_name") or ""),
        "支給年月": payment_period_label(result),
        "支給年": result.payment_date.year,
        "支給月": result.payment_date.month,
        "締日": closing_date_iso(result),
        "支給日": result.payment_date.isoformat(),
        "従業員コード": employee_code_value,
        "従業員名": result.employee_name,
        "役職区分": result.role,
        "扶養人数": int_value(result.dependents),
        "勤務日数": int_value(result.work_days),
        "基本給": int_value(result.basic_salary),
        "役員報酬": int_value(result.executive_compensation),
        "現場手当": int_value(result.site_allowance),
        "皆勤手当": int_value(result.attendance_allowance),
        "休日出勤日数": int_value(result.holiday_work_days),
        "休日出勤手当": int_value(result.holiday_work_allowance),
        "夜間日数": int_value(result.night_work_days),
        "夜間手当": int_value(result.night_allowance),
        "半徹日数": int_value(result.half_night_work_days),
        "半徹手当": int_value(result.half_night_allowance),
        "総支給額": int_value(result.gross_pay),
        "健康保険": health_related,
        "厚生年金": int_value(result.pension_insurance),
        "雇用保険": int_value(result.employment_insurance),
        "社会保険料合計": social_total,
        "源泉所得税": int_value(result.withholding_income_tax),
        "住民税": int_value(result.resident_tax),
        "その他控除": int_value(result.other_deduction),
        "控除合計": total_deductions,
        "差引支給額": net_pay,
        "発行状態": issue_status,
        "再発行回数": int_value(reissue_count),
        "最終再発行日時": latest_reissued_at,
        "PDFファイル名": pdf_name,
        "CSV作成日時": created_at.strftime("%Y-%m-%d %H:%M:%S"),
        "取込用キー": f"{company.get('company_code', '')}_{result.payment_date.isoformat()}_{employee_code_value}",
    }


def create_payslip_csv(
    result: PayrollResult,
    output_dir: Path = CSV_DIR,
    *,
    issue_status: str = "発行済",
    reissue_count: int = 0,
    latest_reissued_at: str = "",
    pdf_filename: str | None = None,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / csv_filename(result)
    row = csv_row(
        result,
        issue_status=issue_status,
        reissue_count=reissue_count,
        latest_reissued_at=latest_reissued_at,
        pdf_filename=pdf_filename,
    )
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerow(row)
    return path


def export_yayoi_csv_placeholder() -> Path:
    """弥生会計CSV出力は次フェーズ実装予定です。"""
    CSV_DIR.mkdir(parents=True, exist_ok=True)
    raise NotImplementedError("弥生会計CSV出力はVer.0.2以降で実装予定です。")
