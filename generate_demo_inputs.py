from __future__ import annotations

from pathlib import Path

from runtime_bootstrap import bootstrap_runtime

bootstrap_runtime()

from PIL import Image, ImageDraw, ImageFont
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen import canvas

from demo_data import DEMO_COMPANY, DEMO_EMPLOYEES, DEMO_RESIDENT_TAX


BASE_DIR = Path(__file__).resolve().parent
INPUT_DIR = BASE_DIR / "demo_inputs"
PDF_PATH = INPUT_DIR / "resident_tax_notice_demo_multi_employee_R8.pdf"
PNG_PATH = INPUT_DIR / "resident_tax_notice_demo_scan_R8.png"


def _lines_for(name: str) -> list[str]:
    employee = DEMO_EMPLOYEES[name]
    tax = DEMO_RESIDENT_TAX[name]
    lines = [
        "DEMO / FICTIONAL DATA / 架空データ",
        "特別徴収義務者用 - 会社用複数人通知書",
        str(DEMO_COMPANY["company_name"]),
        f"市区町村: {DEMO_COMPANY['municipality']}    年度: {DEMO_COMPANY['fiscal_year']}",
        f"納税義務者氏名: {name}",
        f"従業員コード: {employee['employee_id']}",
        f"年税額\t{tax['annual_amount']:,} 円",
    ]
    for month, amount in tax["monthly_amounts"].items():
        if month == "翌年1月分":
            lines.append("翌年分")
        label = month.removeprefix("翌年")
        lines.append(f"{label}\t{amount:,} 円")
    return lines


def create_pdf() -> None:
    INPUT_DIR.mkdir(parents=True, exist_ok=True)
    font_name = "HeiseiMin-W3"
    pdfmetrics.registerFont(UnicodeCIDFont(font_name))
    document = canvas.Canvas(str(PDF_PATH), pagesize=A4)
    width, height = A4
    for name in DEMO_EMPLOYEES:
        document.setTitle("Fictional Resident Tax Notice Demo")
        document.setFont(font_name, 18)
        y = height - 56
        for index, line in enumerate(_lines_for(name)):
            if index == 0:
                document.setFillColorRGB(0.75, 0.05, 0.05)
                document.setFont(font_name, 18)
            elif index == 1:
                document.setFillColorRGB(0, 0, 0)
                document.setFont(font_name, 15)
            else:
                document.setFillColorRGB(0, 0, 0)
                document.setFont(font_name, 12)
            if "\t" in line:
                label, value = line.split("\t", 1)
                document.drawString(54, y, label)
                document.drawString(250, y, value)
            else:
                document.drawString(54, y, line)
            y -= 32 if index < 2 else 27
        document.rect(40, 40, width - 80, height - 80)
        document.showPage()
    document.save()


def _font(size: int) -> ImageFont.FreeTypeFont:
    candidates = [
        Path("C:/Windows/Fonts/YuGothM.ttc"),
        Path("C:/Windows/Fonts/meiryo.ttc"),
        Path("C:/Windows/Fonts/msgothic.ttc"),
    ]
    path = next((candidate for candidate in candidates if candidate.exists()), None)
    if path is None:
        raise RuntimeError("A Japanese Windows font was not found.")
    return ImageFont.truetype(str(path), size=size)


def create_png() -> None:
    image = Image.new("RGB", (1800, 2500), "white")
    draw = ImageDraw.Draw(image)
    title_font = _font(46)
    body_font = _font(32)
    small_font = _font(28)
    y = 55
    for person_index, name in enumerate(DEMO_EMPLOYEES):
        for line_index, line in enumerate(_lines_for(name)):
            font = title_font if line_index == 0 else body_font if line_index < 7 else small_font
            fill = "#b00020" if line_index == 0 else "black"
            if "\t" in line:
                label, value = line.split("\t", 1)
                draw.text((70, y), label, font=font, fill=fill)
                draw.text((430, y), value, font=font, fill=fill)
            else:
                draw.text((70, y), line, font=font, fill=fill)
            y += 62 if line_index < 2 else 48
        if person_index == 0:
            draw.line((60, y + 10, 1740, y + 10), fill="black", width=3)
            y += 70
    image.save(PNG_PATH, format="PNG", dpi=(300, 300))


def main() -> None:
    create_pdf()
    create_png()
    print(PDF_PATH.name)
    print(PNG_PATH.name)


if __name__ == "__main__":
    main()
