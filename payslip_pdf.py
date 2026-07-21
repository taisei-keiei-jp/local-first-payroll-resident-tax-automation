from __future__ import annotations

from calendar import monthrange
from datetime import date
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

from payroll_core import PayrollResult, load_json, wareki_date


BASE_DIR = Path(__file__).resolve().parent
PAYSLIP_DIR = BASE_DIR / "output" / "payslips"
LOGO_WATERMARK_PATH = BASE_DIR / "assets" / "demo_watermark.png"
LOGO_WATERMARK_WIDTH = 128

BLACK = (0, 0, 0)
GRAY = (0.42, 0.42, 0.42)


def _register_fonts() -> str:
    font_name = "TMA-YuMincho"
    font_path = Path(r"C:\Windows\Fonts\yumin.ttf")
    fallback_path = Path(r"C:\Windows\Fonts\BIZ-UDMinchoM.ttc")
    fallback_gothic_path = Path(r"C:\Windows\Fonts\meiryo.ttc")
    try:
        if font_path.exists():
            pdfmetrics.registerFont(TTFont(font_name, str(font_path)))
        elif fallback_path.exists():
            font_name = "TMA-BizUDMincho"
            pdfmetrics.registerFont(TTFont(font_name, str(fallback_path), subfontIndex=0))
        elif fallback_gothic_path.exists():
            font_name = "TMA-Meiryo"
            pdfmetrics.registerFont(TTFont(font_name, str(fallback_gothic_path), subfontIndex=0))
        else:
            font_name = "HeiseiMin-W3"
            pdfmetrics.registerFont(UnicodeCIDFont(font_name))
    except Exception:
        font_name = "HeiseiMin-W3"
        try:
            pdfmetrics.registerFont(UnicodeCIDFont(font_name))
        except Exception:
            pass
    return font_name


def amount(value: int) -> str:
    return f"{int(value or 0):,}"


def employee_code(employee_name: str) -> str:
    employee = load_json("employee_master.json").get(employee_name, {})
    return str(employee.get("employee_id", "")).zfill(6)


def display_employee_name(employee_name: str) -> str:
    clean_name = str(employee_name or "").removesuffix("様").strip()
    employee = load_json("employee_master.json").get(clean_name, {})
    return str(employee.get("display_name") or clean_name)


def closing_date_label(payroll_month: date) -> str:
    last_day = monthrange(payroll_month.year, payroll_month.month)[1]
    return wareki_date(date(payroll_month.year, payroll_month.month, last_day))


def payslip_payment_month_label(payment_date: date) -> str:
    return f"令和{payment_date.year - 2018}年{payment_date.month}月分"


def reiwa_short_year(d: date) -> int:
    return d.year - 2018


def reiwa_short_year_month(d: date) -> str:
    return f"R{reiwa_short_year(d):02d}.{d.month:02d}"


def payslip_document_label(result: PayrollResult) -> str:
    return "役員報酬" if result.role == "役員" or int(result.executive_compensation or 0) > 0 else "給与"


def payslip_document_filename_label(result: PayrollResult) -> str:
    return f"{payslip_document_label(result)}明細"


def build_payslip_filename_base(result: PayrollResult, document_name: str = "給与明細") -> str:
    closing_part = reiwa_short_year_month(result.payroll_month)
    payment_part = reiwa_short_year_month(result.payment_date)
    return f"{payment_part}月分({closing_part}〆{payment_part}支給){document_name}_{result.employee_name}"


def payslip_filename(result: PayrollResult) -> str:
    return f"{build_payslip_filename_base(result, payslip_document_filename_label(result))}.pdf"


def payslip_pdf_filename(result: PayrollResult) -> str:
    return f"{build_payslip_filename_base(result, payslip_document_filename_label(result))}.pdf"


def set_font(c: canvas.Canvas, font: str, size: float) -> None:
    c.setFont(font, size)
    c.setFillColorRGB(*BLACK)


def text_width(text: str, font: str, size: float) -> float:
    return pdfmetrics.stringWidth(str(text), font, size)


def draw_text(
    c: canvas.Canvas,
    text: str,
    x: float,
    y: float,
    font: str,
    size: float = 8.5,
    align: str = "left",
) -> None:
    set_font(c, font, size)
    value = str(text)
    if align == "right":
        c.drawString(x - text_width(value, font, size), y, value)
    elif align == "center":
        c.drawString(x - text_width(value, font, size) / 2, y, value)
    else:
        c.drawString(x, y, value)


def wrap_pdf_text(text: str, font: str, size: float, max_width: float) -> list[str]:
    wrapped: list[str] = []
    for raw_line in str(text).replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if not raw_line:
            wrapped.append("")
            continue
        current = ""
        for char in raw_line:
            candidate = current + char
            if current and text_width(candidate, font, size) > max_width:
                wrapped.append(current)
                current = char
            else:
                current = candidate
        wrapped.append(current)
    return wrapped


def draw_multiline_text(
    c: canvas.Canvas,
    text: str,
    x: float,
    top: float,
    font: str,
    size: float,
    max_width: float,
    max_height: float,
) -> None:
    line_height = size + 3.0
    max_lines = max(1, int(max_height // line_height))
    lines = wrap_pdf_text(text, font, size, max_width)[:max_lines]
    for index, line_text in enumerate(lines):
        draw_text(c, line_text, x, top - size - index * line_height, font, size, "left")


def line(c: canvas.Canvas, x1: float, y1: float, x2: float, y2: float, width: float = 0.16) -> None:
    c.setStrokeColorRGB(*GRAY)
    c.setLineWidth(width)
    c.line(x1, y1, x2, y2)


def rect(
    c: canvas.Canvas,
    x: float,
    y: float,
    w: float,
    h: float,
    width: float = 0.18,
    radius: float = 0,
) -> None:
    c.setStrokeColorRGB(*GRAY)
    c.setLineWidth(width)
    if radius:
        c.roundRect(x, y, w, h, radius, stroke=1, fill=0)
    else:
        c.rect(x, y, w, h, stroke=1, fill=0)


def draw_fixed_table(
    c: canvas.Canvas,
    *,
    x: float,
    top: float,
    w: float,
    h: float,
    title: str,
    headers: list[str],
    rows: list[list[str]],
    col_widths: list[float],
    font: str,
    total: list[str] | None = None,
    title_size: float = 9.2,
    body_size: float = 8.7,
    row_h: float = 15.2,
    show_blank_row_lines: bool = False,
    right_align_cols: set[int] | None = None,
) -> None:
    right_align_cols = right_align_cols or set()
    title_h = 17
    header_h = 16
    total_h = 17 if total else 0
    bottom = top - h
    header_top = top - title_h
    body_top = header_top - header_h
    total_top = bottom + total_h

    rect(c, x, bottom, w, h, radius=0.5)
    line(c, x, header_top, x + w, header_top)
    line(c, x, body_top, x + w, body_top)
    if total:
        line(c, x, total_top, x + w, total_top, width=0.18)

    current_x = x
    for col_w in col_widths[:-1]:
        current_x += col_w
        line(c, current_x, header_top, current_x, bottom)

    draw_text(c, title, x + w / 2, top - title_h / 2 - title_size / 3, font, title_size, "center")

    current_x = x
    for header, col_w in zip(headers, col_widths):
        draw_text(c, header, current_x + col_w / 2, header_top - header_h / 2 - body_size / 3, font, body_size, "center")
        current_x += col_w

    body_h = h - title_h - header_h - total_h
    max_rows = max(1, int(body_h // row_h))
    visible_row_lines = max_rows if show_blank_row_lines else 1
    for i in range(1, visible_row_lines):
        y = body_top - i * row_h
        if y > total_top:
            line(c, x, y, x + w, y, width=0.16)

    for row_index in range(min(len(rows), max_rows)):
        y = body_top - row_index * row_h - row_h / 2 - body_size / 3
        current_x = x
        for col_index, (value, col_w) in enumerate(zip(rows[row_index], col_widths)):
            if col_index == len(col_widths) - 1 or col_index in right_align_cols:
                draw_text(c, value, current_x + col_w - 7, y, font, body_size, "right")
            else:
                draw_text(c, value, current_x + 6.5, y, font, body_size, "left")
            current_x += col_w

    if total:
        y = bottom + total_h / 2 - body_size / 3
        draw_text(c, total[0], x + 6.5, y, font, body_size, "left")
        draw_text(c, total[1], x + w - 7, y, font, body_size, "right")


def draw_simple_grid(
    c: canvas.Canvas,
    *,
    x: float,
    top: float,
    w: float,
    row_h: float,
    rows: list[list[str]],
    col_widths: list[float],
    font: str,
    size: float = 8.5,
    bold_last: bool = False,
    center_first_row: bool = False,
    center_cells: set[tuple[int, int]] | None = None,
) -> None:
    center_cells = center_cells or set()
    h = row_h * len(rows)
    bottom = top - h
    rect(c, x, bottom, w, h, radius=0.5)
    for i in range(1, len(rows)):
        line(c, x, top - i * row_h, x + w, top - i * row_h)
    current_x = x
    for col_w in col_widths[:-1]:
        current_x += col_w
        line(c, current_x, top, current_x, bottom)

    for row_index, row in enumerate(rows):
        text_size = size + 1 if bold_last and row_index == len(rows) - 1 else size
        y = top - row_index * row_h - row_h / 2 - text_size / 3
        current_x = x
        for col_index, (value, col_w) in enumerate(zip(row, col_widths)):
            if (row_index, col_index) in center_cells or (center_first_row and row_index == 0):
                draw_text(c, value, current_x + col_w / 2, y, font, text_size, "center")
            elif col_index == len(col_widths) - 1:
                draw_text(c, value, current_x + col_w - 5, y, font, text_size, "right")
            else:
                draw_text(c, value, current_x + 5, y, font, text_size, "left")
            current_x += col_w


def nonzero_rows(rows: list[tuple[str, int]], always_show: set[str] | None = None) -> list[list[str]]:
    always_show = always_show or set()
    return [[label, amount(value)] for label, value in rows if value or label in always_show]


def allowance_label(label: str, work_days: int) -> str:
    return f"{label}（{int(work_days)}日）" if int(work_days or 0) > 0 else label


def draw_logo_watermark(c: canvas.Canvas, page_w: float, page_h: float) -> None:
    if not LOGO_WATERMARK_PATH.exists():
        return
    logo_w = LOGO_WATERMARK_WIDTH
    logo_h = logo_w * 360 / 1103
    x = page_w - logo_w - 42
    y = page_h - logo_h - 38
    try:
        c.saveState()
        c.drawImage(
            str(LOGO_WATERMARK_PATH),
            x,
            y,
            width=logo_w,
            height=logo_h,
            mask="auto",
            preserveAspectRatio=True,
            anchor="c",
        )
        c.restoreState()
    except Exception:
        try:
            c.restoreState()
        except Exception:
            pass


def create_payslip_pdf(result: PayrollResult, output_dir: Path = PAYSLIP_DIR) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    font = _register_fonts()
    filename = payslip_pdf_filename(result)
    path = output_dir / filename

    c = canvas.Canvas(str(path), pagesize=A4)
    page_w, page_h = A4
    c.setTitle(filename)
    draw_logo_watermark(c, page_w, page_h)

    code = employee_code(result.employee_name)
    employee_name_label = display_employee_name(result.employee_name)
    employee_name_with_honorific = employee_name_label if employee_name_label.endswith("様") else f"{employee_name_label}　　様"
    closing_label = closing_date_label(result.payroll_month)
    payment_month_label = payslip_payment_month_label(result.payment_date)
    document_label = payslip_document_label(result)

    # 宛名枠
    address_x = 66
    address_y = 708
    address_w = 226
    address_h = 108
    rect(c, address_x, address_y, address_w, address_h, width=0.16, radius=1.4)
    draw_text(c, f"{payment_month_label}　{document_label}", address_x + address_w / 2, address_y + address_h - 19, font, 10.4, "center")
    name_line_y = address_y + 35
    line(c, address_x + 15, name_line_y, address_x + address_w - 15, name_line_y, width=0.14)
    draw_text(c, code, address_x + 17, name_line_y + 8, font, 8.5, "left")
    draw_text(c, employee_name_label, address_x + 122, name_line_y + 8, font, 11.0, "center")
    draw_text(c, "様", address_x + address_w - 25, name_line_y + 8, font, 11.0, "center")
    company_name = str(load_json("company_config.json").get("legal_company_name", ""))
    draw_text(c, company_name, address_x + 17, address_y + 16, font, 9.6, "left")

    # タイトルエリア
    title_y = address_y - 65
    draw_text(c, payslip_document_filename_label(result), 30, title_y, font, 15.4, "left")
    draw_text(c, f"{payment_month_label}　{document_label}", 258, title_y + 1, font, 10.2, "left")
    draw_text(c, f"支給日　{result.payment_date_label}", page_w - 58, title_y + 7, font, 9.8, "right")
    draw_text(c, f"締日　{closing_label}", page_w - 58, title_y - 6, font, 9.8, "right")
    draw_text(c, f"扶養人数　{int(result.dependents or 0)}人", page_w - 58, title_y - 19, font, 9.8, "right")

    # 社員情報小枠
    emp_x = 24
    emp_top = title_y - 24
    draw_simple_grid(
        c,
        x=emp_x,
        top=emp_top,
        w=230,
        row_h=17,
        rows=[["コード", "氏　　　名"], [code, employee_name_with_honorific]],
        col_widths=[58, 172],
        font=font,
        size=9.0,
        center_first_row=True,
        center_cells={(1, 1)},
    )

    # 中央3列表
    main_top = emp_top - 50
    main_h = 386
    gap = 5
    left_x = 24
    attendance_w = 184
    pay_w = 171
    deduct_w = 172

    draw_fixed_table(
        c,
        x=left_x,
        top=main_top,
        w=attendance_w,
        h=main_h,
        title="勤　怠　内　訳",
        headers=["項目", "時間・回数", "単価"],
        rows=[["出勤日数", f"{int(result.work_days or 0)}日", ""]],
        col_widths=[60, 66, 58],
        font=font,
        right_align_cols={1},
    )

    if result.executive_compensation:
        payment_rows = [["役員報酬", amount(result.executive_compensation)]]
    else:
        payment_rows = nonzero_rows(
            [
                ("基本給", result.basic_salary),
                ("現場手当", result.site_allowance),
                ("皆勤手当", result.attendance_allowance),
                (allowance_label("休日出勤", result.holiday_work_days), result.holiday_work_allowance),
                (allowance_label("夜間手当", result.night_work_days), result.night_allowance),
                (allowance_label("半徹手当", result.half_night_work_days), result.half_night_allowance),
            ],
            always_show={"基本給"},
        )

    pay_x = left_x + attendance_w + gap
    draw_fixed_table(
        c,
        x=pay_x,
        top=main_top,
        w=pay_w,
        h=main_h,
        title="支　給　項　目",
        headers=["項目", "金額"],
        rows=payment_rows,
        col_widths=[96, 75],
        font=font,
        total=["合計", amount(result.gross_pay)],
    )

    deduction_rows = nonzero_rows(
        [
            ("健康保険", result.health_insurance),
            ("介護保険", result.care_insurance),
            ("子ども子育て支援", result.child_support),
            ("厚生年金", result.pension_insurance),
            ("雇用保険", result.employment_insurance),
            ("所得税", result.withholding_income_tax),
            ("住民税", result.resident_tax),
            ("その他控除", result.other_deduction),
        ]
    )
    deduct_x = pay_x + pay_w + gap
    draw_fixed_table(
        c,
        x=deduct_x,
        top=main_top,
        w=deduct_w,
        h=main_h,
        title="控　除　項　目",
        headers=["項目", "金額"],
        rows=deduction_rows,
        col_widths=[100, 72],
        font=font,
        total=["合計", amount(result.total_deductions)],
    )

    # 下段エリア
    lower_top = main_top - main_h - 12
    lower_gap = 8
    lower_box_w = 141
    lower_box_h = 84
    payment_row_h = 18
    payment_h = payment_row_h * 3
    blank_x = 24
    lower_right_edge = deduct_x + deduct_w
    right_x = lower_right_edge - lower_box_w
    other_x = right_x - lower_gap - lower_box_w
    blank_w = other_x - lower_gap - blank_x
    blank_h = lower_box_h + lower_gap + payment_h
    rect(c, blank_x, lower_top - blank_h, blank_w, blank_h, radius=0.4)

    draw_fixed_table(
        c,
        x=other_x,
        top=lower_top,
        w=lower_box_w,
        h=blank_h,
        title="そ　の　他",
        headers=["項目", "金額"],
        rows=[
            ["社会保険合計", amount(result.social_insurance_total)],
            ["源泉税判定額", amount(result.withholding_tax_base)],
            ["非課税合計", "0"],
        ],
        col_widths=[88, 53],
        font=font,
        title_size=8.8,
        body_size=8.3,
        row_h=14.2,
    )

    note_h = lower_box_h
    note_bottom = lower_top - note_h
    rect(c, right_x, note_bottom, lower_box_w, note_h, radius=0.4)
    note_title_h = 17
    draw_text(c, "備　考", right_x + lower_box_w / 2, lower_top - note_title_h / 2 - 8.8 / 3, font, 8.8, "center")
    line(c, right_x, lower_top - note_title_h, right_x + lower_box_w, lower_top - note_title_h)
    if result.note:
        draw_multiline_text(
            c,
            result.note,
            right_x + 8,
            lower_top - note_title_h - 7,
            font,
            8.0,
            lower_box_w - 16,
            note_h - note_title_h - 12,
        )
    draw_simple_grid(
        c,
        x=right_x,
        top=note_bottom - lower_gap,
        w=lower_box_w,
        row_h=payment_row_h,
        rows=[
            ["振込支給額", amount(result.net_pay)],
            ["現金支給額", "0"],
            ["差引支給額", amount(result.net_pay)],
        ],
        col_widths=[82, 59],
        font=font,
        size=8.8,
    )

    c.showPage()
    c.save()
    return path
