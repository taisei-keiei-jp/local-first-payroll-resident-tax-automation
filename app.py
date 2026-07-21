from __future__ import annotations

import base64
import json
import logging
import os
import re
import threading
import time
import unicodedata
from calendar import monthrange
from datetime import date, datetime
from pathlib import Path

from runtime_bootstrap import bootstrap_runtime

bootstrap_runtime()

from download_package import create_pdf_zip_bytes, payroll_documents_zip_filename

try:
    import pandas as pd
    import streamlit as st
except ModuleNotFoundError as exc:  # pragma: no cover - UI environment guard
    missing = exc.name
    raise SystemExit(
        f"{missing} がインストールされていません。README.md の手順に従い、必要ライブラリをインストールしてください。"
    ) from exc

from database import (
    activate_resident_tax_notice,
    confirm_resident_tax_notice,
    create_resident_tax_source_document,
    delete_payroll_history,
    fetch_history,
    fetch_issued_files,
    fetch_resident_tax_notices,
    fetch_resident_tax_source_documents,
    get_confirmed_resident_tax,
    record_issued_file,
    save_payroll_result,
    update_resident_tax_notice_employee_link,
    update_resident_tax_source_document_type,
)
from payroll_issuance import generate_payroll_pdf_csv
from payslip_pdf import (
    create_payslip_pdf,
    payslip_document_filename_label,
)
from payroll_core import (
    RESIDENT_TAX_MONTHS,
    PayrollInput,
    PayrollResult,
    add_month,
    calculate_payroll,
    get_resident_tax_collection_type,
    load_all_masters,
    save_json,
    update_resident_tax_amount,
    wareki_date,
    wareki_year_month,
)
from report_pdf import (
    ReportConfigurationError,
    create_employer_insurance_pdf,
    create_payroll_summary_pdf,
    create_resident_tax_pdf,
    employee_code_for_name,
    resident_tax_targets,
)
from resident_tax_service import (
    MONTHS as NOTICE_MONTHS,
    PdfPasswordRequiredError,
    extract_notice,
    document_type_label,
    inspect_pdf_security,
    ocr_status,
    preview_images,
    preview_region_image,
    save_source_file,
    sha256_bytes,
    validate_notice_import,
)


BASE_DIR = Path(__file__).resolve().parent
LOGO_PATH = BASE_DIR / "assets" / "demo_logo.png"
LOGGER = logging.getLogger(__name__)
REPORT_ISSUE_CHECKBOX_KEYS = (
    "report_issue_payslips",
    "report_issue_payroll_summary",
    "report_issue_employer_insurance",
    "report_issue_resident_tax",
)
REPORT_SELECT_ALL_KEY = "report_select_all"


st.set_page_config(
    page_title="Local-First Payroll & Resident Tax Automation",
    layout="wide",
)


def sync_report_items_from_select_all() -> None:
    selected = bool(st.session_state.get(REPORT_SELECT_ALL_KEY, False))
    for key in REPORT_ISSUE_CHECKBOX_KEYS:
        st.session_state[key] = selected


def sync_report_select_all_from_items() -> None:
    st.session_state[REPORT_SELECT_ALL_KEY] = all(
        bool(st.session_state.get(key, False)) for key in REPORT_ISSUE_CHECKBOX_KEYS
    )


def yen(value: int) -> str:
    return f"{value:,.0f} 円"


def days(value: int) -> str:
    return f"{int(value or 0)}日"


def issue_status_label(value: str | None) -> str:
    return {"issued": "発行済", "reissued": "修正済"}.get(value or "issued", "発行済")


def health_deduct_label(value: int | None) -> str:
    return "控除する" if int(value if value is not None else 1) else "控除しない"


def allowance_label(label: str, work_days: int) -> str:
    return f"{label}（{int(work_days)}日）" if int(work_days or 0) > 0 else label


def format_history_datetime(value: str | None) -> str:
    if not value:
        return "日時不明"
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return str(value)
    return parsed.strftime("%Y/%m/%d %H:%M")


def format_csv_datetime(value: str | None) -> str:
    if not value:
        return ""
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return str(value)
    return parsed.strftime("%Y-%m-%d %H:%M:%S")


def csv_issue_status_label(value: str | None) -> str:
    return "修正再発行済" if value == "reissued" else "発行済"


def set_text_input_default(key: str, value: int | str) -> None:
    st.session_state[key] = "" if value in (None, 0, "0") else str(value)


def month_range(start: date, months: int) -> list[date]:
    return [date(start.year + (start.month - 1 + i) // 12, (start.month - 1 + i) % 12 + 1, 1) for i in range(months)]


def payment_month_options(start: date = date(2026, 1, 1), months: int = 36) -> list[tuple[str, date]]:
    return [(wareki_year_month(add_month(start, i)), add_month(start, i)) for i in range(months)]


def closing_month_from_payment_month(payment_month: date) -> date:
    return add_month(date(payment_month.year, payment_month.month, 1), -1)


def payment_month_label_from_date(value: date) -> str:
    return wareki_year_month(date(value.year, value.month, 1))


def result_payment_month_label(result: PayrollResult) -> str:
    return payment_month_label_from_date(result.payment_date)


def parse_non_negative_int(label: str, value: str) -> tuple[int, str | None]:
    raw = "" if value is None else str(value).strip()
    if not raw:
        return 0, None
    normalized = unicodedata.normalize("NFKC", raw).replace(",", "").strip()
    if not re.fullmatch(r"\d+", normalized):
        return 0, f"{label}は0以上の整数で入力してください。"
    return int(normalized), None


def blank_int_input(container, label: str, *, key: str, help: str | None = None) -> str:
    return container.text_input(label, key=key, help=help)


def parse_inputs(values: list[tuple[str, str, str]]) -> tuple[dict[str, int], list[str]]:
    parsed: dict[str, int] = {}
    errors: list[str] = []
    for key, label, raw in values:
        parsed_value, error = parse_non_negative_int(label, raw)
        parsed[key] = parsed_value
        if error:
            errors.append(error)
    return parsed, errors


def header() -> None:
    # 表示用表の汎用CSVエクスポートを隠し、正式給与CSVの導線を統合発行画面に限定する。
    st.markdown(
        "<style>[data-testid='stElementToolbar']{display:none !important;}</style>",
        unsafe_allow_html=True,
    )
    st.markdown("# OpenAI Build Week Demo")
    st.markdown("## Local-First Payroll & Resident Tax Automation for Japanese Small Businesses")
    st.info(
        "All names, companies, documents, and amounts in this application are fictitious. "
        "All processing is performed locally on this PC. No payroll or tax data is sent to the cloud.\n\n"
        "このアプリ内の会社・氏名・書類・金額はすべて架空です。処理はこのPC内で完結し、給与・税務データをクラウドへ送信しません。"
    )


def sidebar_logo() -> None:
    if not LOGO_PATH.exists():
        return
    encoded_logo = base64.b64encode(LOGO_PATH.read_bytes()).decode("ascii")
    st.sidebar.markdown(
        f"""
        <div style="padding-left:14px; padding-top:2px; padding-bottom:12px;">
            <img src="data:image/png;base64,{encoded_logo}"
                 style="width:230px; max-width:92%; height:auto; display:block;" />
        </div>
        """,
        unsafe_allow_html=True,
    )


def result_cards(result) -> None:
    cols = st.columns(4)
    cols[0].metric("総支給額", yen(result.gross_pay))
    cols[1].metric("控除合計", yen(result.total_deductions))
    cols[2].metric("差引支給額", yen(result.net_pay))
    cols[3].metric("源泉税判定額", yen(result.withholding_tax_base))

    st.subheader("計算結果")
    st.markdown("#### 支給項目")
    if result.executive_compensation:
        payment_items = [
            ("役員報酬", yen(result.executive_compensation)),
            ("総支給額", yen(result.gross_pay)),
        ]
    else:
        payment_items = [
            ("基本給", yen(result.basic_salary)),
            ("現場手当", yen(result.site_allowance)),
            ("皆勤手当", yen(result.attendance_allowance)),
            (allowance_label("休日出勤", result.holiday_work_days), yen(result.holiday_work_allowance)),
            (allowance_label("夜間手当", result.night_work_days), yen(result.night_allowance)),
            (allowance_label("半徹手当", result.half_night_work_days), yen(result.half_night_allowance)),
            ("総支給額", yen(result.gross_pay)),
        ]
    st.dataframe(pd.DataFrame(payment_items, columns=["項目", "金額"]), hide_index=True, use_container_width=True)

    data = [
        ("支給年月", result_payment_month_label(result)),
        ("締年月", result.payroll_month_label),
        ("支給日", result.payment_date_label),
        ("勤務日数", days(result.work_days)),
        ("扶養人数", f"{result.dependents}名"),
        ("控除対象社会保険料年月", result.social_insurance_month_label),
        ("住民税対象", f"{result.resident_tax_year} {result.resident_tax_month}"),
        ("住民税市区町村", result.resident_tax_municipality or "未確定"),
        ("参照通知書ID", str(result.resident_tax_notice_id or "なし（手入力）")),
        ("通知書確定日時", result.resident_tax_notice_confirmed_at or "なし"),
        ("通知書手動修正", "あり" if result.resident_tax_notice_manual_corrected else "なし"),
        ("健保標準報酬月額", yen(result.standard_monthly_health)),
        ("厚年標準報酬月額", yen(result.standard_monthly_pension)),
        ("健保系控除", health_deduct_label(result.health_insurance_deduct_enabled)),
        ("健康保険料", yen(result.health_insurance)),
        ("介護保険料", yen(result.care_insurance)),
        ("子ども・子育て支援金", yen(result.child_support)),
        ("厚生年金保険料", yen(result.pension_insurance)),
        ("雇用保険料", yen(result.employment_insurance)),
        ("社会保険料等合計", yen(result.social_insurance_total)),
        ("源泉所得税", yen(result.withholding_income_tax)),
        ("住民税特別徴収額", yen(result.resident_tax)),
        ("通知書の元月額", yen(int(result.resident_tax_original_amount or 0))),
        ("給与計算時の変更", "あり" if result.resident_tax_override else "なし"),
        ("住民税変更理由", result.resident_tax_override_reason or "なし"),
        ("その他控除", yen(result.other_deduction)),
    ]
    st.dataframe(pd.DataFrame(data, columns=["項目", "内容"]), hide_index=True, use_container_width=True)


def result_from_history_row(row: dict) -> PayrollResult:
    data = json.loads(row["result_json"])
    data.setdefault("work_days", int(row.get("work_days") or 0))
    data.setdefault("holiday_work_days", int(row.get("holiday_work_days") or 0))
    data.setdefault("night_work_days", int(row.get("night_work_days") or 0))
    data.setdefault("half_night_work_days", int(row.get("half_night_work_days") or 0))
    data["meal_deduction"] = 0
    data["total_deductions"] = (
        int(data.get("social_insurance_total") or 0)
        + int(data.get("withholding_income_tax") or 0)
        + int(data.get("resident_tax") or 0)
        + int(data.get("other_deduction") or 0)
    )
    data["net_pay"] = int(data.get("gross_pay") or 0) - int(data["total_deductions"])
    if "health_insurance_deduct_enabled" not in data:
        value = row.get("health_insurance_deduct_enabled")
        data["health_insurance_deduct_enabled"] = 1 if value is None else int(value)
    data["payroll_month"] = date.fromisoformat(data["payroll_month"])
    data["payment_date"] = date.fromisoformat(data["payment_date"])
    saved_dependents = row.get("dependent_count")
    if saved_dependents is not None:
        data["dependents"] = int(saved_dependents)
    elif data.get("dependents") is None:
        employee = load_all_masters()["employees"].get(data.get("employee_name"), {})
        data["dependents"] = int(employee.get("dependents", 0))
    for key, default in {
        "resident_tax_notice_id": row.get("resident_tax_notice_id"),
        "resident_tax_original_amount": row.get("resident_tax_original_amount"),
        "resident_tax_used_amount": row.get("resident_tax_used_amount", data.get("resident_tax")),
        "resident_tax_override": int(row.get("resident_tax_override") or 0),
        "resident_tax_override_reason": row.get("resident_tax_override_reason") or "",
        "resident_tax_override_at": row.get("resident_tax_override_at"),
        "resident_tax_municipality": "",
        "resident_tax_notice_confirmed_at": "",
        "resident_tax_notice_manual_corrected": 0,
    }.items():
        data.setdefault(key, default)
    return PayrollResult(**data)


def csv_metadata_for_history(rows: list[dict], row: dict, result: PayrollResult) -> dict[str, str | int]:
    payment_month = date(result.payment_date.year, result.payment_date.month, 1)
    reissued_rows = []
    for candidate in rows:
        candidate_payment_date = date.fromisoformat(candidate["payment_date"])
        candidate_payment_month = date(candidate_payment_date.year, candidate_payment_date.month, 1)
        if (
            candidate["employee_name"] == result.employee_name
            and candidate_payment_month == payment_month
            and candidate.get("issue_status") == "reissued"
        ):
            reissued_rows.append(candidate)
    latest_reissued_at = max((candidate.get("calculated_at") for candidate in reissued_rows if candidate.get("calculated_at")), default="")
    return {
        "issue_status": csv_issue_status_label(row.get("issue_status")),
        "reissue_count": len(reissued_rows),
        "latest_reissued_at": format_csv_datetime(latest_reissued_at),
    }


def reissue_number_for_history(rows: list[dict], row: dict) -> int:
    if row.get("issue_status") != "reissued":
        return 0
    payment_date = date.fromisoformat(row["payment_date"])
    payment_month = date(payment_date.year, payment_date.month, 1)
    reissued_rows = []
    for candidate in rows:
        candidate_payment_date = date.fromisoformat(candidate["payment_date"])
        candidate_payment_month = date(candidate_payment_date.year, candidate_payment_date.month, 1)
        if (
            candidate["employee_name"] == row["employee_name"]
            and candidate_payment_month == payment_month
            and candidate.get("issue_status") == "reissued"
        ):
            reissued_rows.append(candidate)
    reissued_rows.sort(key=lambda candidate: (str(candidate.get("calculated_at") or ""), int(candidate["id"])))
    for index, candidate in enumerate(reissued_rows, start=1):
        if int(candidate["id"]) == int(row["id"]):
            return index
    return len(reissued_rows)


def issuance_csv_download_label(rows: list[dict], row: dict) -> str:
    result = result_from_history_row(row)
    calculated_at = row.get("calculated_at")
    if row.get("issue_status") == "reissued":
        status_text = f"修正再発行{reissue_number_for_history(rows, row)}回目"
        date_text = f"最終再発行：{format_history_datetime(str(calculated_at))}" if calculated_at else ""
    else:
        status_text = "発行済"
        date_text = f"初回発行：{format_history_datetime(str(calculated_at))}" if calculated_at else ""
    parts = [result_payment_month_label(result), result.employee_name, status_text]
    if date_text:
        parts.append(date_text)
    parts.append(f"履歴ID：{row['id']}")
    return " / ".join(parts)


def remember_generated_payslip_downloads(history_id: int, pdf_path: Path, csv_path: Path) -> None:
    st.session_state["last_payslip_download"] = {
        "history_id": int(history_id),
        "pdf_path": str(pdf_path),
        "csv_path": str(csv_path),
    }


def render_payslip_download_buttons(selected_history_id: int) -> None:
    download_info = st.session_state.get("last_payslip_download")
    if not download_info or int(download_info.get("history_id", 0)) != int(selected_history_id):
        return

    pdf_path = Path(str(download_info.get("pdf_path", "")))
    csv_path = Path(str(download_info.get("csv_path", "")))
    if not pdf_path.exists() or not csv_path.exists():
        st.warning("直近作成したPDFまたはCSVが見つかりません。もう一度作成してください。")
        return

    st.success("給与明細PDFを作成しました。下のボタンからダウンロードできます。")
    st.caption(f"PDF保存先：{pdf_path}")
    st.caption(f"CSV保存先：{csv_path}")
    st.download_button(
        label="PDFをダウンロード",
        data=pdf_path.read_bytes(),
        file_name=pdf_path.name,
        mime="application/pdf",
        key=f"download_payslip_{selected_history_id}_{pdf_path.name}",
    )
    st.download_button(
        label="CSVをダウンロード",
        data=csv_path.read_bytes(),
        file_name=csv_path.name,
        mime="text/csv",
        key=f"download_payslip_csv_{selected_history_id}_{csv_path.name}",
    )


def payment_month_from_history_row(row: dict) -> date:
    payment_date = date.fromisoformat(str(row["payment_date"]))
    return date(payment_date.year, payment_date.month, 1)


def latest_history_rows_for_payment_month(
    rows: list[dict],
    target_month: date,
    employees: dict,
) -> list[dict]:
    latest_by_employee: dict[str, dict] = {}
    for row in rows:
        if payment_month_from_history_row(row) != target_month:
            continue
        employee_name = str(row["employee_name"])
        current = latest_by_employee.get(employee_name)
        row_key = (str(row.get("calculated_at") or ""), int(row["id"]))
        current_key = (
            str(current.get("calculated_at") or ""),
            int(current["id"]),
        ) if current else ("", -1)
        if current is None or row_key > current_key:
            latest_by_employee[employee_name] = row
    return sorted(
        latest_by_employee.values(),
        key=lambda row: employee_code_for_name(str(row["employee_name"]), employees),
    )


def history_label(row: dict) -> str:
    result = result_from_history_row(row)
    return f"履歴ID {row['id']} / {row['calculated_at']} / {result.employee_name} / {result_payment_month_label(result)}"


def history_summary(rows: list[dict]) -> list[dict]:
    summaries = []
    for row in rows:
        result = result_from_history_row(row)
        summaries.append(
            {
                "履歴ID": row["id"],
                "保存日時": row["calculated_at"],
                "社員名": result.employee_name,
                "支給年月": result_payment_month_label(result),
                "締年月": result.payroll_month_label,
                "支給日": result.payment_date_label,
                "勤務日数": days(result.work_days),
                "扶養人数": f"{result.dependents}名",
                "総支給額": yen(result.gross_pay),
                "控除合計": yen(result.total_deductions),
                "差引支給額": yen(result.net_pay),
                "発行状態": issue_status_label(row.get("issue_status")),
                "備考": result.note,
            }
        )
    return summaries


def history_detail(result: PayrollResult) -> list[dict]:
    return [
        {"項目": "支給年月", "内容": result_payment_month_label(result)},
        {"項目": "締年月", "内容": result.payroll_month_label},
        {"項目": "支給日", "内容": result.payment_date_label},
        {"項目": "勤務日数", "内容": days(result.work_days)},
        {"項目": "扶養人数", "内容": f"{result.dependents}名"},
        {"項目": "基本給", "内容": yen(result.basic_salary)},
        {"項目": "役員報酬", "内容": yen(result.executive_compensation)},
        {"項目": "現場手当", "内容": yen(result.site_allowance)},
        {"項目": "皆勤手当", "内容": yen(result.attendance_allowance)},
        {"項目": allowance_label("休日出勤", result.holiday_work_days), "内容": yen(result.holiday_work_allowance)},
        {"項目": allowance_label("夜間手当", result.night_work_days), "内容": yen(result.night_allowance)},
        {"項目": allowance_label("半徹手当", result.half_night_work_days), "内容": yen(result.half_night_allowance)},
        {"項目": "健保系控除", "内容": health_deduct_label(result.health_insurance_deduct_enabled)},
        {"項目": "健康保険料", "内容": yen(result.health_insurance)},
        {"項目": "介護保険料", "内容": yen(result.care_insurance)},
        {"項目": "厚生年金保険料", "内容": yen(result.pension_insurance)},
        {"項目": "子ども・子育て支援金", "内容": yen(result.child_support)},
        {"項目": "雇用保険料", "内容": yen(result.employment_insurance)},
        {"項目": "社会保険料等合計", "内容": yen(result.social_insurance_total)},
        {"項目": "源泉税判定額", "内容": yen(result.withholding_tax_base)},
        {"項目": "源泉所得税", "内容": yen(result.withholding_income_tax)},
        {"項目": "住民税対象年度", "内容": result.resident_tax_year},
        {"項目": "住民税対象月", "内容": result.resident_tax_month},
        {"項目": "住民税特別徴収額", "内容": yen(result.resident_tax)},
        {"項目": "その他控除", "内容": yen(result.other_deduction)},
        {"項目": "控除合計", "内容": yen(result.total_deductions)},
        {"項目": "差引支給額", "内容": yen(result.net_pay)},
        {"項目": "備考", "内容": result.note},
    ]


def remember_payroll_result(result, payroll_input: PayrollInput) -> None:
    st.session_state["current_payroll_result"] = result
    st.session_state["current_payroll_inputs"] = {
        "employee_name": payroll_input.employee_name,
        "payroll_month": payroll_input.payroll_month.isoformat(),
        "payment_date": result.payment_date.isoformat(),
        "work_days": result.work_days,
        "basic_salary": result.basic_salary,
        "executive_compensation": result.executive_compensation,
        "site_allowance": result.site_allowance,
        "attendance_allowance": result.attendance_allowance,
        "holiday_work_allowance": result.holiday_work_allowance,
        "night_allowance": result.night_allowance,
        "half_night_allowance": result.half_night_allowance,
        "holiday_work_days": result.holiday_work_days,
        "night_work_days": result.night_work_days,
        "half_night_work_days": result.half_night_work_days,
        "gross_pay": result.gross_pay,
        "health_insurance_deduct_enabled": result.health_insurance_deduct_enabled,
        "health_insurance": result.health_insurance,
        "care_insurance": result.care_insurance,
        "pension_insurance": result.pension_insurance,
        "child_support": result.child_support,
        "employment_insurance": result.employment_insurance,
        "social_insurance_total": result.social_insurance_total,
        "withholding_tax_base": result.withholding_tax_base,
        "dependents": result.dependents,
        "withholding_income_tax": result.withholding_income_tax,
        "resident_tax_year": result.resident_tax_year,
        "resident_tax_month": result.resident_tax_month,
        "resident_tax": result.resident_tax,
        "other_deduction": result.other_deduction,
        "total_deductions": result.total_deductions,
        "net_pay": result.net_pay,
        "note": result.note,
    }
    st.session_state["last_result"] = result


def load_history_into_payroll_form(row: dict) -> None:
    result = result_from_history_row(row)
    st.session_state["payroll_employee_name"] = result.employee_name
    st.session_state["payroll_month_label"] = result_payment_month_label(result)
    set_text_input_default("work_days_raw", result.work_days)
    set_text_input_default("site_allowance_raw", result.site_allowance)
    set_text_input_default("attendance_allowance_raw", result.attendance_allowance)
    set_text_input_default("holiday_work_allowance_raw", result.holiday_work_allowance)
    set_text_input_default("night_allowance_raw", result.night_allowance)
    set_text_input_default("half_night_allowance_raw", result.half_night_allowance)
    set_text_input_default("holiday_work_days_raw", result.holiday_work_days)
    set_text_input_default("night_work_days_raw", result.night_work_days)
    set_text_input_default("half_night_work_days_raw", result.half_night_work_days)
    set_text_input_default("other_deduction_raw", result.other_deduction)
    st.session_state["payroll_note"] = result.note
    st.session_state["health_insurance_deduct_choice"] = health_deduct_label(result.health_insurance_deduct_enabled)
    st.session_state["reissue_source_id"] = int(row["id"])
    st.session_state.pop("current_payroll_result", None)


def result_action_buttons(result) -> None:
    st.markdown("### 計算後の操作")
    st.info("給与明細PDF・管理CSVは、計算履歴へ保存した後、左メニューの「給与明細・CSV発行」から発行してください。")
    if st.button("計算履歴へ保存", key="save_current_payroll_result"):
        try:
            reissue_source_id = st.session_state.get("reissue_source_id")
            issue_status = "reissued" if reissue_source_id else "issued"
            history_id = save_payroll_result(
                result,
                issue_status=issue_status,
                reissue_source_id=reissue_source_id,
            )
            st.session_state["last_history_id"] = history_id
            st.session_state.pop("reissue_source_id", None)
            st.success(f"計算履歴へ保存しました。計算ID: {history_id}")
        except Exception as exc:  # pragma: no cover - UI safety net
            st.error(f"計算履歴への保存に失敗しました。エラー内容：{exc}")


def guide_page() -> None:
    st.markdown(
        "<h1 style='line-height:1.35; margin-bottom:0.25rem;'>"
        "Local-First Payroll & Resident Tax Automation"
        "</h1>",
        unsafe_allow_html=True,
    )
    st.caption("OpenAI Build Week - Fictional Local Demo")
    st.subheader("利用説明書")
    st.info("このアプリは、毎月の給与控除計算と給与明細PDF作成を、かんたんに行うための専用アプリです。")

    st.subheader("1. このアプリでできること")
    col1, col2 = st.columns(2)
    with col1:
        st.markdown(
            """
            - 給与を入力できます
            - 社会保険料などの控除額を確認できます
            - 住民税の特別徴収額を登録できます
            """
        )
    with col2:
        st.markdown(
            """
            - 計算した給与データを履歴として保存できます
            - 保存した履歴から給与明細PDFを作成できます
            - 架空従業員2名の給与明細を作成できます
            """
        )
    st.success("給与入力、控除額確認、PDF作成、履歴確認まで、このアプリで行えます。")

    st.subheader("2. 毎月の基本的な使い方")
    st.markdown(
        """
        1. 住民税の金額を確認する
        2. 必要があれば、住民税特別徴収額を登録する
        3. 「給与計算入力」で社員を選ぶ
        4. 支給年月、締日、支給日を確認する
        5. 勤務日数や手当、控除を入力する
        6. 「計算する」を押す
        7. 計算結果を確認する
        8. 「計算履歴へ保存」を押す
        9. 「給与明細・CSV発行」へ進む
        10. 保存した履歴を選んでPDFを作る
        """
    )

    st.markdown(
        """
        **流れ**

        住民税登録  
        ↓  
        給与計算入力  
        ↓  
        計算結果確認  
        ↓  
        計算履歴へ保存  
        ↓  
        給与明細・CSV発行
        """
    )

    with st.expander("3. 住民税を登録する方法", expanded=True):
        st.markdown(
            """
            住民税は自動計算しません。  
            市区町村から届いた「特別徴収税額通知書」に書かれている金額を、そのまま入力します。

            登録するときに見るもの：
            - 社員名
            - 年度
            - 6月分から翌年5月分までの各月金額

            住民税の月額と徴収区分は別の設定です。月額が登録されていても、普通徴収中は給与から控除されず、住民税納付一覧表にも掲載されません。特別徴収へ切り替わった場合は、対象支給月の登録月額を給与から控除します。徴収区分の変更が必要な場合は管理者へ連絡してください。
            """
        )
        st.warning("住民税は「支給日ベース」で使います。")
        st.markdown(
            """
            例：令和8年6月分給与を、令和8年7月10日に支給する場合  
            → 住民税は「令和8年度 7月分」を使います。
            """
        )

    with st.expander("4. 給与計算をする方法", expanded=True):
        st.markdown(
            """
            「給与計算入力」画面で行うこと：

            1. 社員を選びます
            2. 支給年月を選びます
            3. 支給日を確認します
            4. 勤務日数を入力します
            5. 手当や控除を入力します
            6. 「計算する」を押します

            役員の場合：
            - 役員報酬は固定です
            - 手当欄は表示されません
            - 雇用保険はありません

            社員の場合：
            - 基本給は固定です
            - 現場手当、皆勤手当、休日出勤、夜間手当、半徹手当を入力できます
            - 雇用保険があります
            """
        )
        st.info("勤務日数は給与計算には使いません。給与明細に表示するための項目です。")

    with st.expander("5. 入力するときの注意点", expanded=True):
        st.markdown(
            """
            金額欄は、空欄のままでも大丈夫です。  
            空欄の場合は、0円として計算されます。

            入力例：
            - 空欄 → 0円
            - 10000 → 10,000円
            - 10,000 → 10,000円

            入力してはいけない例：
            - -1000 → マイナスなので不可
            - abc → 数字ではないので不可

            勤務日数も、空欄の場合は0日として扱います。  
            勤務日数に小数は入れないでください。
            """
        )

    with st.expander("6. 給与明細PDFを作る方法", expanded=True):
        st.markdown(
            """
            給与明細PDFは、給与計算入力画面では作りません。

            まず給与計算をして、「計算履歴へ保存」を押してください。  
            そのあと、左メニューの「給与明細・CSV発行」を開きます。

            PDF作成の流れ：
            1. 「給与明細・CSV発行」を開く
            2. 作成したい計算履歴を選ぶ
            3. 「給与明細PDFを作成」ボタンを押す
            4. ダウンロードボタンからPDFを保存する
            """
        )
        st.warning("PDFは、保存済みの計算履歴から作成します。保存していない計算結果はPDFにできません。")

    with st.expander("7. 計算履歴を見る方法"):
        st.markdown(
            """
            「計算履歴」では、過去に保存した給与計算を確認できます。

            確認できる内容：
            - 社員名
            - 支給年月
            - 支給日
            - 勤務日数
            - 総支給額
            - 控除合計
            - 差引支給額

            過去の明細をもう一度PDFにしたいときは、  
            「給与明細・CSV発行」から該当する履歴IDを選んでください。
            """
        )

    st.subheader("8. よくある間違い")
    st.warning(
        """
        1. 計算結果を保存せずにPDFを作ろうとする  
        → 先に「計算履歴へ保存」を押してください。

        2. 住民税を登録していない  
        → 住民税は自動計算されません。先に金額を登録してください。

        3. 支給年月、締日、支給日を間違える  
        → 社会保険料は締月ベース、住民税と所得税は支給日ベースです。

        4. 金額欄に文字を入れる  
        → 金額欄は数字だけを入力してください。

        5. 役員の手当を入力しようとする  
        → 役員報酬固定のため、手当欄は表示されません。
        """
    )

    st.subheader("9. 大切な計算ルール")
    st.markdown(
        """
        - 社会保険料は「締月」で判定します
        - 所得税は「支給日」で判定します
        - 住民税も「支給日」で判定します
        - 住民税は自動計算ではなく、登録した金額を使います
        - 勤務日数は計算には使わず、明細に表示するだけです
        """
    )
    st.info(
        """
        例：令和8年6月分給与、支給日：令和8年7月10日

        - 社会保険料 → 令和8年6月分として計算
        - 住民税 → 令和8年度7月分を使用
        - 所得税 → 令和8年7月10日の支給として計算
        """
    )

    st.subheader("10. 困ったときの確認ポイント")
    st.markdown(
        """
        - 住民税は登録されていますか？
        - 給与計算後に「計算履歴へ保存」を押しましたか？
        - PDF出力画面で正しい履歴を選んでいますか？
        - 支給年月、締日、支給日は合っていますか？
        - 金額欄に文字やマイナスを入れていませんか？
        - アプリが止まった場合は、ブラウザを閉じてデスクトップショートカットから起動し直してください
        """
    )

    st.subheader("起動・終了の説明")
    st.markdown("起動方法：デスクトップショートカットを実行します。")
    st.markdown("ブラウザで以下が開きます。")
    st.code("http://127.0.0.1:8520/")
    st.markdown("終了方法：左メニュー下部の「アプリを終了する」を押します。")
    st.warning("右上の Deploy ボタンは使いません。")

    st.subheader("お問い合わせ・運営情報")
    st.markdown(
        """
        運営・作成：  
        OpenAI Build Week Demo  
        Local-only fictional application

        対象会社：  
        株式会社サンプルワークス（架空）
        """
    )
    st.caption("このデモは架空データ専用です。制度改正や料率変更があった場合は、実運用前に専門家による確認が必要です。")


def guide_page() -> None:
    st.markdown(
        "<h1 style='line-height:1.35; margin-bottom:0.25rem;'>"
        "Local-First Payroll & Resident Tax Automation"
        "</h1>",
        unsafe_allow_html=True,
    )
    st.caption("OpenAI Build Week - Fictional Local Demo")
    st.subheader("アプリ利用説明書")
    st.info("このアプリでは、給与控除の計算、計算履歴の確認、給与明細PDF・給与関連帳票と管理用CSVの作成ができます。")

    st.subheader("1. 起動と終了")
    st.markdown(
        """
        - 通常は、デスクトップショートカットをダブルクリックして起動します。
        - 起動中はアプリの実行ウィンドウが開きます。終了まで閉じないでください。
        - 起動するときは、デスクトップショートカットをダブルクリックします。
        - ブラウザで自動的にアプリが開きます。通常、URLを手入力する必要はありません。
        - 起動URLは `http://127.0.0.1:8520/` です。
        - 終了するときは、左メニュー下部の「アプリを終了する」を押します。
        - 確認メッセージで「はい、終了する」を押した後、終了画面が表示されたらブラウザ画面を閉じてください。
        """
    )
    st.caption("万一、画面上の終了ボタンで終了できない場合は、起動中の画面を閉じるか、管理者へ確認してください。")

    st.subheader("2. 給与計算入力")
    st.markdown(
        """
        1. 左メニューの「給与計算入力」を開きます。
        2. 対象社員と支給年月を選びます。
           例：1月末締め・2月10日支給分は「令和8年2月分」として選びます。
        3. 必要な金額、日数、その他控除、備考を入力します。
        4. 「健保系控除」を選びます。通常は「控除する」です。
        5. 「計算する」を押し、結果を確認します。
        6. 問題なければ「計算履歴へ保存」を押します。
        """
    )

    with st.expander("健保系控除あり／なし", expanded=True):
        st.markdown(
            """
            - 通常は「控除する」を選びます。
            - 建設国保などで健康保険料を給与から控除しない月は「控除しない」を選びます。
            - 「控除しない」の場合、健康保険料、介護保険料、子ども・子育て支援金が0円になります。
            - 厚生年金保険料は通常どおり控除されます。
            - 雇用保険料も対象者の設定どおり控除されます。
            - 源泉所得税は、社会保険料等を差し引いた後の金額で再計算されます。
            """
        )

    with st.expander("役員の入力ルール", expanded=True):
        st.markdown(
            """
            - デモの役員報酬は320,000円固定です。
            - 雇用保険は控除しません。
            - 現場手当、皆勤手当、休日出勤、夜間手当、半徹手当は入力しません。
            - 勤務日数は、締月の暦日数で自動表示されます。
            - 例：支給年月が令和8年2月の場合、締月は令和8年1月のため31日です。
            - 勤務日数は給与計算には使わず、明細表示用です。
            - 扶養人数は支給日基準で自動判定されます。
            - デモの扶養人数は1名です。
            - 扶養人数は源泉所得税の計算に使われ、計算結果と給与明細PDFにも表示されます。
            """
        )

    with st.expander("社員の入力ルール", expanded=True):
        st.markdown(
            """
            - デモの基本給は210,000円固定です。
            - 雇用保険あり、扶養人数0名です。
            - 勤務日数は手入力します。給与計算には使わず、明細表示用です。
            - このデモでは基本給のみを使用します。
            - 休日出勤は、金額の次に休日出勤日数を入力します。
            - 夜間手当は、金額の次に夜間手当日数を入力します。
            - 半徹手当は、金額の次に半徹手当日数を入力します。
            """
        )
        st.markdown(
            """
            例：休日出勤が1日あり、手当が5,000円の場合  
            休日出勤に `5,000`、休日出勤日数に `1` と入力します。  
            PDFでは `休日出勤（1日）` のように表示されます。
            """
        )

    st.subheader("3. 控除と備考")
    st.markdown(
        """
        控除として表示される主な項目は、健康保険料、介護保険料、子ども・子育て支援金、厚生年金保険料、雇用保険料、源泉所得税、住民税特別徴収額、その他控除です。

        架空従業員2名は令和8年度の特別徴収対象です。「住民税通知書取込・確認」で元資料を目視確認し、確認済みとして確定した月額だけを給与計算へ反映します。確定通知書がない場合や通知書額を変更する場合は、警告と理由入力が必要です。

        通知書を取り込むと、今回作成された確認データへ自動的に切り替わります。自動照合済みの従業員と自動判定済みの帳票種類は固定表示され、訂正が必要な場合だけ訂正操作と理由入力を使用します。

        読取警告がない場合は、元資料と12か月分を照合した確認チェック1つだけで確定操作へ進めます。例外確定チェックと理由欄は、確定を妨げる警告がある場合だけ表示されます。

        備考欄は自由に入力できます。2行、3行に分けて入力した内容は、PDFにも改行された状態で表示されます。長すぎる文章は帳票内で見づらくなるため、短めに入力してください。
        """
    )

    st.subheader("4. 計算履歴と修正再発行")
    st.markdown(
        """
        - 計算した内容は、計算履歴へ保存できます。
        - 過去に作った明細を確認できます。
        - 選択した履歴IDを「給与明細・CSV発行」へ引き継げます。計算履歴から直接ダウンロードはしません。
        - 不要な履歴はチェックを入れて削除できます。
        - 削除時は確認メッセージが出て、「はい、削除する」を押した場合だけ削除されます。
        - 削除した履歴は元に戻せません。
        - 「修正して再発行」を押すと、履歴の内容を給与計算入力画面へ読み込めます。
        - 修正後に保存すると、新しい履歴として保存され、元の履歴は残ります。
        - 修正後の履歴は「修正再発行」として扱われます。
        - PDFやCSVの作成・再発行・ダウンロードは「給与明細・CSV発行」だけで行います。
        """
    )

    st.subheader("5. 発行状況確認")
    st.markdown(
        """
        - 支給年月ごと、対象者ごとに発行状況を確認できます。
        - ステータスは、未発行、発行済、修正再発行1回目、修正再発行2回目などです。
        - 修正再発行がある場合は、直近の再発行日時も表示されます。
        - どの月の明細を作ったか、作り忘れがないか確認できます。
        - 発行状況確認は確認専用です。対象履歴IDを「給与明細・CSV発行」へ引き継げます。
        - 履歴IDとは、保存された給与計算1件ごとに付く番号です。同じ支給年月・同じ対象者の履歴が複数ある場合でも、履歴IDで区別できます。
        """
    )

    st.subheader("6. 給与明細・CSV発行")
    st.markdown(
        """
        - 正式給与のPDF・CSVは「給与明細・CSV発行」だけから作成します。
        - 保存済み履歴IDの確定値を使用し、発行時に給与を再計算しません。
        - PDF作成後は、PDFだけでなくCSVもダウンロードできます。
        - PDF内の「○月分 給与」は支給年月で表示されます。
        - PDF/CSVファイル名は支給年月を先頭にし、締年月と支給年月の両方が表示されます。
        - デモ版PDFには顧客固有のロゴや印影を表示しません。
        - PDFには、源泉所得税の計算に使った扶養人数も表示されます。
        - 備考欄の改行もPDFに反映されます。
        - 休日出勤、夜間手当、半徹手当は、日数を入力した場合に `休日出勤（1日）`、`夜間手当（2日）`、`半徹手当（1日）` のように表示されます。
        - PDFダウンロード時にブラウザの確認メッセージが出ることがあります。本アプリはPC内で動作するローカルアプリです。表示された場合は内容を確認して保存してください。不明な場合は管理者へ確認してください。
        """
    )
    st.markdown(
        """
        CSVは、管理用アプリへの取り込みを想定した「標準CSV Ver.1.0」です。

        CSVには、支給年月、締日、支給日、従業員名、扶養人数、勤務日数、支給額、控除額、差引支給額、PDFファイル名、取込用キーなどが固定の列順で出力されます。

        取込用キーは、同じ給与明細を重複して取り込まないための管理用文字列です。会社コード、支給日、従業員コードを組み合わせて作成されます。

        CSVはExcelで開ける形式です。Excelで直接開いた場合、CSV形式バージョン `1.0` が `1` と表示されたり、日付が `2026/4/10` のように表示されたりすることがあります。ただし、CSVの生データ自体は所定形式で保存されています。
        """
    )

    st.subheader("7. 給与関連一括帳票")
    st.markdown(
        """
        - 左メニューの「給与明細・CSV発行」を開き、「給与関連一括帳票」タブを選びます。
        - 帳票は入力途中の値ではなく、計算履歴へ保存済みの結果から作成します。先に各社員の給与計算を行い、「計算履歴へ保存」を押してください。
        - 支給年月を選び、「給与明細」「給与支給・控除一覧表」「事業所負担保険料一覧表」「住民税納付一覧表」から必要なものへチェックを付けます。1種類だけでも複数種類でも発行できます。
        - 「すべて選択」をオンにすると4種類をまとめて選択でき、オフにすると4種類すべてを解除できます。必要な帳票だけを個別に選択することもできます。
        - 給与支給・控除一覧表は指定見本の5社員枠で出力し、社員は従業員コード昇順に表示します。5名未満の未使用枠と0円項目は空欄です。右上には締日・支給日を表示し、帳票上に作成日は表示しません。
        - 事業所負担保険料一覧表は指定見本形式で、社員を従業員コード昇順に1つの明細領域へ表示します。社員間に横罫線は入れず、0円項目は空欄です。厚生年金基金列は将来用として残し、白地の全社計行に人数と保険料合計を表示します。右上には実際の作成日とページ番号を表示します。
        - 給与明細は「個別発行」または「全員一括発行」を選べます。全員一括発行でも、社員ごとに別々のPDFが作成されます。
        - 発行に成功したPDFは個別にダウンロードできます。成功したPDFだけをまとめたZIPもダウンロードできます。
        - 住民税納付一覧表は、特別徴収かつ住民税額がある社員だけが対象です。対象者がいない場合はPDFを作成せず、他に選択した帳票の発行は続けます。
        - エラーが表示された場合は、対象月の計算履歴が保存されているか、保存先へ書き込めるかを確認して再実行してください。事業所負担保険料の設定不足と表示された場合は、管理者へ連絡してください。
        """
    )


def payroll_page(masters: dict) -> None:
    st.header("給与計算 / Payroll Calculation")
    st.info("毎月入力するのは、対象社員、支給年月、各種手当、その他控除、備考です。月額や料率を変更する場合は、設定内容を管理者が確認してください。")

    labels = [label for label, _ in payment_month_options()]
    month_map = dict(payment_month_options())
    employee_names = list(masters["employees"].keys())
    default_employee_index = 0

    employee_name = st.selectbox("対象社員", employee_names, index=default_employee_index, key="payroll_employee_name")
    employee = masters["employees"][employee_name]
    allowance_input_enabled = bool(employee.get("allowance_input_enabled", False))

    default_month_index = labels.index(st.session_state["payroll_month_label"]) if st.session_state.get("payroll_month_label") in labels else 1
    month_label = st.selectbox("支給年月", labels, index=default_month_index, key="payroll_month_label")
    selected_payment_month = month_map[month_label]
    selected_payroll_month = closing_month_from_payment_month(selected_payment_month)
    payment_day = int(masters["company"]["payment_day"])
    selected_payment_date = date(selected_payment_month.year, selected_payment_month.month, payment_day)
    closing_day = monthrange(selected_payroll_month.year, selected_payroll_month.month)[1]
    selected_closing_date = date(selected_payroll_month.year, selected_payroll_month.month, closing_day)
    st.caption(f"締日：{wareki_date(selected_closing_date)} / 支給日：{wareki_date(selected_payment_date)}")
    if get_resident_tax_collection_type(masters["employees"][employee_name], selected_payment_date) is None:
        st.warning(
            "住民税の徴収区分を確認できないため、住民税を給与控除していません。\n"
            "管理者へ設定をご確認ください。"
        )
    collection_type = get_resident_tax_collection_type(employee, selected_payment_date)
    employee_code = str(employee.get("employee_id", "")).zfill(6)
    confirmed_notice = (
        get_confirmed_resident_tax(employee_code, selected_payment_date)
        if collection_type == "特別徴収"
        else None
    )
    if collection_type == "特別徴収":
        st.markdown("**徴収区分：特別徴収**")
        if confirmed_notice:
            st.success(
                f"確認済み通知書：{confirmed_notice['municipality']} / "
                f"{confirmed_notice['fiscal_year']} {confirmed_notice['target_month']} / "
                f"月額 {yen(int(confirmed_notice['confirmed_amount']))} / "
                f"確定 {format_history_datetime(confirmed_notice['confirmed_at'])}"
            )
            if int(confirmed_notice.get("manual_corrected") or 0):
                st.info("この通知書には目視確認時の手動修正があります。")
        else:
            st.warning(
                "特別徴収対象ですが、この支給月の確定済み住民税額がありません。"
                "通知書を取り込むか、月額を手動入力してください。"
            )

    with st.form("payroll_form"):
        if employee.get("role") != "役員":
            work_days_raw = blank_int_input(
                st,
                "勤務日数",
                key="work_days_raw",
                help="締月の勤務日数を入力してください。給与明細と計算履歴に表示されます。計算金額には影響しません。",
            )
            st.caption("勤務日数の単位：日。給与明細に表示されますが、給与額・控除額の自動計算には使用しません。")
        else:
            auto_work_days = monthrange(selected_payroll_month.year, selected_payroll_month.month)[1]
            st.text_input("勤務日数", value=str(auto_work_days), disabled=True)
            st.caption("役員のため、勤務日数は締月の暦日数を自動表示します。")
            work_days_raw = str(auto_work_days)
        health_insurance_deduct_choice = st.selectbox(
            "健保系控除",
            ["控除する", "控除しない"],
            index=0,
            key="health_insurance_deduct_choice",
        )
        st.caption("「控除しない」を選ぶと、健康保険料・介護保険料・子ども子育て支援金を0円にします。厚生年金保険料は通常どおり控除します。")
        if allowance_input_enabled:
            st.caption(f"{employee_name}の給与は、基本給に入力した各種手当を加算して計算します。")
            a1, a2 = st.columns(2)
            site_allowance_raw = blank_int_input(a1, "現場手当", key="site_allowance_raw")
            attendance_allowance_raw = blank_int_input(a2, "皆勤手当", key="attendance_allowance_raw")
            h1, h2 = st.columns(2)
            holiday_work_allowance_raw = blank_int_input(h1, "休日出勤", key="holiday_work_allowance_raw")
            holiday_work_days_raw = blank_int_input(h2, "休日出勤日数", key="holiday_work_days_raw")
            n1, n2 = st.columns(2)
            night_allowance_raw = blank_int_input(n1, "夜間手当", key="night_allowance_raw")
            night_work_days_raw = blank_int_input(n2, "夜間手当日数", key="night_work_days_raw")
            hn1, hn2 = st.columns(2)
            half_night_allowance_raw = blank_int_input(hn1, "半徹手当", key="half_night_allowance_raw")
            half_night_work_days_raw = blank_int_input(hn2, "半徹手当日数", key="half_night_work_days_raw")
        else:
            fixed_label = "役員報酬" if employee.get("role") == "役員" else "基本給"
            fixed_amount = int(employee.get("executive_compensation") or employee.get("basic_salary") or 0)
            st.caption(f"{employee_name}は{fixed_label}{fixed_amount:,}円固定です。追加手当は0円として扱います。")
            site_allowance_raw = ""
            attendance_allowance_raw = ""
            holiday_work_allowance_raw = ""
            night_allowance_raw = ""
            half_night_allowance_raw = ""
            holiday_work_days_raw = ""
            night_work_days_raw = ""
            half_night_work_days_raw = ""
        other_deduction_raw = blank_int_input(st, "その他控除", key="other_deduction_raw")
        if collection_type == "特別徴収":
            resident_default = int(confirmed_notice["confirmed_amount"]) if confirmed_notice else 0
            resident_tax_used_raw = st.text_input(
                "今回使用する住民税特別徴収額",
                value=str(resident_default),
                key=f"resident_tax_used_{employee_code}_{selected_payment_month.isoformat()}",
            )
            resident_tax_override_reason = st.text_input(
                "通知書額から変更する場合、または通知書なしで入力する場合の理由",
                key=f"resident_tax_reason_{employee_code}_{selected_payment_month.isoformat()}",
            )
        else:
            resident_tax_used_raw = "0"
            resident_tax_override_reason = ""
        note = st.text_area("備考", key="payroll_note")
        submitted = st.form_submit_button("計算する")

    if submitted:
        parsed, errors = parse_inputs(
            [
                ("work_days", "勤務日数", work_days_raw),
                ("site_allowance", "現場手当", site_allowance_raw),
                ("attendance_allowance", "皆勤手当", attendance_allowance_raw),
                ("holiday_work_allowance", "休日出勤", holiday_work_allowance_raw),
                ("night_allowance", "夜間手当", night_allowance_raw),
                ("half_night_allowance", "半徹手当", half_night_allowance_raw),
                ("holiday_work_days", "休日出勤 日数", holiday_work_days_raw),
                ("night_work_days", "夜間手当 日数", night_work_days_raw),
                ("half_night_work_days", "半徹手当 日数", half_night_work_days_raw),
                ("other_deduction", "その他控除", other_deduction_raw),
                ("resident_tax_used", "住民税特別徴収額", resident_tax_used_raw),
            ]
        )
        original_resident_tax = int(confirmed_notice["confirmed_amount"]) if confirmed_notice else None
        if collection_type == "特別徴収":
            if confirmed_notice is None and not resident_tax_override_reason.strip():
                errors.append("確定済み通知書がないため、住民税を手動入力して計算する理由を入力してください。")
            elif confirmed_notice is not None and parsed["resident_tax_used"] != original_resident_tax and not resident_tax_override_reason.strip():
                errors.append("通知書月額から変更する理由を入力してください。")
        if errors:
            for error in errors:
                st.error(error)
        else:
            payroll_input = PayrollInput(
                employee_name=employee_name,
                payroll_month=selected_payroll_month,
                work_days=parsed["work_days"],
                site_allowance=parsed["site_allowance"],
                attendance_allowance=parsed["attendance_allowance"],
                holiday_work_allowance=parsed["holiday_work_allowance"],
                night_allowance=parsed["night_allowance"],
                half_night_allowance=parsed["half_night_allowance"],
                holiday_work_days=parsed["holiday_work_days"],
                night_work_days=parsed["night_work_days"],
                half_night_work_days=parsed["half_night_work_days"],
                other_deduction=parsed["other_deduction"],
                health_insurance_deduct_enabled=1 if health_insurance_deduct_choice == "控除する" else 0,
                note=note,
                resident_tax_notice_id=int(confirmed_notice["id"]) if confirmed_notice else None,
                resident_tax_original_amount=original_resident_tax,
                resident_tax_used_amount=parsed["resident_tax_used"] if collection_type == "特別徴収" else 0,
                resident_tax_override_reason=resident_tax_override_reason.strip(),
                resident_tax_municipality=str(confirmed_notice["municipality"]) if confirmed_notice else "",
                resident_tax_notice_confirmed_at=str(confirmed_notice["confirmed_at"]) if confirmed_notice else "",
                resident_tax_notice_manual_corrected=int(confirmed_notice["manual_corrected"] or 0) if confirmed_notice else 0,
            )
            result = calculate_payroll(payroll_input, masters)
            remember_payroll_result(result, payroll_input)

    current_result = st.session_state.get("current_payroll_result")
    if current_result:
        result_cards(current_result)
        result_action_buttons(current_result)
    else:
        st.info("入力後、「計算する」を押すと、計算結果と「計算履歴へ保存」ボタンが表示されます。\nPDF・CSVは保存後に「給与明細・CSV発行」から発行してください。")


def resident_tax_page(masters: dict) -> None:
    st.header("住民税特別徴収額入力・確認")
    st.write("住民税は市区町村から届く通知書の月割額を登録します。特別徴収の場合に、支給日の月の登録額を控除します。")

    master = masters["resident_tax"]
    df = pd.DataFrame(master["rows"])
    st.dataframe(df, hide_index=True, use_container_width=True)

    employee_name = st.selectbox(
        "社員名", list(masters["employees"].keys()), key="resident_tax_employee_name"
    )
    collection_type = get_resident_tax_collection_type(masters["employees"][employee_name])
    if collection_type == "普通徴収":
        st.markdown("**徴収区分：普通徴収**")
        st.info("普通徴収のため、登録されている月額は給与計算および住民税納付一覧表には使用されません。")
    elif collection_type == "特別徴収":
        st.markdown("**徴収区分：特別徴収**")
        st.info("対象支給月の登録月額を給与から控除します。")
    else:
        st.markdown("**徴収区分：確認できません**")
        st.warning(
            "住民税の徴収区分を確認できないため、住民税を給与控除していません。\n"
            "管理者へ設定をご確認ください。"
        )

    with st.form("resident_form"):
        col1, col2, col3 = st.columns(3)
        fiscal_year = col1.selectbox("年度", ["令和8年度", "令和9年度", "令和10年度"])
        target_month = col2.selectbox("対象月", RESIDENT_TAX_MONTHS)
        amount_raw = blank_int_input(col3, "月割額", key="resident_tax_amount_raw")
        submitted = st.form_submit_button("住民税マスターへ保存")
    if submitted:
        amount, error = parse_non_negative_int(f"住民税{target_month}", amount_raw)
        if error:
            st.error(error)
        else:
            update_resident_tax_amount(master, employee_name, fiscal_year, target_month, amount)
            save_json("resident_tax_master.json", master)
            st.success("住民税マスターを保存しました。画面を再読み込みすると一覧に反映されます。")


NOTICE_FORM_STATE_PREFIXES = (
    "notice_year",
    "notice_municipality",
    "notice_annual",
    "notice_month_editor",
    "notice_notes",
    "notice_ack",
    "notice_all_months_checked",
    "notice_override_warning",
    "notice_correction_reason",
    "notice_confirmation_checked",
    "notice_exception_confirm",
    "notice_exception_reason",
    "notice_employee_edit",
    "notice_employee_choice",
    "notice_employee_reason",
    "notice_employee_confirm",
    "notice_type_edit",
    "notice_type_choice",
    "notice_type_reason",
    "notice_type_confirm",
)


def clear_notice_form_state(notice_id: int) -> None:
    """通知書切替時に未保存の入力・確認状態を別通知書へ持ち越さない。"""
    for prefix in NOTICE_FORM_STATE_PREFIXES:
        st.session_state.pop(f"{prefix}_{int(notice_id)}", None)


def select_resident_notice(notice_id: int) -> None:
    st.session_state["resident_tax_selected_notice_id"] = int(notice_id)
    st.session_state.pop("resident_tax_last_rendered_notice_id", None)


def shortened_source_filename(filename: str, limit: int = 44) -> str:
    name = str(filename or "元ファイル名不明")
    if len(name) <= limit:
        return name
    path = Path(name)
    suffix = path.suffix
    keep = max(8, limit - len(suffix) - 1)
    return f"{path.stem[:keep]}…{suffix}"


def pdf_processing_status_labels(pdf_status: dict) -> tuple[str, str, str]:
    security_check = str(pdf_status.get("security_check") or "")
    if security_check in {"text_provider_failed", "text_provider_unavailable"}:
        encrypted_label = "判定保留（画像OCRで処理）"
    elif pdf_status.get("encrypted") and pdf_status.get("auto_unlocked"):
        encrypted_label = "自動解除済み"
    elif pdf_status.get("encrypted") and pdf_status.get("accessible_for_text", pdf_status.get("accessible")):
        encrypted_label = "パスワードで解除済み"
    elif pdf_status.get("encrypted"):
        encrypted_label = "パスワード確認が必要"
    else:
        encrypted_label = "暗号化なし"
    text_label = "抽出成功" if pdf_status.get("text_extraction") == "success" else "抽出不可"
    ocr_label = {
        "success": "成功",
        "failed": "失敗",
        "required": "実行予定",
        "not_used": "未使用",
    }.get(str(pdf_status.get("ocr_fallback")), "未使用")
    return encrypted_label, text_label, ocr_label


def notice_employee_identity(row: dict, auto: dict | None = None) -> tuple[str, str]:
    auto = auto or {}
    name = str(row.get("employee_name") or auto.get("employee_name") or "")
    code = str(row.get("employee_code") or auto.get("employee_code") or "")
    return name, code.zfill(6) if code else ""


def resident_tax_notice_page(masters: dict) -> None:
    st.header("住民税通知書取込・確認 / Resident Tax Notice Import & Review")
    st.info(
        "通知書はローカルPC内だけで処理します。自動読取結果は要確認であり、"
        "「確認済みとして確定反映」を押すまでは給与計算へ反映されません。"
    )
    current_ocr = ocr_status()
    if current_ocr["available"]:
        st.success("日本語OCR：利用可能")
    else:
        st.warning(
            "日本語OCR：利用できません。"
            "画像通知書はプレビューを見ながら手動入力してください。"
            "文字情報を持つPDFは自動抽出できます。"
        )
    with st.expander("OCR診断情報を表示", expanded=False):
        if current_ocr["available"]:
            st.caption(
                f"Tesseract：{current_ocr['source']} / {current_ocr['version'] or 'バージョン不明'} / "
                f"日本語データ：jpn確認済み / 英語データ：eng確認済み / "
                f"簡易OCR：{current_ocr['self_test']}"
            )
        else:
            st.caption(f"利用できない理由：{current_ocr['reason']}")

    uploads = st.file_uploader(
        "通知書を選択（複数可）",
        type=["pdf", "jpg", "jpeg", "png"],
        accept_multiple_files=True,
        key="resident_tax_notice_uploads",
    )
    upload_passwords: dict[str, str | None] = {}
    password_blocked = False
    for upload in uploads or []:
        if Path(upload.name).suffix.lower() != ".pdf":
            continue
        data = upload.getvalue()
        digest = sha256_bytes(data)
        security = inspect_pdf_security(data)
        if not security.get("password_required"):
            upload_passwords[digest] = None
            continue
        password_key = f"resident_pdf_password_{digest[:16]}"
        password = st.text_input(
            f"{upload.name} のPDFパスワード",
            type="password",
            key=password_key,
            help="パスワードはDB・原本情報・ログへ保存しません。",
        )
        upload_passwords[digest] = password or None
        if not password:
            password_blocked = True
            st.info("このPDFは暗号化されています。読み取るにはPDFのパスワードが必要です。")
        else:
            password_status = inspect_pdf_security(data, password)
            if password_status.get("password_required"):
                password_blocked = True
                st.warning("PDFのパスワードが一致しません。再入力してください。")

    if st.button(
        "選択ファイルをローカル取込・自動読取",
        disabled=not uploads or password_blocked,
    ):
        new_notice_ids: list[int] = []
        import_groups: list[dict] = []
        import_errors: list[str] = []
        failed_drafts: list[dict] = []
        for upload in uploads or []:
            stored_path: Path | None = None
            stored_created = False
            result: dict | None = None
            try:
                data = upload.getvalue()
                digest = sha256_bytes(data)
                result = extract_notice(
                    data,
                    upload.name,
                    password=upload_passwords.get(digest),
                )
                major_failures = validate_notice_import(result)
                if major_failures:
                    raise ValueError(
                        "自動読取を完了できなかったため、誤った確認データをDBへ保存しません。"
                        + " ".join(major_failures)
                        + " 原本プレビューを確認し、読取可能なPDFで再試行してください。"
                    )
                stored_path, digest, stored_created = save_source_file(data, upload.name)
                try:
                    source_id, notice_ids = create_resident_tax_source_document(
                        source_filename=upload.name,
                        stored_filename=str(stored_path.relative_to(BASE_DIR)),
                        source_sha256=digest,
                        source_mime=upload.type or "",
                        extracted_notices=result["notices"],
                        raw_text=result["raw_text"],
                        warnings=result["warnings"],
                        confidence=result["confidence"],
                        detected_document_type=result["document_type"],
                        fiscal_year=str(result["fields"].get("fiscal_year") or ""),
                        municipality=str(result["fields"].get("municipality") or ""),
                        page_count=int(result.get("page_count") or 1),
                        ocr_result=result.get("ocr_result") or {},
                    )
                except Exception:
                    if stored_created and stored_path.is_file():
                        stored_path.unlink()
                    raise
                new_notice_ids.extend(int(notice_id) for notice_id in notice_ids)
                import_groups.append(
                    {
                        "source_id": int(source_id),
                        "filename": upload.name,
                        "document_type": str(result.get("document_type") or "unknown"),
                        "notice_ids": [int(notice_id) for notice_id in notice_ids],
                        "pdf_status": (result.get("ocr_result") or {}).get("pdf_security") or {},
                    }
                )
            except PdfPasswordRequiredError:
                import_errors.append(
                    f"{upload.name}: このPDFは暗号化されています。読み取るにはPDFのパスワードが必要です。"
                )
            except ValueError as exc:
                import_errors.append(f"{upload.name}: {exc}")
                if result is not None:
                    failed_drafts.append(
                        {
                            "filename": upload.name,
                            "mime": upload.type or "",
                            "data": data,
                            "result": result,
                            "digest": sha256_bytes(data),
                        }
                    )
            except Exception as exc:  # pragma: no cover - UI safety net
                LOGGER.exception("住民税通知書の取込に失敗しました")
                import_errors.append(f"{upload.name} の取込に失敗しました。詳細はローカルログを確認してください。")
        st.session_state["resident_tax_last_import"] = {
            "groups": import_groups,
            "notice_ids": new_notice_ids,
            "errors": import_errors,
            "failed_drafts": failed_drafts,
        }
        if new_notice_ids:
            st.session_state["resident_tax_selected_notice_id"] = int(new_notice_ids[0])
            st.session_state.pop("resident_tax_last_rendered_notice_id", None)
        st.rerun()

    notices = fetch_resident_tax_notices()
    sources = fetch_resident_tax_source_documents()
    last_import = st.session_state.get("resident_tax_last_import") or {}
    for error_message in last_import.get("errors", []):
        st.warning(error_message)
    for draft in last_import.get("failed_drafts", []):
        digest = str(draft.get("digest") or "")
        draft_key = digest[:16]
        draft_data = draft.get("data") or b""
        draft_filename = str(draft.get("filename") or "通知書.pdf")
        draft_result = draft.get("result") or {}
        partial_fields = draft_result.get("fields") or {}
        with st.expander(
            f"DB未保存の原本プレビュー・手動入力：{shortened_source_filename(draft_filename)}",
            expanded=True,
        ):
            st.info(
                "重大な読取失敗を検出したため、この原本はDBへ保存していません。"
                "原本を見ながら全項目を手動入力し、検証に合格した場合だけ確認データを作成できます。"
            )
            draft_images, draft_preview_warnings = preview_images(draft_data, draft_filename)
            for page_index, image_data in enumerate(draft_images, start=1):
                st.image(image_data, caption=f"未保存原本 ページ{page_index}", use_container_width=True)
            for warning in draft_preview_warnings:
                st.warning(warning)
            employee_options = ["対象従業員を選択してください", *masters["employees"].keys()]
            partial_employee = str(partial_fields.get("employee_name") or "")
            employee_index = employee_options.index(partial_employee) if partial_employee in employee_options else 0
            manual_employee = st.selectbox(
                "対象従業員（手動入力）",
                employee_options,
                index=employee_index,
                key=f"failed_employee_{draft_key}",
            )
            manual_year = st.text_input(
                "年度（手動入力）",
                value=str(partial_fields.get("fiscal_year") or masters["company"].get("fiscal_year", "")),
                key=f"failed_year_{draft_key}",
            )
            manual_municipality = st.text_input(
                "市区町村（手動入力）",
                value=str(partial_fields.get("municipality") or ""),
                key=f"failed_municipality_{draft_key}",
            )
            annual_value = partial_fields.get("annual_amount")
            manual_annual_raw = st.text_input(
                "年税額（手動入力）",
                value="" if annual_value is None else str(annual_value),
                key=f"failed_annual_{draft_key}",
            )
            partial_monthly = partial_fields.get("monthly_amounts") or {}
            manual_rows = [
                {"対象月": month, "確定値": partial_monthly.get(month)}
                for month in NOTICE_MONTHS
            ]
            manual_month_editor = st.data_editor(
                pd.DataFrame(manual_rows),
                hide_index=True,
                disabled=["対象月"],
                use_container_width=True,
                key=f"failed_months_{draft_key}",
            )
            manual_reason = st.text_input(
                "自動読取できなかった原本を手動入力する理由",
                key=f"failed_reason_{draft_key}",
            )
            manual_annual, manual_annual_error = parse_non_negative_int(
                "年税額", manual_annual_raw
            )
            manual_months: dict[str, int] = {}
            manual_missing = False
            for _, row in manual_month_editor.iterrows():
                if pd.isna(row["確定値"]):
                    manual_missing = True
                    continue
                manual_months[str(row["対象月"])] = int(row["確定値"])
            manual_total_matches = (
                not manual_missing
                and not manual_annual_error
                and sum(manual_months.values()) == manual_annual
            )
            if not manual_total_matches and not manual_missing and not manual_annual_error:
                st.warning("月別合計と年税額が一致しません。")
            manual_save_disabled = not (
                manual_employee in masters["employees"]
                and manual_year.strip()
                and manual_municipality.strip()
                and manual_annual_raw.strip()
                and not manual_annual_error
                and not manual_missing
                and manual_total_matches
                and manual_reason.strip()
            )
            if st.button(
                "手動入力内容を検証して確認データを作成",
                key=f"save_failed_draft_{draft_key}",
                disabled=manual_save_disabled,
            ):
                employee_code = str(masters["employees"][manual_employee].get("employee_id", "")).zfill(6)
                manual_fields = {
                    "employee_code": employee_code,
                    "employee_name": manual_employee,
                    "recognized_name": str(partial_fields.get("recognized_name") or ""),
                    "fiscal_year": manual_year.strip(),
                    "municipality": manual_municipality.strip(),
                    "designation_number": str(partial_fields.get("designation_number") or ""),
                    "annual_amount": manual_annual,
                    "monthly_amounts": manual_months,
                    "notes": manual_reason.strip(),
                    "extraction_method": "利用者手動入力",
                    "document_type": str(draft_result.get("document_type") or "unknown"),
                }
                manual_candidate = {
                    "fields": manual_fields,
                    "warnings": ["自動読取失敗後に全項目を利用者が手動入力した確認データです。"],
                    "confidence": None,
                    "page_number": 1,
                    "region": {},
                }
                manual_result = {
                    "notices": [manual_candidate],
                    "ocr_result": draft_result.get("ocr_result") or {},
                }
                manual_failures = validate_notice_import(manual_result)
                if manual_failures:
                    st.error(" ".join(manual_failures))
                else:
                    manual_path, manual_digest, manual_created = save_source_file(
                        draft_data, draft_filename
                    )
                    try:
                        source_id, notice_ids = create_resident_tax_source_document(
                            source_filename=draft_filename,
                            stored_filename=str(manual_path.relative_to(BASE_DIR)),
                            source_sha256=manual_digest,
                            source_mime=str(draft.get("mime") or ""),
                            extracted_notices=[manual_candidate],
                            raw_text=str(draft_result.get("raw_text") or ""),
                            warnings=manual_candidate["warnings"],
                            confidence=None,
                            detected_document_type=str(draft_result.get("document_type") or "unknown"),
                            fiscal_year=manual_year.strip(),
                            municipality=manual_municipality.strip(),
                            page_count=int(draft_result.get("page_count") or 1),
                            ocr_result=draft_result.get("ocr_result") or {},
                        )
                    except Exception:
                        if manual_created and manual_path.is_file():
                            manual_path.unlink()
                        raise
                    st.session_state["resident_tax_last_import"] = {
                        "groups": [{
                            "source_id": int(source_id),
                            "filename": draft_filename,
                            "document_type": str(draft_result.get("document_type") or "unknown"),
                            "notice_ids": [int(value) for value in notice_ids],
                            "pdf_status": (draft_result.get("ocr_result") or {}).get("pdf_security") or {},
                        }],
                        "notice_ids": [int(value) for value in notice_ids],
                        "errors": [],
                        "failed_drafts": [],
                    }
                    st.session_state["resident_tax_selected_notice_id"] = int(notice_ids[0])
                    st.session_state.pop("resident_tax_last_rendered_notice_id", None)
                    st.rerun()
    if not notices:
        st.info("取り込まれた通知書はありません。")
        return
    st.caption(f"原本 {len(sources)}件 / 従業員別確認データ {len(notices)}件")
    notice_by_id = {int(row["id"]): row for row in notices}
    recent_ids = [
        int(notice_id)
        for notice_id in last_import.get("notice_ids", [])
        if int(notice_id) in notice_by_id
    ]
    if recent_ids:
        groups = last_import.get("groups") or []
        source_count = len(groups)
        st.success(
            f"今回、原本{source_count}件から従業員別確認データを{len(recent_ids)}件取り込みました。"
            "最初の新規通知書を自動選択しています。"
        )
        for group in groups:
            group_ids = [int(value) for value in group.get("notice_ids", []) if int(value) in notice_by_id]
            if not group_ids:
                continue
            st.write(
                f"{document_type_label(str(group.get('document_type') or 'unknown'))}："
                f"{shortened_source_filename(str(group.get('filename') or ''))} / "
                f"確認データ{len(group_ids)}件"
            )
            pdf_status = group.get("pdf_status") or {}
            if pdf_status:
                encrypted_label, text_label, ocr_label = pdf_processing_status_labels(pdf_status)
                st.caption(
                    f"暗号化PDF：{encrypted_label} / PDFテキスト：{text_label} / "
                    f"OCRフォールバック：{ocr_label}"
                )
            button_columns = st.columns(min(3, len(group_ids)))
            for index, notice_id in enumerate(group_ids):
                recent = notice_by_id[notice_id]
                recent_auto = json.loads(recent.get("auto_result_json") or "{}")
                recent_name, recent_code = notice_employee_identity(recent, recent_auto)
                label = f"{recent_name or '未照合'}（{recent_code or '未選択'}）を開く"
                if button_columns[index % len(button_columns)].button(
                    label,
                    key=f"open_recent_notice_{notice_id}",
                ):
                    st.session_state["resident_tax_selected_notice_id"] = notice_id
                    st.session_state.pop("resident_tax_last_rendered_notice_id", None)
                    st.rerun()
    label_by_id = {
        int(row["id"]): (
            f"通知書ID{row['id']}｜"
            f"{document_type_label(str(row.get('source_document_type') or row.get('document_type') or 'unknown'))}｜"
            f"{row.get('employee_name') or '未照合'}"
            f"（{str(row.get('employee_code') or '').zfill(6) if row.get('employee_code') else '未選択'}）｜"
            f"{row.get('fiscal_year') or '年度未認識'}｜"
            f"{shortened_source_filename(str(row.get('source_filename') or ''))}"
        )
        for row in notices
    }
    available_ids = list(label_by_id)
    if int(st.session_state.get("resident_tax_selected_notice_id") or 0) not in label_by_id:
        st.session_state["resident_tax_selected_notice_id"] = int(available_ids[0])
    selected_id = int(
        st.selectbox(
            "確認する通知書",
            available_ids,
            format_func=lambda notice_id: label_by_id[int(notice_id)],
            key="resident_tax_selected_notice_id",
        )
    )
    previous_notice_id = st.session_state.get("resident_tax_last_rendered_notice_id")
    if previous_notice_id != selected_id:
        if previous_notice_id is not None:
            clear_notice_form_state(int(previous_notice_id))
        clear_notice_form_state(selected_id)
        st.session_state["resident_tax_last_rendered_notice_id"] = selected_id
    notice = next(row for row in notices if int(row["id"]) == selected_id)
    st.caption(f"選択中の元ファイル（全文）：{notice.get('source_filename') or '不明'}")
    auto = json.loads(notice.get("auto_result_json") or "{}")
    confirmed = json.loads(notice.get("confirmed_result_json") or "{}") if notice.get("is_confirmed") else {}
    source_path = BASE_DIR / str(notice["stored_filename"])
    source_data = source_path.read_bytes() if source_path.is_file() else b""
    source_pdf_status = (
        json.loads(notice.get("source_ocr_result_json") or "{}")
        .get("pdf_security", {})
    )
    source_password = st.session_state.get(
        f"resident_pdf_password_{str(notice.get('source_sha256') or '')[:16]}"
    )
    if source_pdf_status:
        encrypted_label, text_label, ocr_label = pdf_processing_status_labels(source_pdf_status)
        st.caption(
            f"暗号化PDF：{encrypted_label} / PDFテキスト：{text_label} / "
            f"OCRフォールバック：{ocr_label}"
        )

    detected_type = str(notice.get("source_detected_document_type") or "unknown")
    current_type = str(notice.get("source_document_type") or notice.get("document_type") or "unknown")
    type_options = ["company_multi", "individual_single", "unknown"]
    st.markdown(f"**帳票種類：** {document_type_label(current_type)}")
    type_status = "確定候補" if current_type == detected_type and current_type != "unknown" else "利用者確認が必要"
    if current_type != detected_type and current_type != "unknown":
        type_status = "利用者訂正済み"
    st.caption(
        f"自動判定：{document_type_label(detected_type)} / 判定状態：{type_status}"
    )
    if current_type == "unknown":
        st.warning("帳票種類を判定できませんでした。原本を確認して帳票種類を選択してください。")
        type_editing = True
    else:
        type_editing = st.checkbox(
            "帳票種類を訂正する",
            key=f"notice_type_edit_{selected_id}",
            disabled=bool(notice.get("is_confirmed")),
        )
    if type_editing:
        selected_type = st.selectbox(
            "訂正後の帳票種類",
            type_options,
            index=type_options.index(current_type) if current_type in type_options else 2,
            format_func=document_type_label,
            key=f"notice_type_choice_{selected_id}",
        )
        type_reason = st.text_input(
            "帳票種類の訂正理由（必須）",
            key=f"notice_type_reason_{selected_id}",
        )
        st.write(
            f"変更前：{document_type_label(current_type)} → "
            f"変更後：{document_type_label(selected_type)}"
        )
        type_confirm = st.checkbox(
            "変更前後と原本を確認しました",
            key=f"notice_type_confirm_{selected_id}",
        )
        type_save_disabled = (
            selected_type == current_type
            or not type_reason.strip()
            or not type_confirm
        )
        if st.button(
            "帳票種類の訂正を保存",
            key=f"save_notice_type_{selected_id}",
            disabled=type_save_disabled,
        ):
            update_resident_tax_source_document_type(
                int(notice["source_document_id"]),
                selected_type,
                reason=type_reason.strip(),
            )
            clear_notice_form_state(selected_id)
            st.success("帳票種類の訂正と理由を保存しました。")
            st.rerun()

    preview_col, edit_col = st.columns([1.2, 1])
    with preview_col:
        st.subheader("元資料の該当領域")
        st.caption(
            f"元ファイル: {notice['source_filename']} / SHA-256: {notice['source_sha256']} / "
            f"取込: {format_history_datetime(notice['imported_at'])}"
        )
        if source_data:
            region = json.loads(notice.get("region_json") or "{}")
            region_image, region_warnings = preview_region_image(
                source_data, notice["source_filename"], region, password=source_password
            )
            if region_image:
                st.image(
                    region_image,
                    caption=f"通知書ID {selected_id} の氏名・年税額・月別税額表（拡大）",
                    use_container_width=True,
                )
            for warning in region_warnings:
                st.warning(warning)
            with st.expander("原本全体を表示"):
                images, preview_warnings = preview_images(
                    source_data, notice["source_filename"], password=source_password
                )
                for page_index, image_data in enumerate(images, start=1):
                    st.image(image_data, caption=f"ページ {page_index}", use_container_width=True)
                if not images and Path(notice["source_filename"]).suffix.lower() == ".pdf":
                    encoded_pdf = base64.b64encode(source_data).decode("ascii")
                    st.markdown(
                        f'<iframe src="data:application/pdf;base64,{encoded_pdf}" '
                        'width="100%" height="760" title="住民税通知書PDFプレビュー"></iframe>',
                        unsafe_allow_html=True,
                    )
                for warning in preview_warnings:
                    st.warning(warning)
        else:
            st.error("保存した元ファイルが見つかりません。")
        with st.expander("抽出した文字列を確認"):
            st.text_area("抽出文字列", value=notice.get("raw_extracted_text") or "", height=240, disabled=True)

    with edit_col:
        st.subheader("自動読取結果と確定値")
        if int(notice.get("is_confirmed") or 0):
            st.success(
                f"確認済み（{format_history_datetime(notice.get('confirmed_at'))}） / "
                f"{'現在有効' if notice.get('is_active') else '無効版'} / 改訂{notice['revision_number']}"
            )
        else:
            st.warning("要確認：元資料とすべての項目・月額を照合してください。")
        if notice.get("confidence") is not None:
            st.metric("OCR平均信頼度（参考）", f"{float(notice['confidence']):.1f}")
        st.caption(
            f"自動認識：通知書氏名={auto.get('recognized_name') or '認識できず'} / "
            f"対象従業員={auto.get('employee_name') or '未照合'} "
            f"（{'一致' if auto.get('employee_name') in masters['employees'] else '不一致'}） / "
            f"年度={auto.get('fiscal_year') or '未認識'} "
            f"（{'一致' if auto.get('fiscal_year') == '令和8年度' else '不一致'}） / "
            f"市区町村={auto.get('municipality') or '未認識'} / "
            f"年税額={yen(int(auto.get('annual_amount') or 0))}"
        )
        employee_names = ["対象従業員を選択してください", *masters["employees"].keys()]
        linked_name = str(
            confirmed.get("employee_name")
            or notice.get("employee_name")
            or ""
        )
        linked_code = str(
            confirmed.get("employee_code")
            or notice.get("employee_code")
            or ""
        )
        employee_name = linked_name if linked_name in employee_names[1:] else employee_names[0]
        employee_code = (
            str(masters["employees"][employee_name].get("employee_id", "")).zfill(6)
            if employee_name in masters["employees"] else ""
        )
        if employee_name in employee_names[1:]:
            st.markdown(f"**対象従業員：** {employee_name}（{employee_code}）")
            auto_matched_name = str(auto.get("employee_name") or "")
            match_status = "一致" if auto_matched_name == employee_name else "利用者訂正済み"
            st.caption(
                f"氏名照合：{match_status} / "
                f"通知書氏名：{auto.get('recognized_name') or '認識できず'} / "
                f"紐付け先：{employee_name}（{employee_code}）"
            )
            employee_editing = st.checkbox(
                "紐付け先を訂正する",
                key=f"notice_employee_edit_{selected_id}",
                disabled=bool(notice.get("is_confirmed")),
            )
        else:
            st.warning("対象従業員：未照合")
            st.caption(f"通知書氏名：{auto.get('recognized_name') or '認識できず'}")
            employee_editing = True

        if employee_editing:
            employee_choice = st.selectbox(
                "紐付け先従業員を選択",
                employee_names,
                index=employee_names.index(employee_name) if employee_name in employee_names else 0,
                key=f"notice_employee_choice_{selected_id}",
            )
            employee_reason = st.text_input(
                "紐付け先の訂正理由（必須）",
                key=f"notice_employee_reason_{selected_id}",
            )
            before_employee = (
                f"{employee_name}（{employee_code}）"
                if employee_name in employee_names[1:]
                else "未照合"
            )
            after_code = (
                str(masters["employees"][employee_choice].get("employee_id", "")).zfill(6)
                if employee_choice in masters["employees"] else ""
            )
            after_employee = (
                f"{employee_choice}（{after_code}）"
                if employee_choice in employee_names[1:]
                else "未選択"
            )
            st.write(f"変更前：{before_employee} → 変更後：{after_employee}")
            employee_confirm = st.checkbox(
                "変更前後と通知書氏名を確認しました",
                key=f"notice_employee_confirm_{selected_id}",
            )
            link_save_disabled = (
                employee_choice not in employee_names[1:]
                or employee_choice == employee_name
                or not employee_reason.strip()
                or not employee_confirm
            )
            if st.button(
                "紐付け先の訂正を保存",
                key=f"save_notice_employee_{selected_id}",
                disabled=link_save_disabled,
            ):
                update_resident_tax_notice_employee_link(
                    selected_id,
                    after_code,
                    employee_choice,
                    employee_reason.strip(),
                )
                clear_notice_form_state(selected_id)
                st.success("紐付け先の訂正と理由を保存しました。読取金額は変更していません。")
                st.rerun()

        fiscal_year = st.text_input(
            "年度",
            value=str(confirmed.get("fiscal_year") or auto.get("fiscal_year") or ""),
            key=f"notice_year_{selected_id}",
        )
        municipality = st.text_input(
            "市区町村",
            value=str(confirmed.get("municipality") or auto.get("municipality") or ""),
            key=f"notice_municipality_{selected_id}",
        )
        annual_initial = (
            confirmed.get("annual_amount")
            if "annual_amount" in confirmed
            else auto.get("annual_amount", "")
        )
        annual_raw = st.text_input(
            "年税額",
            value=str(annual_initial if annual_initial is not None else ""),
            key=f"notice_annual_{selected_id}",
        )
        auto_monthly = auto.get("monthly_amounts") or {}
        confirmed_monthly = confirmed.get("monthly_amounts") or {}
        monthly_rows = []
        for month in NOTICE_MONTHS:
            auto_value = auto_monthly.get(month) if month in auto_monthly else None
            confirmed_value = (
                confirmed_monthly.get(month)
                if month in confirmed_monthly
                else auto_value
            )
            monthly_rows.append(
                {
                    "対象月": month,
                    "自動読取値": int(auto_value) if auto_value is not None else None,
                    "確定値": int(confirmed_value) if confirmed_value is not None else None,
                }
            )
        edited_months = st.data_editor(
            pd.DataFrame(monthly_rows),
            hide_index=True,
            use_container_width=True,
            disabled=["対象月", "自動読取値"],
            key=f"notice_month_editor_{selected_id}",
        )
        notes = st.text_area(
            "備考・修正理由",
            value=str(confirmed.get("notes") or notice.get("notes") or ""),
            key=f"notice_notes_{selected_id}",
        )

        annual_amount, annual_error = parse_non_negative_int("年税額", annual_raw)
        month_values: dict[str, int] = {}
        missing_confirmed_months: list[str] = []
        for _, row in edited_months.iterrows():
            month = str(row["対象月"])
            raw_value = row["確定値"]
            if pd.isna(raw_value):
                missing_confirmed_months.append(month)
                continue
            month_values[month] = int(raw_value)
        monthly_total = sum(month_values.values())
        st.metric("月別合計", yen(monthly_total))
        warnings = json.loads(notice.get("warnings_json") or "[]")
        if annual_error:
            warnings.append(annual_error)
        if not str(annual_raw).strip():
            warnings.append("年税額を認識できません。")
        if missing_confirmed_months:
            warnings.append("12か月のうち未読取または未入力の月があります。")
        if not annual_error and not missing_confirmed_months and monthly_total != annual_amount:
            warnings.append("月別合計と年税額が一致しません。")
        if annual_amount == 0 and monthly_total > 0:
            warnings.append("年税額が0円ですが月別税額が存在します。")
        if any(0 < amount < 100 for amount in month_values.values()):
            warnings.append("100円未満の月額があり、誤読の可能性が高いため確認が必要です。")
        if employee_name not in masters["employees"]:
            warnings.append("対象従業員が未照合です。")
        if not fiscal_year.strip():
            warnings.append("年度を認識できません。")
        elif fiscal_year != str(masters["company"].get("fiscal_year", "")):
            warnings.append(f"今回の対象年度（{masters['company'].get('fiscal_year', '')}）と一致しません。")
        if not municipality.strip():
            warnings.append("市区町村を認識できません。")
        if current_type == "unknown":
            warnings.append("帳票種類を判定できません。")
        if current_type == "company_multi":
            sibling_names = {
                str(row.get("employee_name") or "")
                for row in notices
                if int(row.get("source_document_id") or 0) == int(notice.get("source_document_id") or 0)
            }
            if not set(masters["employees"]).issubset(sibling_names):
                warnings.append("会社用帳票ですが、対象従業員2名を検出できていません。")
        warnings = list(dict.fromkeys(warnings))
        for warning in warnings:
            st.error(warning)
        confirmation_checked = st.checkbox(
            "元資料と6月から翌年5月までの12か月分の住民税額を照合しました",
            key=f"notice_confirmation_checked_{selected_id}",
        )
        exception_confirmed = False
        exception_reason = ""
        if warnings:
            exception_confirmed = st.checkbox(
                "警告内容を確認し、例外的に確定します",
                key=f"notice_exception_confirm_{selected_id}",
            )
            exception_reason = st.text_input(
                "警告が残る状態で確定する理由",
                key=f"notice_exception_reason_{selected_id}",
            )
        base_requirements_complete = (
            employee_name in masters["employees"]
            and not missing_confirmed_months
            and not annual_error
            and bool(str(annual_raw).strip())
        )
        exception_requirements_complete = (
            not warnings
            or (exception_confirmed and bool(exception_reason.strip()))
        )
        confirm_disabled = not (
            confirmation_checked
            and base_requirements_complete
            and exception_requirements_complete
        )
        if warnings and confirm_disabled:
            st.caption(
                "警告があるため、通常確認、例外確定、具体的な理由、対象従業員、"
                "12か月分の確定値をすべて確認してください。"
            )
        if st.button(
            "確認済みとして確定反映",
            key=f"confirm_notice_{selected_id}",
            disabled=confirm_disabled,
        ):
            if annual_error:
                st.error(annual_error)
            else:
                values = {
                    "employee_code": employee_code,
                    "employee_name": employee_name,
                    "fiscal_year": fiscal_year.strip(),
                    "municipality": municipality.strip(),
                    "annual_amount": annual_amount,
                    "monthly_amounts": month_values,
                    "notes": notes.strip(),
                    "all_months_manually_checked": True,
                }
                confirm_resident_tax_notice(selected_id, values, exception_reason.strip() or notes.strip())
                st.success("確認済み通知書として確定し、この従業員・年度の有効版にしました。")
                st.rerun()

        comparisons = []
        comparison_notice_ids: list[int] = []
        if employee_name in masters["employees"] and fiscal_year.strip():
            for other in notices:
                other_auto = json.loads(other.get("auto_result_json") or "{}")
                other_confirmed = json.loads(other.get("confirmed_result_json") or "{}") if other.get("is_confirmed") else {}
                other_values = other_confirmed or other_auto
                other_name = str(other.get("employee_name") or other_values.get("employee_name") or "")
                other_year = str(other.get("fiscal_year") or other_values.get("fiscal_year") or "")
                if (
                    other_name != employee_name
                    or other_year != fiscal_year.strip()
                ):
                    continue
                other_id = int(other["id"])
                other_months = other_values.get("monthly_amounts") or {}
                if other_id == selected_id:
                    month_result = "表示中"
                    display_annual = annual_amount
                else:
                    month_result = (
                        "一致"
                        if all(
                            int(other_months.get(month) or 0) == month_values.get(month, 0)
                            for month in NOTICE_MONTHS
                        )
                        else "不一致"
                    )
                    display_annual = int(other_values.get("annual_amount") or other.get("annual_amount") or 0)
                comparisons.append(
                    {
                        "表示中": "●" if other_id == selected_id else "",
                        "通知書ID": other_id,
                        "帳票種類": document_type_label(
                            str(other.get("source_document_type") or other.get("document_type") or "unknown")
                        ),
                        "対象従業員": f"{other_name}（{str(other.get('employee_code') or '').zfill(6)}）",
                        "年度": other_year,
                        "元ファイル": str(other.get("source_filename") or ""),
                        "年税額": display_annual,
                        "月別比較": month_result,
                        "確定状態": "確認済" if other.get("is_confirmed") else "要確認",
                        "有効状態": "有効" if other.get("is_active") else "無効",
                    }
                )
                comparison_notice_ids.append(other_id)
        if len(comparisons) >= 2:
            st.subheader("同一従業員・年度の資料比較")
            st.dataframe(pd.DataFrame(comparisons), hide_index=True, use_container_width=True)
            switchable_ids = [notice_id for notice_id in comparison_notice_ids if notice_id != selected_id]
            if switchable_ids:
                switch_columns = st.columns(min(3, len(switchable_ids)))
                for index, notice_id in enumerate(switchable_ids):
                    switch_columns[index % len(switch_columns)].button(
                        f"通知書ID{notice_id}へ切替",
                        key=f"compare_switch_notice_{selected_id}_{notice_id}",
                        on_click=select_resident_notice,
                        args=(notice_id,),
                    )
            st.caption("相違があっても既存の有効版を自動上書きしません。確認後に有効版を選択してください。")

        if int(notice.get("is_confirmed") or 0) and not int(notice.get("is_active") or 0):
            if st.button("この確認済み改訂版を現在有効にする", key=f"activate_notice_{selected_id}"):
                activate_resident_tax_notice(selected_id)
                st.success("有効版を切り替えました。過去の給与履歴は変更されません。")
                st.rerun()


def history_page() -> None:
    st.header("計算履歴 / Payroll History")
    rows = fetch_history(200)
    if not rows:
        st.info("まだ計算履歴はありません。")
        return
    summary_df = pd.DataFrame(history_summary(rows))
    delete_df = summary_df.copy()
    delete_df.insert(0, "削除", False)
    edited_df = st.data_editor(
        delete_df,
        hide_index=True,
        use_container_width=True,
        disabled=[column for column in delete_df.columns if column != "削除"],
        key="history_delete_editor",
    )

    if st.button("選択した履歴を削除"):
        selected_ids = edited_df.loc[edited_df["削除"], "履歴ID"].astype(int).tolist()
        if not selected_ids:
            st.warning("削除する履歴を選択してください。")
        else:
            st.session_state["pending_delete_history_ids"] = selected_ids

    pending_delete_ids = st.session_state.get("pending_delete_history_ids", [])
    if pending_delete_ids:
        st.warning("選択した計算履歴を削除します。\n本当によろしいですか？")
        confirm_col, cancel_col = st.columns(2)
        if confirm_col.button("はい、削除する"):
            deleted_count = delete_payroll_history(pending_delete_ids)
            st.session_state.pop("pending_delete_history_ids", None)
            st.success(f"{deleted_count}件の計算履歴を削除しました。")
            st.rerun()
        if cancel_col.button("キャンセル"):
            st.session_state.pop("pending_delete_history_ids", None)
            st.info("削除をキャンセルしました。")
            st.rerun()

    selected_label = st.selectbox("詳細を確認する履歴", [history_label(row) for row in rows])
    selected_row = rows[[history_label(row) for row in rows].index(selected_label)]
    selected_result = result_from_history_row(selected_row)
    with st.expander("選択した履歴の詳細", expanded=True):
        st.dataframe(pd.DataFrame(history_detail(selected_result)), hide_index=True, use_container_width=True)
        st.info("正式給与PDF・CSVの発行とダウンロードは「給与明細・CSV発行」に統一されています。")
        if st.button("この履歴を給与明細・CSV発行画面で開く", key=f"open_issue_{selected_row['id']}"):
            st.session_state["requested_issue_history_id"] = int(selected_row["id"])
            st.session_state["pending_main_page"] = "給与明細・CSV発行"
            st.rerun()
        if st.button("修正して再発行", key=f"reissue_{selected_row['id']}"):
            load_history_into_payroll_form(selected_row)
            st.session_state["pending_main_page"] = "給与計算入力"
            st.success("選択した履歴を給与計算入力画面へ読み込みました。内容を修正して再計算してください。")
            st.rerun()


def issuance_status_page(masters: dict) -> None:
    st.header("発行状況確認")
    rows = fetch_history(1000)
    status_by_key: dict[tuple[date, str], dict[str, str | int | None | dict]] = {}
    for row in rows:
        payment_date = date.fromisoformat(row["payment_date"])
        key = (date(payment_date.year, payment_date.month, 1), row["employee_name"])
        status = status_by_key.setdefault(
            key,
            {
                "history_count": 0,
                "reissued_count": 0,
                "first_issued_at": None,
                "latest_reissued_at": None,
                "latest_row": None,
            },
        )
        status["history_count"] = int(status["history_count"] or 0) + 1
        calculated_at = row.get("calculated_at")
        first_issued_at = status.get("first_issued_at")
        if calculated_at and (not first_issued_at or str(calculated_at) < str(first_issued_at)):
            status["first_issued_at"] = calculated_at
        latest_row = status.get("latest_row")
        if not latest_row or (calculated_at and str(calculated_at) > str(latest_row.get("calculated_at", ""))):
            status["latest_row"] = row
        if row.get("issue_status") == "reissued":
            status["reissued_count"] = int(status["reissued_count"] or 0) + 1
            latest_reissued_at = status.get("latest_reissued_at")
            if calculated_at and (not latest_reissued_at or str(calculated_at) > str(latest_reissued_at)):
                status["latest_reissued_at"] = calculated_at

    label_map = {month: label for label, month in payment_month_options()}
    employees = list(masters["employees"].keys())

    data = []
    for payment_month in month_range(date(2026, 1, 1), 6):
        for employee_name in employees:
            status = status_by_key.get((payment_month, employee_name))
            if not status:
                status_text = "未発行"
                csv_label = ""
                sort_at = ""
            elif int(status.get("reissued_count") or 0) > 0:
                status_text = (
                    f"修正再発行{int(status['reissued_count'])}回目\n"
                    f"最終再発行：{format_history_datetime(status.get('latest_reissued_at'))}"
                )
                csv_label = (
                    f"{label_map.get(payment_month, payment_month.isoformat())} / {employee_name} / "
                    f"修正再発行{int(status['reissued_count'])}回目"
                )
                if status.get("latest_reissued_at"):
                    csv_label += f" / 最終再発行：{format_history_datetime(status.get('latest_reissued_at'))}"
                sort_at = str(status.get("latest_reissued_at") or "")
            else:
                first_issued_at = status.get("first_issued_at")
                status_text = "発行済"
                if first_issued_at:
                    status_text = f"発行済\n初回発行：{format_history_datetime(str(first_issued_at))}"
                csv_label = f"{label_map.get(payment_month, payment_month.isoformat())} / {employee_name} / 発行済"
                if first_issued_at:
                    csv_label += f" / 初回発行：{format_history_datetime(str(first_issued_at))}"
                sort_at = str(first_issued_at or "")
            display_row = {
                "支給年月": label_map.get(payment_month, payment_month.isoformat()),
                "対象者": employee_name,
                "ステータス": status_text,
            }
            data.append(display_row)
    st.dataframe(pd.DataFrame(data), hide_index=True, use_container_width=True)

    st.info("この画面は発行状況の確認専用です。PDF・CSVの発行とダウンロードは統合発行画面で行います。")
    if rows:
        selected_history_id = int(
            st.selectbox(
                "統合発行画面で開く履歴",
                [int(row["id"]) for row in rows],
                format_func=lambda history_id: history_label(next(row for row in rows if int(row["id"]) == int(history_id))),
                key="issuance_status_open_history_id",
            )
        )
        if st.button("この履歴を給与明細・CSV発行画面で開く", key="issuance_status_open_button"):
            st.session_state["requested_issue_history_id"] = selected_history_id
            st.session_state["pending_main_page"] = "給与明細・CSV発行"
            st.rerun()


def pdf_page() -> None:
    st.header("給与明細・CSV発行 / Payslip & CSV Issuance")
    st.info("正式給与のPDF・管理CSVは、この画面だけから発行します。選択した保存済み履歴IDの確定値を使用し、再計算しません。")
    rows = fetch_history(200)
    if not rows:
        st.warning("まだ保存済みの計算履歴がありません。先に給与計算入力画面で計算し、計算履歴へ保存してください。")
        return

    history_ids = [int(row["id"]) for row in rows]
    requested_id = int(st.session_state.pop("requested_issue_history_id", history_ids[0]))
    default_index = history_ids.index(requested_id) if requested_id in history_ids else 0
    selected_history_id = int(
        st.selectbox(
            "PDF・CSVを発行する保存済み履歴ID",
            history_ids,
            index=default_index,
            format_func=lambda history_id: history_label(next(row for row in rows if int(row["id"]) == int(history_id))),
            key="unified_issue_history_id",
        )
    )
    selected_row = next(row for row in rows if int(row["id"]) == selected_history_id)
    result = result_from_history_row(selected_row)
    result_cards(result)
    st.caption("画面表示・PDF・CSVは、上で選択した同じ履歴IDの result_json を共通入力元にします。")
    if st.button("給与明細PDF・管理CSVを作成／再発行"):
        try:
            csv_meta = csv_metadata_for_history(rows, selected_row, result)
            pdf_path, csv_path, issue_info = generate_payroll_pdf_csv(
                int(selected_row["id"]), result, csv_meta
            )
            remember_generated_payslip_downloads(int(selected_row["id"]), pdf_path, csv_path)
            st.session_state["last_issue_info"] = issue_info
        except Exception as exc:  # pragma: no cover - UI safety net
            st.error(f"給与明細PDFまたはCSVの作成に失敗しました。エラー内容：{exc}")
    render_payslip_download_buttons(int(selected_row["id"]))
    issued = fetch_issued_files(int(selected_row["id"]))
    if issued:
        st.subheader("この履歴IDの発行記録")
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "種別": row["file_type"],
                        "発行日時": format_history_datetime(row["issued_at"]),
                        "再発行回数": int(row["reissue_count"]),
                        "ファイル名": row["filename"],
                        "SHA-256": row["file_sha256"],
                        "保存場所": row["stored_path"],
                    }
                    for row in issued
                ]
            ),
            hide_index=True,
            use_container_width=True,
        )


def report_batch_page(masters: dict) -> None:
    st.header("給与関連一括帳票・ZIP")
    st.info("計算履歴へ保存済みのデータだけを使用して、給与明細と給与関連帳票を発行します。")
    rows = fetch_history(5000)
    if not rows:
        st.warning("発行対象となる計算履歴がありません。\n先に給与計算を行い、計算履歴へ保存してください。")
        return

    available_months = sorted({payment_month_from_history_row(row) for row in rows}, reverse=True)
    selected_month = st.selectbox(
        "支給年月",
        available_months,
        format_func=lambda value: f"{value.year}年{value.month}月分",
        key="report_payment_month",
    )
    employee_master = masters["employees"]
    selected_rows = latest_history_rows_for_payment_month(rows, selected_month, employee_master)
    selected_results = [result_from_history_row(row) for row in selected_rows]

    target_summary = []
    for row, result in zip(selected_rows, selected_results):
        employee_code = str(employee_master.get(result.employee_name, {}).get("employee_id", "")).zfill(6)
        target_summary.append(
            {
                "従業員コード": employee_code,
                "氏名": result.employee_name,
                "履歴ID": int(row["id"]),
                "保存日時": format_history_datetime(row.get("calculated_at")),
            }
        )
    st.caption("同じ支給年月・同じ社員の履歴が複数ある場合は、最新の保存履歴を使用します。")
    st.dataframe(pd.DataFrame(target_summary), hide_index=True, use_container_width=True)

    st.subheader("発行対象を選択")
    if REPORT_SELECT_ALL_KEY not in st.session_state:
        st.session_state[REPORT_SELECT_ALL_KEY] = all(
            bool(st.session_state.get(key, False)) for key in REPORT_ISSUE_CHECKBOX_KEYS
        )
    st.checkbox(
        "すべて選択",
        key=REPORT_SELECT_ALL_KEY,
        on_change=sync_report_items_from_select_all,
    )
    issue_payslips = st.checkbox(
        "給与明細",
        key="report_issue_payslips",
        on_change=sync_report_select_all_from_items,
    )
    issue_payroll_summary = st.checkbox(
        "給与支給・控除一覧表",
        key="report_issue_payroll_summary",
        on_change=sync_report_select_all_from_items,
    )
    issue_employer_insurance = st.checkbox(
        "事業所負担保険料一覧表",
        key="report_issue_employer_insurance",
        on_change=sync_report_select_all_from_items,
    )
    issue_resident_tax = st.checkbox(
        "住民税納付一覧表",
        key="report_issue_resident_tax",
        on_change=sync_report_select_all_from_items,
    )

    payslip_mode = ""
    individual_history_id: int | None = None
    if issue_payslips:
        payslip_mode = st.radio(
            "給与明細の発行方法",
            ["個別発行", "全員一括発行"],
            key="report_payslip_mode",
        )
        if payslip_mode == "個別発行":
            rows_by_id = {int(row["id"]): row for row in selected_rows}
            result_by_id = {int(row["id"]): result for row, result in zip(selected_rows, selected_results)}
            individual_history_id = int(
                st.selectbox(
                    "発行する従業員",
                    list(rows_by_id),
                    format_func=lambda history_id: (
                        f"{str(employee_master.get(result_by_id[int(history_id)].employee_name, {}).get('employee_id', '')).zfill(6)} "
                        f"{result_by_id[int(history_id)].employee_name}"
                    ),
                    key="report_individual_history_id",
                )
            )

    if st.button("選択したPDFを発行", key="issue_selected_reports"):
        selected_any = any(
            [issue_payslips, issue_payroll_summary, issue_employer_insurance, issue_resident_tax]
        )
        if not selected_any:
            st.session_state["report_batch_downloads"] = {
                "month": selected_month.isoformat(),
                "files": [],
                "notices": [],
                "errors": [],
            }
            st.warning("発行する帳票を1つ以上選択してください。")
        else:
            files: list[dict[str, str]] = []
            notices: list[str] = []
            errors: list[str] = []

            if issue_payslips:
                payslip_targets = list(zip(selected_rows, selected_results))
                if payslip_mode == "個別発行":
                    payslip_targets = [
                        pair for pair in payslip_targets if int(pair[0]["id"]) == int(individual_history_id or 0)
                    ]
                for row, result in payslip_targets:
                    try:
                        pdf_path = create_payslip_pdf(result)
                        record_issued_file(int(row["id"]), "payslip_pdf", pdf_path)
                        files.append(
                            {
                                "label": f"{result.employee_name}の{payslip_document_filename_label(result)}",
                                "path": str(pdf_path),
                            }
                        )
                    except Exception:
                        LOGGER.exception("給与明細PDFの一括発行に失敗しました: history_id=%s", row.get("id"))
                        errors.append(
                            f"{result.employee_name}の給与明細を作成できませんでした。保存先の空き容量と書き込み権限を確認し、もう一度実行してください。"
                        )

            if issue_payroll_summary:
                try:
                    pdf_path = create_payroll_summary_pdf(
                        selected_results,
                        masters["company"],
                        employee_master,
                    )
                    files.append({"label": "給与支給・控除一覧表", "path": str(pdf_path)})
                    for row in selected_rows:
                        record_issued_file(int(row["id"]), "payroll_summary_pdf", pdf_path)
                except Exception:
                    LOGGER.exception("給与支給・控除一覧表の発行に失敗しました")
                    errors.append(
                        "給与支給・控除一覧表を作成できませんでした。対象月の計算履歴と保存先を確認し、もう一度実行してください。"
                    )

            if issue_employer_insurance:
                try:
                    pdf_path = create_employer_insurance_pdf(
                        selected_results,
                        masters["company"],
                        employee_master,
                        masters["rates"],
                    )
                    files.append({"label": "事業所負担保険料一覧表", "path": str(pdf_path)})
                    for row in selected_rows:
                        record_issued_file(int(row["id"]), "employer_insurance_pdf", pdf_path)
                except ReportConfigurationError:
                    LOGGER.exception("事業所負担保険料の計算設定が不足しています")
                    errors.append("事業所負担保険料の計算設定が未完了のため、帳票を発行できません。管理者へ確認してください。")
                except Exception:
                    LOGGER.exception("事業所負担保険料一覧表の発行に失敗しました")
                    errors.append(
                        "事業所負担保険料一覧表を作成できませんでした。対象月の計算履歴と保存先を確認し、もう一度実行してください。"
                    )

            if issue_resident_tax:
                invalid_collection_names = [
                    result.employee_name
                    for result in selected_results
                    if get_resident_tax_collection_type(
                        employee_master.get(result.employee_name, {}), result.payment_date
                    )
                    is None
                ]
                if invalid_collection_names:
                    notices.append(
                        "住民税の徴収区分を確認できないため、住民税を給与控除していません。\n"
                        "管理者へ設定をご確認ください。\n"
                        f"対象：{', '.join(invalid_collection_names)}"
                    )
                if not resident_tax_targets(selected_results, employee_master):
                    notices.append("特別徴収対象者がいないため、\n住民税納付一覧表は発行できません")
                else:
                    try:
                        pdf_path = create_resident_tax_pdf(
                            selected_results,
                            masters["company"],
                            employee_master,
                        )
                        if pdf_path is not None:
                            files.append({"label": "住民税納付一覧表", "path": str(pdf_path)})
                            for row in selected_rows:
                                record_issued_file(int(row["id"]), "resident_tax_report_pdf", pdf_path)
                    except Exception:
                        LOGGER.exception("住民税納付一覧表の発行に失敗しました")
                        errors.append(
                            "住民税納付一覧表を作成できませんでした。徴収区分・市町村設定と保存先を確認し、もう一度実行してください。"
                        )

            st.session_state["report_batch_downloads"] = {
                "month": selected_month.isoformat(),
                "files": files,
                "notices": notices,
                "errors": errors,
            }

    download_state = st.session_state.get("report_batch_downloads", {})
    if download_state.get("month") != selected_month.isoformat():
        return
    for notice in download_state.get("notices", []):
        st.warning(notice)
    for error in download_state.get("errors", []):
        st.error(error)

    available_files: list[tuple[str, Path]] = []
    for file_info in download_state.get("files", []):
        path = Path(str(file_info.get("path", "")))
        if path.is_file():
            available_files.append((str(file_info.get("label", path.stem)), path))
        else:
            st.error(f"{file_info.get('label', 'PDF')}の保存ファイルが見つかりません。もう一度発行してください。")
    if not available_files:
        return

    st.success(f"{len(available_files)}件のPDFを作成しました。")
    st.subheader("個別ダウンロード")
    for index, (label, path) in enumerate(available_files):
        st.download_button(
            label=f"{label}をダウンロード",
            data=path.read_bytes(),
            file_name=path.name,
            mime="application/pdf",
            key=f"download_report_{selected_month.isoformat()}_{index}_{path.name}",
        )

    try:
        zip_bytes = create_pdf_zip_bytes([path for _, path in available_files])
        zip_name = payroll_documents_zip_filename(
            selected_month,
            str(masters["company"].get("legal_company_name") or masters["company"].get("company_name") or "Demo Company"),
        )
        st.download_button(
            label="正常に作成されたPDFを一括ZIPでダウンロード",
            data=zip_bytes,
            file_name=zip_name,
            mime="application/zip",
            key=f"download_report_zip_{selected_month.isoformat()}",
        )
        st.caption("ZIPは保存せず、今回正常に作成されたPDFだけを直下へ格納します。")
    except Exception:
        LOGGER.exception("一括ZIPの作成に失敗しました")
        st.error("一括ZIPを作成できませんでした。個別PDFは上のボタンからダウンロードできます。")


def unified_issuance_page(masters: dict) -> None:
    individual_tab, batch_tab, history_tab = st.tabs(
        ["個別給与明細・管理CSV", "給与関連一括帳票", "発行履歴"]
    )
    with individual_tab:
        pdf_page()
    with batch_tab:
        report_batch_page(masters)
    with history_tab:
        st.header("発行履歴")
        issued = fetch_issued_files()
        if not issued:
            st.info("統合後の発行履歴はまだありません。")
        else:
            st.dataframe(pd.DataFrame(issued), hide_index=True, use_container_width=True)


def app_exit_controls() -> None:
    st.sidebar.markdown("---")
    if st.session_state.get("app_exit_completed"):
        st.sidebar.success(
            "給与控除・明細発行ナビを終了しました。\n\n"
            "この画面は閉じてください。\n"
            "しばらくするとアプリ本体も停止します。\n\n"
            "再度利用する場合は、デスクトップショートカットから起動してください。"
        )
        return
    if st.sidebar.button("アプリを終了する"):
        st.session_state["confirm_app_exit"] = True
    if st.session_state.get("confirm_app_exit"):
        st.sidebar.warning("アプリを終了します。\n保存していない入力内容は失われます。\n本当によろしいですか？")
        if st.sidebar.button("はい、終了する"):
            st.session_state["app_exit_completed"] = True
            st.session_state["confirm_app_exit"] = False
            st.rerun()
        if st.sidebar.button("キャンセル"):
            st.session_state["confirm_app_exit"] = False
            st.rerun()


def schedule_app_shutdown(delay_seconds: float = 30.0) -> None:
    if st.session_state.get("app_shutdown_scheduled"):
        return
    st.session_state["app_shutdown_scheduled"] = True

    def shutdown_current_process() -> None:
        time.sleep(delay_seconds)
        os._exit(0)

    threading.Thread(target=shutdown_current_process, daemon=True).start()


def main() -> None:
    header()
    masters = load_all_masters()
    if "pending_main_page" in st.session_state:
        st.session_state["main_page"] = st.session_state.pop("pending_main_page")
    sidebar_logo()
    st.sidebar.markdown(
        "**Demo workflow / デモ手順**\n\n"
        "1. Upload demo tax notice\n"
        "2. Review two employee records\n"
        "3. Confirm monthly tax amounts\n"
        "4. Run June 2026 payroll\n"
        "5. Issue payslip PDF and accounting CSV"
    )
    page = st.sidebar.radio(
        "メニュー",
        [
            "アプリ利用説明書",
            "給与計算入力",
            "住民税通知書取込・確認",
            "給与明細・CSV発行",
            "計算履歴",
            "発行状況確認",
        ],
        key="main_page",
    )
    app_exit_controls()
    if st.session_state.get("app_exit_completed"):
        schedule_app_shutdown()
        st.success("給与控除・明細発行ナビを終了しました。")
        st.info("この画面は閉じてください。\nしばらくするとアプリ本体も停止します。\n\n再度利用する場合は、デスクトップショートカットから起動してください。")
        st.stop()
    if page == "アプリ利用説明書":
        guide_page()
    elif page == "給与計算入力":
        payroll_page(masters)
    elif page == "住民税通知書取込・確認":
        resident_tax_notice_page(masters)
    elif page == "給与明細・CSV発行":
        unified_issuance_page(masters)
    elif page == "計算履歴":
        history_page()
    else:
        issuance_status_page(masters)


if __name__ == "__main__":
    main()
