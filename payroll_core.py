from __future__ import annotations

import json
from calendar import monthrange
from dataclasses import asdict, dataclass
from datetime import date, datetime
from decimal import Decimal, ROUND_FLOOR
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parent
CONFIG_DIR = BASE_DIR / "config"

RESIDENT_TAX_MONTHS = [
    "6月分",
    "7月分",
    "8月分",
    "9月分",
    "10月分",
    "11月分",
    "12月分",
    "翌年1月分",
    "翌年2月分",
    "翌年3月分",
    "翌年4月分",
    "翌年5月分",
]

RESIDENT_TAX_COLLECTION_TYPES = {"普通徴収", "特別徴収"}

ALLOWANCE_FIELDS = [
    "site_allowance",
    "attendance_allowance",
    "holiday_work_allowance",
    "night_allowance",
    "half_night_allowance",
]


@dataclass(frozen=True)
class PayrollInput:
    employee_name: str
    payroll_month: date
    work_days: int = 0
    site_allowance: int = 0
    attendance_allowance: int = 0
    holiday_work_allowance: int = 0
    night_allowance: int = 0
    half_night_allowance: int = 0
    holiday_work_days: int = 0
    night_work_days: int = 0
    half_night_work_days: int = 0
    variable_allowance: int = 0
    meal_deduction: int = 0
    other_deduction: int = 0
    health_insurance_deduct_enabled: int = 1
    note: str = ""
    resident_tax_notice_id: int | None = None
    resident_tax_original_amount: int | None = None
    resident_tax_used_amount: int | None = None
    resident_tax_override_reason: str = ""
    resident_tax_municipality: str = ""
    resident_tax_notice_confirmed_at: str = ""
    resident_tax_notice_manual_corrected: int = 0


@dataclass(frozen=True)
class PayrollResult:
    employee_name: str
    role: str
    payroll_month: date
    payroll_month_label: str
    payment_date: date
    payment_date_label: str
    age: int
    tax_year_label: str
    social_insurance_year: str
    social_insurance_month_label: str
    resident_tax_year: str
    resident_tax_month: str
    dependents: int
    tax_class: str
    work_days: int
    basic_salary: int
    executive_compensation: int
    site_allowance: int
    attendance_allowance: int
    holiday_work_allowance: int
    night_allowance: int
    half_night_allowance: int
    holiday_work_days: int
    night_work_days: int
    half_night_work_days: int
    variable_allowance: int
    gross_pay: int
    assessment_base: int
    standard_monthly_health: int
    standard_monthly_pension: int
    health_insurance_deduct_enabled: int
    health_insurance: int
    care_insurance: int
    child_support: int
    pension_insurance: int
    employment_insurance: int
    social_insurance_total: int
    withholding_tax_base: int
    withholding_income_tax: int
    resident_tax: int
    meal_deduction: int
    other_deduction: int
    total_deductions: int
    net_pay: int
    note: str
    resident_tax_notice_id: int | None = None
    resident_tax_original_amount: int | None = None
    resident_tax_used_amount: int | None = None
    resident_tax_override: int = 0
    resident_tax_override_reason: str = ""
    resident_tax_override_at: str | None = None
    resident_tax_municipality: str = ""
    resident_tax_notice_confirmed_at: str = ""
    resident_tax_notice_manual_corrected: int = 0

    def to_record(self) -> dict[str, Any]:
        data = asdict(self)
        data["payroll_month"] = self.payroll_month.isoformat()
        data["payment_date"] = self.payment_date.isoformat()
        return data


def load_json(filename: str) -> Any:
    with (CONFIG_DIR / filename).open("r", encoding="utf-8") as f:
        return json.load(f)


def save_json(filename: str, data: Any) -> None:
    with (CONFIG_DIR / filename).open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_all_masters() -> dict[str, Any]:
    return {
        "company": load_json("company_config.json"),
        "employees": load_json("employee_master.json"),
        "resident_tax": load_json("resident_tax_master.json"),
        "rates": load_json("rates_master.json"),
    }


def add_month(d: date, months: int) -> date:
    month_index = d.month - 1 + months
    year = d.year + month_index // 12
    month = month_index % 12 + 1
    return date(year, month, 1)


def payment_date_for(payroll_month: date, payment_day: int = 10) -> date:
    next_month = add_month(date(payroll_month.year, payroll_month.month, 1), 1)
    return date(next_month.year, next_month.month, payment_day)


def reiwa_number(gregorian_year: int) -> int:
    return gregorian_year - 2018


def fiscal_year_label_from_month(d: date) -> str:
    start_year = d.year if d.month >= 4 else d.year - 1
    return f"令和{reiwa_number(start_year)}年度"


def tax_year_label_from_payment_date(payment_date: date) -> str:
    return f"令和{reiwa_number(payment_date.year)}年分"


def resident_tax_year_and_month(payment_date: date) -> tuple[str, str]:
    if 6 <= payment_date.month <= 12:
        return f"令和{reiwa_number(payment_date.year)}年度", f"{payment_date.month}月分"
    return f"令和{reiwa_number(payment_date.year - 1)}年度", f"翌年{payment_date.month}月分"


def wareki_year_month(d: date) -> str:
    return f"令和{reiwa_number(d.year)}年{d.month}月分"


def wareki_date(d: date) -> str:
    return f"令和{reiwa_number(d.year)}年{d.month}月{d.day}日"


def parse_payroll_month_label(label: str) -> date:
    cleaned = label.replace("令和", "").replace("年", "-").replace("月分", "")
    year_part, month_part = cleaned.split("-")
    return date(2018 + int(year_part), int(month_part), 1)


def payroll_month_options(start: date = date(2025, 4, 1), months: int = 36) -> list[tuple[str, date]]:
    return [(wareki_year_month(add_month(start, i)), add_month(start, i)) for i in range(months)]


def age_on(birthday: date, target: date) -> int:
    age = target.year - birthday.year
    if (target.month, target.day) < (birthday.month, birthday.day):
        age -= 1
    return age


def parse_iso_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def round_yen(amount: Decimal | float | int) -> int:
    dec = amount if isinstance(amount, Decimal) else Decimal(str(amount))
    floor = int(dec.to_integral_value(rounding=ROUND_FLOOR))
    fraction = dec - Decimal(floor)
    return floor + 1 if fraction > Decimal("0.5") else floor


def non_negative_int(value: int | str | None) -> int:
    amount = int(value or 0)
    return max(0, amount)


def floor_to_10(amount: Decimal | float | int) -> int:
    dec = amount if isinstance(amount, Decimal) else Decimal(str(amount))
    return int(dec.to_integral_value(rounding=ROUND_FLOOR)) // 10 * 10


def employee_dependents(employee: dict[str, Any], payment_date: date) -> int:
    if "dependent_switch" not in employee:
        return int(employee.get("dependents", 0))
    for rule in employee["dependent_switch"]:
        if "until" in rule and payment_date <= parse_iso_date(rule["until"]):
            return int(rule["dependents"])
        if "from" in rule and payment_date >= parse_iso_date(rule["from"]):
            return int(rule["dependents"])
    return int(employee.get("dependents", 0))


def employee_dependents_for_payroll(employee: dict[str, Any], payroll_month: date, payment_date: date) -> int:
    return employee_dependents(employee, payment_date)


def get_resident_tax_collection_type(employee: dict[str, Any], payment_date: date | None = None) -> str | None:
    """支給日現在の徴収区分を返す。不明・不正な値は安全側で None とする。"""
    value = str(employee.get("resident_tax_collection_type", "")).strip()
    if value == "特別徴収" and payment_date is not None:
        start_value = str(employee.get("resident_tax_special_collection_from", "")).strip()
        end_value = str(employee.get("resident_tax_special_collection_to", "")).strip()
        target = f"{payment_date.year:04d}-{payment_date.month:02d}"
        if start_value and target < start_value:
            return "普通徴収"
        if end_value and target > end_value:
            return "普通徴収"
    return value if value in RESIDENT_TAX_COLLECTION_TYPES else None


def get_resident_tax(master: dict[str, Any], employee_name: str, fiscal_year: str, target_month: str) -> int:
    matches = [
        row
        for row in master.get("rows", [])
        if row.get("employee_name") == employee_name and row.get("fiscal_year") == fiscal_year
    ]
    if len(matches) > 1:
        raise ValueError(f"住民税マスターに重複があります: {employee_name} / {fiscal_year}")
    if not matches:
        return 0
    value = matches[0].get(target_month, 0)
    return int(value or 0)


def get_resident_tax_amount(
    master: dict[str, Any], employee: dict[str, Any], payment_date: date
) -> int:
    """徴収区分と支給年月を共通判定し、給与控除する住民税額を返す。"""
    if get_resident_tax_collection_type(employee, payment_date) != "特別徴収":
        return 0
    fiscal_year, target_month = resident_tax_year_and_month(payment_date)
    return get_resident_tax(master, str(employee.get("name", "")), fiscal_year, target_month)


def update_resident_tax_amount(
    master: dict[str, Any],
    employee_name: str,
    fiscal_year: str,
    target_month: str,
    amount: int,
) -> dict[str, Any]:
    if target_month not in RESIDENT_TAX_MONTHS:
        raise ValueError(f"住民税対象月が不正です: {target_month}")
    rows = master.setdefault("rows", [])
    matches = [row for row in rows if row.get("employee_name") == employee_name and row.get("fiscal_year") == fiscal_year]
    if len(matches) > 1:
        raise ValueError(f"住民税マスターに重複があります: {employee_name} / {fiscal_year}")
    if matches:
        matches[0][target_month] = int(amount or 0)
        return master
    row = {"employee_name": employee_name, "fiscal_year": fiscal_year, "note": ""}
    for month in RESIDENT_TAX_MONTHS:
        row[month] = 0
    row[target_month] = int(amount or 0)
    rows.append(row)
    return master


def withholding_tax(base_amount: int, tax_class: str, dependents: int, tax_year_label: str, rates: dict[str, Any]) -> int:
    tables = rates["withholding_tax_tables"]
    if tax_year_label not in tables:
        raise ValueError(f"源泉税額表が未登録です: {tax_year_label}")
    table = tables[tax_year_label]

    if tax_class == "甲欄":
        column = f"甲{min(max(int(dependents), 0), 7)}人"
    elif tax_class == "乙欄":
        column = "乙欄"
    else:
        raise ValueError(f"税額区分が不正です: {tax_class}")

    for row in table["low"]:
        if row["lower"] <= base_amount < row["upper"]:
            value = row[column]
            if isinstance(value, str) and value.endswith("%"):
                return round_yen(Decimal(base_amount) * Decimal(value.rstrip("%")) / Decimal(100))
            return int(value or 0)

    if column == "乙欄":
        applicable = [row for row in table["otsu_high"] if row["lower"] <= base_amount]
        if not applicable:
            raise ValueError("乙欄の源泉税額表が見つかりません。")
        row = applicable[-1]
        return floor_to_10(Decimal(row["base_tax"]) + (Decimal(base_amount) - Decimal(row["lower"])) * Decimal(str(row["add_rate"])))

    for row in table["high"]:
        if row["lower"] <= base_amount < row["upper"]:
            return floor_to_10(Decimal(row[column]) + (Decimal(base_amount) - Decimal(row["lower"])) * Decimal(str(row["add_rate"])))
    raise ValueError(f"源泉税額表の範囲外です: {base_amount}")


def calculate_payroll(
    payroll_input: PayrollInput,
    masters: dict[str, Any] | None = None,
) -> PayrollResult:
    masters = masters or load_all_masters()
    company = masters["company"]
    employees = masters["employees"]
    rates = masters["rates"]
    employee = employees[payroll_input.employee_name]

    payroll_month = date(payroll_input.payroll_month.year, payroll_input.payroll_month.month, 1)
    payment_date = payment_date_for(payroll_month, int(company["payment_day"]))
    social_year = fiscal_year_label_from_month(payroll_month)
    tax_year = tax_year_label_from_payment_date(payment_date)
    resident_year, resident_month = resident_tax_year_and_month(payment_date)
    birthday = parse_iso_date(employee["birthday"])
    age = age_on(birthday, payment_date)
    dependents = employee_dependents_for_payroll(employee, payroll_month, payment_date)

    allowance_input_enabled = bool(employee.get("allowance_input_enabled", employee.get("variable_allowance_enabled", False)))
    if allowance_input_enabled:
        site_allowance = non_negative_int(payroll_input.site_allowance)
        attendance_allowance = non_negative_int(payroll_input.attendance_allowance)
        holiday_work_allowance = non_negative_int(payroll_input.holiday_work_allowance)
        night_allowance = non_negative_int(payroll_input.night_allowance)
        half_night_allowance = non_negative_int(payroll_input.half_night_allowance)
        holiday_work_days = non_negative_int(payroll_input.holiday_work_days)
        night_work_days = non_negative_int(payroll_input.night_work_days)
        half_night_work_days = non_negative_int(payroll_input.half_night_work_days)
    else:
        site_allowance = 0
        attendance_allowance = 0
        holiday_work_allowance = 0
        night_allowance = 0
        half_night_allowance = 0
        holiday_work_days = 0
        night_work_days = 0
        half_night_work_days = 0

    itemized_allowance_total = (
        site_allowance
        + attendance_allowance
        + holiday_work_allowance
        + night_allowance
        + half_night_allowance
    )
    variable = itemized_allowance_total
    if allowance_input_enabled:
        gross_pay = int(employee.get("basic_salary", 0)) + itemized_allowance_total
    else:
        gross_pay = int(employee.get("executive_compensation") or employee.get("fixed_salary") or 0)
    standard_health = int(employee["standard_monthly_health"])
    standard_pension = max(88000, min(650000, int(employee["standard_monthly_pension"])))

    has_social = bool(employee["social_insurance"])
    has_health = has_social and bool(employee["health_insurance"])
    has_pension = has_social and bool(employee["pension_insurance"])
    has_employment = bool(employee["employment_insurance"])

    health_rate = Decimal(str(rates["health_insurance"][social_year][company["region"]]["employee_rate"]))
    care_rate = Decimal(str(rates["care_insurance"][social_year]["employee_rate"]))
    child_rate = Decimal(str(rates["child_support"][social_year]["employee_rate"]))
    pension_rate = Decimal(str(rates["pension"][social_year]["employee_rate"]))
    employment_rate = Decimal(str(rates["employment_insurance"][social_year][company["employment_insurance_type"]]))

    health_deduct_enabled = 1 if int(payroll_input.health_insurance_deduct_enabled or 0) else 0
    health = round_yen(Decimal(standard_health) * health_rate) if has_health and health_deduct_enabled else 0
    care = round_yen(Decimal(standard_health) * care_rate) if has_health and health_deduct_enabled and 40 <= age < 65 else 0
    child = round_yen(Decimal(standard_health) * child_rate) if has_health and health_deduct_enabled else 0
    pension = round_yen(Decimal(standard_pension) * pension_rate) if has_pension else 0
    employment = round_yen(Decimal(gross_pay) * employment_rate) if has_employment else 0

    social_total = health + care + child + pension + employment
    withholding_base = gross_pay - social_total
    income_tax = withholding_tax(withholding_base, employee["tax_class"], dependents, tax_year, rates)
    collection_type = get_resident_tax_collection_type(employee, payment_date)
    # 特別徴収は、画面側で取得した確認済み通知書額または理由付き手入力だけを使う。
    # resident_tax_master.json の旧参考値を無確認で転用しない。
    resident_tax = (
        non_negative_int(payroll_input.resident_tax_used_amount)
        if collection_type == "特別徴収" and payroll_input.resident_tax_used_amount is not None
        else 0
    )
    original_resident_tax = payroll_input.resident_tax_original_amount
    resident_override = int(
        original_resident_tax is None
        or resident_tax != int(original_resident_tax or 0)
    ) if collection_type == "特別徴収" else 0
    resident_override_at = datetime.now().isoformat(timespec="seconds") if resident_override else None
    meal = 0
    other = int(payroll_input.other_deduction or 0)
    total_deductions = social_total + income_tax + resident_tax + other
    net_pay = gross_pay - total_deductions

    return PayrollResult(
        employee_name=employee["name"],
        role=employee["role"],
        payroll_month=payroll_month,
        payroll_month_label=wareki_year_month(payroll_month),
        payment_date=payment_date,
        payment_date_label=wareki_date(payment_date),
        age=age,
        tax_year_label=tax_year,
        social_insurance_year=social_year,
        social_insurance_month_label=wareki_year_month(payroll_month),
        resident_tax_year=resident_year,
        resident_tax_month=resident_month,
        dependents=dependents,
        tax_class=employee["tax_class"],
        work_days=monthrange(payroll_month.year, payroll_month.month)[1]
        if employee.get("role") == "役員"
        else non_negative_int(payroll_input.work_days),
        basic_salary=int(employee.get("basic_salary", 0)),
        executive_compensation=int(employee.get("executive_compensation", 0)),
        site_allowance=site_allowance,
        attendance_allowance=attendance_allowance,
        holiday_work_allowance=holiday_work_allowance,
        night_allowance=night_allowance,
        half_night_allowance=half_night_allowance,
        holiday_work_days=holiday_work_days,
        night_work_days=night_work_days,
        half_night_work_days=half_night_work_days,
        variable_allowance=variable,
        gross_pay=gross_pay,
        assessment_base=int(employee["assessment_base"]),
        standard_monthly_health=standard_health,
        standard_monthly_pension=standard_pension,
        health_insurance_deduct_enabled=health_deduct_enabled,
        health_insurance=health,
        care_insurance=care,
        child_support=child,
        pension_insurance=pension,
        employment_insurance=employment,
        social_insurance_total=social_total,
        withholding_tax_base=withholding_base,
        withholding_income_tax=income_tax,
        resident_tax=resident_tax,
        meal_deduction=meal,
        other_deduction=other,
        total_deductions=total_deductions,
        net_pay=net_pay,
        note=payroll_input.note or "",
        resident_tax_notice_id=payroll_input.resident_tax_notice_id,
        resident_tax_original_amount=original_resident_tax,
        resident_tax_used_amount=resident_tax,
        resident_tax_override=resident_override,
        resident_tax_override_reason=payroll_input.resident_tax_override_reason or "",
        resident_tax_override_at=resident_override_at,
        resident_tax_municipality=payroll_input.resident_tax_municipality or "",
        resident_tax_notice_confirmed_at=payroll_input.resident_tax_notice_confirmed_at or "",
        resident_tax_notice_manual_corrected=int(payroll_input.resident_tax_notice_manual_corrected or 0),
    )
