from __future__ import annotations

from calendar import monthrange
from collections import defaultdict
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

from payroll_core import PayrollResult, get_resident_tax_collection_type, round_yen
from payslip_pdf import _register_fonts, display_employee_name, draw_text, line, rect


BASE_DIR = Path(__file__).resolve().parent
REPORT_DIR = BASE_DIR / "output" / "reports"


class ReportConfigurationError(ValueError):
    """正確な事業所負担額の算出に必要な設定が不足している場合の例外。"""


def payment_month(results: list[PayrollResult]) -> date:
    if not results:
        raise ValueError("帳票対象の計算履歴がありません。")
    months = {date(result.payment_date.year, result.payment_date.month, 1) for result in results}
    if len(months) != 1:
        raise ValueError("複数の支給年月が混在しています。")
    return months.pop()


def report_filename(results: list[PayrollResult], report_name: str) -> str:
    target = payment_month(results)
    return f"{target.year}年{target.month}月分_{report_name}.pdf"


def _company_code(company: dict[str, Any]) -> str:
    value = str(company.get("company_code", "")).strip()
    if not value:
        raise ValueError("会社コードが設定されていません。")
    return value.zfill(6)


def _company_name(company: dict[str, Any]) -> str:
    return str(company.get("legal_company_name") or company.get("company_name") or "").removesuffix("様")


def employee_code_for_name(employee_name: str, employees: dict[str, Any]) -> str:
    value = str(employees.get(employee_name, {}).get("employee_id", "")).strip()
    return value.zfill(6) if value else "000000"


def _employee_code(result: PayrollResult, employees: dict[str, Any]) -> str:
    return employee_code_for_name(result.employee_name, employees)


def _amount(value: int) -> str:
    amount = int(value or 0)
    return f"{amount:,}" if amount else ""


def _created_label(created_on: date) -> str:
    return f"{created_on.year:04d}年{created_on.month:02d}月{created_on.day:02d}日作成"


def _draw_filled_rect(c: canvas.Canvas, x: float, y: float, w: float, h: float, gray: float) -> None:
    c.saveState()
    c.setFillColorRGB(gray, gray, gray)
    c.rect(x, y, w, h, stroke=0, fill=1)
    c.restoreState()


def _draw_report_header(
    c: canvas.Canvas,
    *,
    title: str,
    target: date,
    company: dict[str, Any],
    created_on: date,
    page_w: float,
    title_y: float,
    show_created: bool = True,
) -> None:
    font = _register_fonts()
    draw_text(c, title, page_w / 2, title_y, font, 17.5, "center")
    draw_text(c, f"{target.year:04d}年{target.month:02d}月分 給与", 30 * mm, title_y + 3, font, 9.5)
    draw_text(c, "1頁", page_w - 28 * mm, title_y + 3, font, 9.5, "right")
    draw_text(c, f"{_company_code(company)}　{_company_name(company)}", 30 * mm, title_y - 12, font, 9.5)
    if show_created:
        draw_text(c, _created_label(created_on), page_w - 28 * mm, title_y - 12, font, 9.5, "right")


def create_payroll_summary_pdf(
    results: list[PayrollResult],
    company: dict[str, Any],
    employees: dict[str, Any],
    output_dir: Path = REPORT_DIR,
    created_on: date | None = None,
) -> Path:
    """指定見本の固定座標で、5社員枠の給与支給・控除一覧表を作る。"""
    target = payment_month(results)
    sorted_results = sorted(results, key=lambda result: _employee_code(result, employees))
    if len(sorted_results) > 5:
        raise ValueError("給与支給・控除一覧表は1ページにつき5名まで発行できます。")

    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / report_filename(results, "給与支給控除一覧表")
    font = _register_fonts()
    c = canvas.Canvas(str(path), pagesize=A4)
    page_w, page_h = A4
    c.setTitle(path.name)

    closing_day = monthrange(sorted_results[0].payroll_month.year, sorted_results[0].payroll_month.month)[1]
    closing_date = date(
        sorted_results[0].payroll_month.year,
        sorted_results[0].payroll_month.month,
        closing_day,
    )
    payment_date = sorted_results[0].payment_date
    draw_text(c, f"{target.year:04d}年{target.month:02d}月分", 52.0, 810.9, font, 8.5)
    draw_text(c, f"{_company_code(company)}　{_company_name(company)}", 52.0, 797.0, font, 8.5)
    draw_text(c, "給与 支給・控除一覧表", page_w / 2, 812.0, font, 13.0, "center")
    draw_text(c, "1頁", 556.0, 810.9, font, 8.5, "right")
    draw_text(
        c,
        f"締日：{closing_date.month:02d}/{closing_date.day:02d} 支給日：{payment_date.month:02d}/{payment_date.day:02d}",
        556.0,
        795.6,
        font,
        8.5,
        "right",
    )

    # reference/給与支給控除一覧表.pdf を200dpiで描画し、罫線座標を計測した値。
    point_per_pixel = 72.0 / 200.0
    x_pixels = [132, 401, 632, 864, 1095, 1326, 1557]
    x_edges = [pixel * point_per_pixel for pixel in x_pixels]
    y_pixels = [
        133, 243,
        277, 311, 345, 379, 413, 447,
        481, 515, 549, 583, 616, 650, 684, 718, 752, 786,
        820, 854, 888, 922, 956, 990, 1023, 1057, 1090, 1124,
        1158, 1192, 1226, 1260, 1293, 1327, 1361, 1395, 1429,
        1463, 1497, 1530, 1563, 1598, 1632, 1667, 1701, 1734,
    ]
    y_edges = [page_h - pixel * point_per_pixel for pixel in y_pixels]
    table_left, table_right = x_edges[0], x_edges[-1]
    table_top, table_bottom = y_edges[0], y_edges[-1]

    pay_labels = [
        "基本給", "役員報酬", "現場手当", "皆勤手当", "休日出勤手当",
        "夜間手当", "半徹手当", "特別手当", "課税通勤手当", "普通残業手当",
        "深夜残業手当", "時間外手当", "時間外手当（前月調整）", "欠勤控除", "遅早控除",
        "所定休日出勤手当", "課税支給合計", "非課税通勤手当", "非課税支給合計", "支給合計",
    ]
    deduction_labels = [
        "健康保険", "介護保険", "子ども子育て支援金", "厚生年金", "雇用保険",
        "調整保険", "社会保険合計", "課税対象額", "所得税", "住民税",
        "年調精算額", "その他控除合計", "控除合計", "差引支給額", "現金支給額",
        "振込支給額", "税制扶養数", "税表区分",
    ]
    attendance_labels = [
        ("出勤日数", "時間外法"),
        ("有給日数", "休業日数"),
        ("就労時間", "給与控除"),
        ("時間外平", "有給残"),
        ("時間外深", "所定休日"),
        ("時間外所", "所定休日"),
    ]

    def result_values(result: PayrollResult) -> dict[str, str]:
        employee = employees.get(result.employee_name, {})
        employment_text = (
            _amount(result.employment_insurance)
            if bool(employee.get("employment_insurance", False))
            else "非加入"
        )
        values = {
            "基本給": _amount(result.basic_salary),
            "役員報酬": _amount(result.executive_compensation),
            "現場手当": _amount(result.site_allowance),
            "皆勤手当": _amount(result.attendance_allowance),
            "休日出勤手当": _amount(result.holiday_work_allowance),
            "夜間手当": _amount(result.night_allowance),
            "半徹手当": _amount(result.half_night_allowance),
            "課税支給合計": _amount(result.gross_pay),
            "支給合計": _amount(result.gross_pay),
            "健康保険": _amount(result.health_insurance),
            "介護保険": _amount(result.care_insurance),
            "子ども子育て支援金": _amount(result.child_support),
            "厚生年金": _amount(result.pension_insurance),
            "雇用保険": employment_text,
            "社会保険合計": _amount(result.social_insurance_total),
            "課税対象額": _amount(result.withholding_tax_base),
            "所得税": _amount(result.withholding_income_tax),
            "住民税": _amount(result.resident_tax),
            "その他控除合計": _amount(result.other_deduction),
            "控除合計": _amount(result.total_deductions),
            "差引支給額": _amount(result.net_pay),
            "振込支給額": _amount(result.net_pay),
            "税制扶養数": str(int(result.dependents)) if int(result.dependents or 0) else "",
            "税表区分": str(result.tax_class or ""),
        }
        return values

    slot_values = [result_values(result) for result in sorted_results]
    slot_values.extend({} for _ in range(5 - len(slot_values)))

    # 見本と同じ3行だけを薄いグレーで網掛けする。
    gray_rows = [(26, 27), (39, 40), (40, 41)]
    for top_index, bottom_index in gray_rows:
        _draw_filled_rect(
            c,
            table_left,
            y_edges[bottom_index],
            table_right - table_left,
            y_edges[top_index] - y_edges[bottom_index],
            0.86,
        )

    def black_line(x1: float, y1: float, x2: float, y2: float, width: float) -> None:
        c.setStrokeColorRGB(0, 0, 0)
        c.setLineWidth(width)
        c.line(x1, y1, x2, y2)

    # 横罫線。外枠と主要区切りは見本に合わせて太線にする。
    major_horizontal = {0, 1, 7, 24, 27, 34, 35, 39, 40, 41, 45}
    for index, y in enumerate(y_edges):
        width = 1.25 if index in {0, 45} else (1.05 if index in major_horizontal else 0.52)
        black_line(table_left, y, table_right, y, width)

    # 社員枠5列は表全体を通し、勤怠欄だけ各枠を左右2セルに分割する。
    for index, x in enumerate(x_edges):
        width = 1.25 if index in {0, len(x_edges) - 1} else 0.62
        black_line(x, table_top, x, table_bottom, width)
    attendance_midpoints = [(x_edges[index] + x_edges[index + 1]) / 2 for index in range(len(x_edges) - 1)]
    for midpoint in attendance_midpoints:
        black_line(midpoint, y_edges[1], midpoint, y_edges[7], 0.52)

    # 社員見出し。
    draw_text(c, "所　属", (x_edges[0] + x_edges[1]) / 2, table_top - 13.0, font, 8.6, "center")
    draw_text(c, "社　員", (x_edges[0] + x_edges[1]) / 2, table_top - 34.4, font, 8.6, "center")
    for slot_index in range(5):
        if slot_index >= len(sorted_results):
            continue
        result = sorted_results[slot_index]
        cell_left = x_edges[slot_index + 1]
        draw_text(c, _employee_code(result, employees), cell_left + 2.2, table_top - 28.1, font, 7.7)
        draw_text(c, display_employee_name(result.employee_name), cell_left + 2.2, table_top - 37.0, font, 7.8)

    # 勤怠6行。確実に対応する勤務日数だけを出勤日数へ表示する。
    for row_index, (left_label, right_label) in enumerate(attendance_labels):
        row_top = y_edges[1 + row_index]
        row_bottom = y_edges[2 + row_index]
        baseline = row_bottom + (row_top - row_bottom - 7.0) / 2
        label_mid = attendance_midpoints[0]
        draw_text(c, left_label, table_left + 2.0, baseline, font, 6.9)
        draw_text(c, right_label, label_mid + 2.0, baseline, font, 6.9)
        if row_index == 0:
            for slot_index, result in enumerate(sorted_results):
                work_days = int(result.work_days or 0)
                if work_days:
                    midpoint = attendance_midpoints[slot_index + 1]
                    draw_text(c, f"{work_days:.2f}", midpoint - 2.0, baseline, font, 7.0, "right")

    # 支給20行、控除18行。社員枠3～5は空欄のまま残す。
    for row_index, label in enumerate(pay_labels):
        edge_index = 7 + row_index
        row_top = y_edges[edge_index]
        row_bottom = y_edges[edge_index + 1]
        baseline = row_bottom + (row_top - row_bottom - 7.0) / 2
        draw_text(c, label, table_left + 2.0, baseline, font, 6.9)
        for slot_index in range(5):
            value = slot_values[slot_index].get(label, "")
            if value:
                draw_text(c, value, x_edges[slot_index + 2] - 2.0, baseline, font, 7.0, "right")

    centered_deduction_labels = {"税表区分"}
    for row_index, label in enumerate(deduction_labels):
        edge_index = 27 + row_index
        row_top = y_edges[edge_index]
        row_bottom = y_edges[edge_index + 1]
        baseline = row_bottom + (row_top - row_bottom - 7.0) / 2
        draw_text(c, label, table_left + 2.0, baseline, font, 6.9)
        for slot_index in range(5):
            value = slot_values[slot_index].get(label, "")
            if not value:
                continue
            is_numeric_value = value.replace(",", "").lstrip("-").isdigit()
            if label in centered_deduction_labels or (label == "雇用保険" and not is_numeric_value):
                center_x = (x_edges[slot_index + 1] + x_edges[slot_index + 2]) / 2
                draw_text(c, value, center_x, baseline, font, 7.0, "center")
            else:
                draw_text(c, value, x_edges[slot_index + 2] - 2.0, baseline, font, 7.0, "right")

    c.showPage()
    c.save()
    return path


def employer_insurance_values(
    result: PayrollResult,
    company: dict[str, Any],
    employees: dict[str, Any],
    rates: dict[str, Any],
) -> dict[str, int]:
    """保存済み標準報酬・賃金と年度別の事業主率から会社負担分を算出する。"""
    try:
        year = result.social_insurance_year
        employee = employees[result.employee_name]
        region = company["region"]
        business_type = company["employment_insurance_type"]
        health_rates = rates["health_insurance"][year][region]
        care_rates = rates["care_insurance"][year]
        support_rates = rates["child_support"][year]
        pension_rates = rates["pension"][year]
        employer_rates = rates["employer_insurance"][year]
        employment_rate = Decimal(str(employer_rates["employment_insurance"][business_type]))
        child_contribution_rate = Decimal(str(employer_rates["child_contribution"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise ReportConfigurationError("事業所負担保険料の計算設定が未完了です。") from exc

    def employer_share(base: int, rate_row: dict[str, Any]) -> int:
        full_rate = Decimal(str(rate_row["full_rate"]))
        employee_rate = Decimal(str(rate_row["employee_rate"]))
        if full_rate < employee_rate:
            raise ReportConfigurationError("事業所負担保険料の料率設定が不正です。")
        return round_yen(Decimal(int(base or 0)) * (full_rate - employee_rate))

    has_health = (
        bool(employee.get("social_insurance"))
        and bool(employee.get("health_insurance"))
        and bool(int(result.health_insurance_deduct_enabled or 0))
    )
    has_pension = bool(employee.get("social_insurance")) and bool(employee.get("pension_insurance"))
    has_employment = bool(employee.get("employment_insurance"))

    health = employer_share(result.standard_monthly_health, health_rates) if has_health else 0
    care = employer_share(result.standard_monthly_health, care_rates) if has_health and 40 <= int(result.age) < 65 else 0
    child_support = employer_share(result.standard_monthly_health, support_rates) if has_health else 0
    pension = employer_share(result.standard_monthly_pension, pension_rates) if has_pension else 0
    pension_fund = 0
    employment = round_yen(Decimal(int(result.gross_pay or 0)) * employment_rate) if has_employment else 0
    child_contribution = (
        round_yen(Decimal(int(result.standard_monthly_pension or 0)) * child_contribution_rate)
        if has_pension
        else 0
    )
    total = health + care + child_support + pension + pension_fund + employment + child_contribution
    return {
        "health": health,
        "care": care,
        "child_support": child_support,
        "pension": pension,
        "pension_fund": pension_fund,
        "employment": employment,
        "child_contribution": child_contribution,
        "total": total,
    }


def create_employer_insurance_pdf(
    results: list[PayrollResult],
    company: dict[str, Any],
    employees: dict[str, Any],
    rates: dict[str, Any],
    output_dir: Path = REPORT_DIR,
    created_on: date | None = None,
) -> Path:
    """指定見本の固定座標で、事業所負担保険料一覧表を作る。"""
    target = payment_month(results)
    created_on = created_on or date.today()
    sorted_results = sorted(results, key=lambda result: _employee_code(result, employees))
    if len(sorted_results) > 3:
        raise ValueError("事業所負担保険料一覧表は1ページにつき3名まで発行できます。")
    values = [employer_insurance_values(result, company, employees, rates) for result in sorted_results]
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / report_filename(results, "事業所負担保険料一覧表")
    font = _register_fonts()
    # 見本PDFの実測ページサイズ（A4横）と表座標をそのまま使用する。
    page_size = (841.61, 595.56)
    c = canvas.Canvas(str(path), pagesize=page_size)
    _, page_h = page_size
    c.setTitle(path.name)

    draw_text(c, f"{target.year:04d}年{target.month:02d}月分 給与", 42.52, 548.28, font, 8.75)
    draw_text(c, "事業所負担保険料一覧表", 404.72, 548.04, font, 16.0, "center")
    draw_text(c, _created_label(created_on), 733.99, 548.28, font, 8.75, "right")
    draw_text(c, "1頁", 790.85, 548.04, font, 11.5, "right")
    draw_text(c, _company_code(company), 42.52, 528.43, font, 8.75)
    draw_text(c, _company_name(company), 85.04, 528.43, font, 8.75)

    x_edges = [39.65, 229.57, 300.44, 371.31, 442.18, 513.05, 583.91, 654.78, 725.65, 799.35]
    table_top = page_h - 79.37
    header_bottom = page_h - 93.54
    blank_bottom = page_h - 107.91
    detail_bottom = page_h - 150.43
    table_bottom = page_h - 164.60
    headers = [
        "社　員　名",
        "健康保険",
        "介護保険",
        "子ども・子育て支援金",
        "厚生年金",
        "厚生年金基金",
        "雇用保険",
        "子ども・子育て拠出金",
        "保険料合計",
    ]

    c.saveState()
    c.setStrokeColorRGB(0.08, 0.08, 0.08)
    c.setLineWidth(0.57)
    for y in (table_top, header_bottom, blank_bottom, detail_bottom, table_bottom):
        c.line(x_edges[0], y, x_edges[-1], y)
    for x in (x_edges[0], x_edges[-1]):
        c.line(x, table_bottom, x, table_top)
    for x in x_edges[1:-1]:
        c.line(x, header_bottom, x, table_top)
        c.line(x, table_bottom, x, blank_bottom)
    c.restoreState()

    for index, header in enumerate(headers):
        size = 6.5 if index in (3, 7) else 10.0
        baseline = 506.64 if index in (3, 7) else 505.31
        draw_text(c, header, (x_edges[index] + x_edges[index + 1]) / 2, baseline, font, size, "center")

    keys = ["health", "care", "child_support", "pension", "pension_fund", "employment", "child_contribution", "total"]
    numeric_right_padding = 2.5
    employee_baselines = [475.23, 461.05, 446.88]
    for baseline, result, row_values in zip(employee_baselines, sorted_results, values):
        draw_text(c, _employee_code(result, employees), 42.06, baseline, font, 8.75)
        draw_text(c, display_employee_name(result.employee_name), 128.80, baseline, font, 8.75)
        for column_index, key in enumerate(keys, start=1):
            draw_text(
                c,
                _amount(row_values[key]),
                x_edges[column_index + 1] - numeric_right_padding,
                baseline,
                font,
                8.75,
                "right",
            )

    total_baseline = 433.28
    draw_text(c, "全社計", 48.78, total_baseline, font, 8.75)
    draw_text(c, f"{len(sorted_results)}人", x_edges[1] - 1.58, total_baseline, font, 8.75, "right")
    for column_index, key in enumerate(keys, start=1):
        total = sum(row[key] for row in values)
        draw_text(
            c,
            _amount(total),
            x_edges[column_index + 1] - numeric_right_padding,
            total_baseline,
            font,
            8.75,
            "right",
        )

    c.showPage()
    c.save()
    return path


def resident_tax_targets(
    results: list[PayrollResult], employees: dict[str, Any]
) -> list[dict[str, Any]]:
    targets: list[dict[str, Any]] = []
    for result in results:
        employee = employees.get(result.employee_name, {})
        if get_resident_tax_collection_type(employee, result.payment_date) != "特別徴収":
            continue
        if int(result.resident_tax or 0) <= 0:
            continue
        targets.append(
            {
                "municipality_code": str(employee.get("municipality_code", "")).zfill(6),
                "municipality_name": str(employee.get("municipality_name", "")),
                "employee_code": str(employee.get("employee_id", "")).zfill(6),
                "employee_name": result.employee_name,
                "resident_tax": int(result.resident_tax),
            }
        )
    return sorted(targets, key=lambda row: (row["municipality_code"], row["employee_code"]))


def create_resident_tax_pdf(
    results: list[PayrollResult],
    company: dict[str, Any],
    employees: dict[str, Any],
    output_dir: Path = REPORT_DIR,
    created_on: date | None = None,
) -> Path | None:
    """指定見本の固定座標で、特別徴収対象者の住民税納付一覧表を作る。"""
    target = payment_month(results)
    targets = resident_tax_targets(results, employees)
    if not targets:
        return None
    created_on = created_on or date.today()
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / report_filename(results, "住民税納付一覧表")
    font = _register_fonts()
    # reference/住民税納付一覧表.pdf の実測ページサイズと固定座標。
    page_size = (595.56, 841.61)
    c = canvas.Canvas(str(path), pagesize=page_size)
    c.setTitle(path.name)

    draw_text(c, "住民税納付一覧表", 293.39, 790.52, font, 16.0, "center")
    draw_text(c, f"{target.year:04d}年{target.month:02d}月分", 45.35, 790.24, font, 8.25)
    draw_text(c, "給与", 103.12, 790.24, font, 8.25)
    draw_text(c, "1頁", 559.92, 795.75, font, 10.0, "right")
    draw_text(c, _company_code(company), 43.94, 771.75, font, 8.25)
    draw_text(c, _company_name(company), 84.20, 771.75, font, 8.25)
    draw_text(c, _created_label(created_on), 538.46, 773.80, font, 8.25, "right")

    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in targets:
        grouped[(row["municipality_code"], row["municipality_name"])].append(row)
    table_rows: list[dict[str, Any]] = []
    for (municipality_code, municipality_name), rows in grouped.items():
        table_rows.extend(rows)
        table_rows.append(
            {
                "municipality_code": municipality_code,
                "municipality_name": f"{municipality_name}計",
                "employee_code": "",
                "employee_name": f"{len(rows)}人",
                "resident_tax": sum(row["resident_tax"] for row in rows),
                "subtotal": True,
            }
        )
    table_rows.append(
        {
            "municipality_code": f"{len(grouped)}件",
            "municipality_name": "全社合計",
            "employee_code": "",
            "employee_name": f"{len(targets)}人",
            "resident_tax": sum(row["resident_tax"] for row in targets),
            "grand_total": True,
        }
    )

    x_edges = [38.27, 94.96, 263.62, 320.31, 490.39, 568.35]
    table_top_from_page = 76.54
    row_h = 14.175
    headers = ["コード", "市町村名", "コード", "社員名", "住民税"]
    table_top = page_size[1] - table_top_from_page
    table_bottom = table_top - row_h * (len(table_rows) + 1)

    c.saveState()
    c.setStrokeColorRGB(0.08, 0.08, 0.08)
    c.setLineWidth(0.85)
    for index in range(len(table_rows) + 2):
        y = table_top - row_h * index
        c.line(x_edges[0], y, x_edges[-1], y)
    for x in x_edges:
        c.line(x, table_bottom, x, table_top)
    c.restoreState()

    header_baseline = 753.97
    for index, header in enumerate(headers):
        draw_text(c, header, (x_edges[index] + x_edges[index + 1]) / 2, header_baseline, font, 10.0, "center")

    for index, row in enumerate(table_rows):
        baseline = 739.06 - row_h * index
        draw_text(c, row["municipality_code"], x_edges[0] + 9.78, baseline, font, 10.0)
        draw_text(c, row["municipality_name"], x_edges[1] + 1.42, baseline, font, 10.0)
        if row["employee_code"]:
            draw_text(c, row["employee_code"], x_edges[2] + 9.82, baseline, font, 10.0)
        if row.get("subtotal") or row.get("grand_total"):
            draw_text(c, row["employee_name"], 469.13, baseline, font, 10.0, "right")
        elif row["employee_name"]:
            draw_text(c, display_employee_name(row["employee_name"]), x_edges[3] + 2.84, baseline, font, 10.0)
        draw_text(c, _amount(row["resident_tax"]), x_edges[-1] - 2.5, baseline, font, 11.0, "right")

    c.showPage()
    c.save()
    return path
