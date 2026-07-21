from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from database import DB_PATH, get_connection, init_db
from demo_data import DEMO_COMPANY, DEMO_EMPLOYEES


BASE_DIR = Path(__file__).resolve().parent
CONFIG_DIR = BASE_DIR / "config"


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def reset_demo_data() -> dict[str, int | str]:
    if DB_PATH.resolve().parent != (BASE_DIR / "data").resolve():
        raise RuntimeError("Safety check failed: demo database is outside this demo folder.")
    _write_json(CONFIG_DIR / "company_config.json", DEMO_COMPANY)
    _write_json(CONFIG_DIR / "employee_master.json", DEMO_EMPLOYEES)
    _write_json(CONFIG_DIR / "resident_tax_master.json", {"rows": []})

    init_db(DB_PATH)
    connection = get_connection(DB_PATH)
    try:
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute("BEGIN IMMEDIATE")
        for table in (
            "issued_files", "payroll_history", "resident_tax_corrections",
            "resident_tax_monthly_amounts", "resident_tax_notices",
            "resident_tax_source_documents", "demo_employees", "demo_company",
        ):
            connection.execute(f'DELETE FROM "{table}"')
        connection.execute(
            "INSERT INTO demo_company(company_code, company_name, municipality, fiscal_year) VALUES (?, ?, ?, ?)",
            (
                DEMO_COMPANY["company_code"], DEMO_COMPANY["company_name"],
                DEMO_COMPANY["municipality"], DEMO_COMPANY["fiscal_year"],
            ),
        )
        for employee in DEMO_EMPLOYEES.values():
            fixed_pay = int(employee.get("executive_compensation") or employee.get("basic_salary") or 0)
            connection.execute(
                "INSERT INTO demo_employees(employee_code, employee_name, role, dependents, fixed_monthly_pay) VALUES (?, ?, ?, ?, ?)",
                (
                    str(employee["employee_id"]), employee["name"], employee["role"],
                    int(employee.get("dependents", 0)), fixed_pay,
                ),
            )
        connection.execute("DELETE FROM sqlite_sequence")
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

    for folder in (BASE_DIR / "output", BASE_DIR / "tmp", BASE_DIR / "logs"):
        if folder.exists():
            shutil.rmtree(folder)
        folder.mkdir(parents=True, exist_ok=True)
    stored_inputs = BASE_DIR / "data" / "resident_tax_notices"
    if stored_inputs.exists():
        shutil.rmtree(stored_inputs)

    connection = get_connection(DB_PATH)
    try:
        integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
        version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    finally:
        connection.close()
    return {"company_count": 1, "employee_count": len(DEMO_EMPLOYEES), "integrity": integrity, "user_version": version}


def main() -> int:
    parser = argparse.ArgumentParser(description="Reset only the local Build Week demo data.")
    parser.add_argument("--yes", action="store_true", help="Skip the interactive confirmation.")
    args = parser.parse_args()
    if not args.yes:
        answer = input("デモDB・給与履歴・発行物を初期化します。続行しますか？ [y/N]: ").strip().lower()
        if answer not in {"y", "yes"}:
            print("キャンセルしました。")
            return 1
    result = reset_demo_data()
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
