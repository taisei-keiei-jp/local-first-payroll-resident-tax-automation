from __future__ import annotations

import json
import hashlib
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "data" / "payroll_history.db"

ALLOWANCE_COLUMNS = {
    "work_days": "INTEGER NOT NULL DEFAULT 0",
    "site_allowance": "INTEGER NOT NULL DEFAULT 0",
    "attendance_allowance": "INTEGER NOT NULL DEFAULT 0",
    "holiday_work_allowance": "INTEGER NOT NULL DEFAULT 0",
    "night_allowance": "INTEGER NOT NULL DEFAULT 0",
    "half_night_allowance": "INTEGER NOT NULL DEFAULT 0",
    "holiday_work_days": "INTEGER NOT NULL DEFAULT 0",
    "night_work_days": "INTEGER NOT NULL DEFAULT 0",
    "half_night_work_days": "INTEGER NOT NULL DEFAULT 0",
    "variable_allowance": "INTEGER NOT NULL DEFAULT 0",
}

FEATURE_COLUMNS = {
    "health_insurance_deduct_enabled": "INTEGER DEFAULT 1",
    "issue_status": "TEXT DEFAULT 'issued'",
    "reissue_source_id": "INTEGER DEFAULT NULL",
    "dependent_count": "INTEGER DEFAULT NULL",
    "resident_tax_notice_id": "INTEGER DEFAULT NULL",
    "resident_tax_original_amount": "INTEGER DEFAULT NULL",
    "resident_tax_used_amount": "INTEGER DEFAULT NULL",
    "resident_tax_override": "INTEGER NOT NULL DEFAULT 0",
    "resident_tax_override_reason": "TEXT DEFAULT ''",
    "resident_tax_override_at": "TEXT DEFAULT NULL",
}

SCHEMA_VERSION = 3
RESIDENT_TAX_MONTHS = (
    "6月分", "7月分", "8月分", "9月分", "10月分", "11月分", "12月分",
    "翌年1月分", "翌年2月分", "翌年3月分", "翌年4月分", "翌年5月分",
)

NOTICE_FEATURE_COLUMNS = {
    "source_document_id": "INTEGER DEFAULT NULL",
    "detected_name": "TEXT DEFAULT ''",
    "document_type": "TEXT DEFAULT 'unknown'",
    "page_number": "INTEGER NOT NULL DEFAULT 1",
    "region_json": "TEXT NOT NULL DEFAULT '{}'",
    "all_months_manually_checked": "INTEGER NOT NULL DEFAULT 0",
}

TABLE_CREATE_SQL = {
    "demo_company": """
        CREATE TABLE demo_company (
            company_code TEXT PRIMARY KEY,
            company_name TEXT NOT NULL,
            municipality TEXT NOT NULL,
            fiscal_year TEXT NOT NULL
        )
    """,
    "demo_employees": """
        CREATE TABLE demo_employees (
            employee_code TEXT PRIMARY KEY,
            employee_name TEXT NOT NULL UNIQUE,
            role TEXT NOT NULL,
            dependents INTEGER NOT NULL,
            fixed_monthly_pay INTEGER NOT NULL
        )
    """,
    "payroll_history": """
        CREATE TABLE payroll_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            employee_name TEXT NOT NULL,
            payroll_month TEXT NOT NULL,
            payment_date TEXT NOT NULL,
            gross_pay INTEGER NOT NULL,
            health_insurance INTEGER NOT NULL,
            care_insurance INTEGER NOT NULL,
            child_support INTEGER NOT NULL,
            pension_insurance INTEGER NOT NULL,
            employment_insurance INTEGER NOT NULL,
            social_insurance_total INTEGER NOT NULL,
            withholding_tax_base INTEGER NOT NULL,
            withholding_income_tax INTEGER NOT NULL,
            resident_tax INTEGER NOT NULL,
            meal_deduction INTEGER NOT NULL,
            other_deduction INTEGER NOT NULL,
            total_deductions INTEGER NOT NULL,
            net_pay INTEGER NOT NULL,
            note TEXT,
            result_json TEXT NOT NULL,
            calculated_at TEXT NOT NULL,
            work_days INTEGER NOT NULL DEFAULT 0,
            site_allowance INTEGER NOT NULL DEFAULT 0,
            attendance_allowance INTEGER NOT NULL DEFAULT 0,
            holiday_work_allowance INTEGER NOT NULL DEFAULT 0,
            night_allowance INTEGER NOT NULL DEFAULT 0,
            half_night_allowance INTEGER NOT NULL DEFAULT 0,
            holiday_work_days INTEGER NOT NULL DEFAULT 0,
            night_work_days INTEGER NOT NULL DEFAULT 0,
            half_night_work_days INTEGER NOT NULL DEFAULT 0,
            variable_allowance INTEGER NOT NULL DEFAULT 0,
            health_insurance_deduct_enabled INTEGER DEFAULT 1,
            issue_status TEXT DEFAULT 'issued',
            reissue_source_id INTEGER DEFAULT NULL,
            dependent_count INTEGER DEFAULT NULL,
            resident_tax_notice_id INTEGER DEFAULT NULL,
            resident_tax_original_amount INTEGER DEFAULT NULL,
            resident_tax_used_amount INTEGER DEFAULT NULL,
            resident_tax_override INTEGER NOT NULL DEFAULT 0,
            resident_tax_override_reason TEXT DEFAULT '',
            resident_tax_override_at TEXT DEFAULT NULL
        )
    """,
    "resident_tax_source_documents": """
        CREATE TABLE resident_tax_source_documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_filename TEXT NOT NULL,
            stored_filename TEXT NOT NULL,
            source_sha256 TEXT NOT NULL UNIQUE,
            source_mime TEXT,
            detected_document_type TEXT NOT NULL DEFAULT 'unknown',
            document_type TEXT NOT NULL DEFAULT 'unknown',
            fiscal_year TEXT,
            municipality TEXT,
            imported_at TEXT NOT NULL,
            page_count INTEGER NOT NULL DEFAULT 1,
            raw_extracted_text TEXT,
            ocr_result_json TEXT NOT NULL DEFAULT '{}'
        )
    """,
    "resident_tax_notices": """
        CREATE TABLE resident_tax_notices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            employee_code TEXT,
            employee_name TEXT,
            fiscal_year TEXT,
            municipality TEXT,
            designation_number TEXT,
            annual_amount INTEGER NOT NULL DEFAULT 0,
            source_filename TEXT NOT NULL,
            stored_filename TEXT NOT NULL,
            source_sha256 TEXT NOT NULL,
            source_mime TEXT,
            raw_extracted_text TEXT,
            auto_result_json TEXT NOT NULL DEFAULT '{}',
            confirmed_result_json TEXT NOT NULL DEFAULT '{}',
            warnings_json TEXT NOT NULL DEFAULT '[]',
            confidence REAL,
            imported_at TEXT NOT NULL,
            corrected_at TEXT,
            confirmed_at TEXT,
            is_confirmed INTEGER NOT NULL DEFAULT 0,
            is_active INTEGER NOT NULL DEFAULT 0,
            revision_number INTEGER NOT NULL DEFAULT 1,
            manual_corrected INTEGER NOT NULL DEFAULT 0,
            correction_reason TEXT DEFAULT '',
            notes TEXT DEFAULT '',
            source_document_id INTEGER DEFAULT NULL,
            detected_name TEXT DEFAULT '',
            document_type TEXT DEFAULT 'unknown',
            page_number INTEGER NOT NULL DEFAULT 1,
            region_json TEXT NOT NULL DEFAULT '{}',
            all_months_manually_checked INTEGER NOT NULL DEFAULT 0
        )
    """,
    "resident_tax_monthly_amounts": """
        CREATE TABLE resident_tax_monthly_amounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            notice_id INTEGER NOT NULL,
            deduction_month TEXT NOT NULL,
            auto_read_amount INTEGER NOT NULL DEFAULT 0,
            confirmed_amount INTEGER NOT NULL DEFAULT 0,
            manual_corrected INTEGER NOT NULL DEFAULT 0,
            correction_note TEXT DEFAULT '',
            UNIQUE(notice_id, deduction_month),
            FOREIGN KEY(notice_id) REFERENCES resident_tax_notices(id)
        )
    """,
    "resident_tax_corrections": """
        CREATE TABLE resident_tax_corrections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            notice_id INTEGER NOT NULL,
            field_name TEXT NOT NULL,
            before_value TEXT,
            after_value TEXT,
            reason TEXT,
            corrected_at TEXT NOT NULL,
            FOREIGN KEY(notice_id) REFERENCES resident_tax_notices(id)
        )
    """,
    "issued_files": """
        CREATE TABLE issued_files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            payroll_history_id INTEGER NOT NULL,
            file_type TEXT NOT NULL,
            issued_at TEXT NOT NULL,
            reissue_count INTEGER NOT NULL DEFAULT 0,
            filename TEXT NOT NULL,
            file_sha256 TEXT NOT NULL,
            stored_path TEXT NOT NULL,
            UNIQUE(payroll_history_id, file_type),
            FOREIGN KEY(payroll_history_id) REFERENCES payroll_history(id)
        )
    """,
}

EXPECTED_INDEXES = {
    "idx_resident_tax_notice_employee_year": (
        "resident_tax_notices",
        ("employee_code", "fiscal_year", "revision_number"),
        False,
        "CREATE INDEX idx_resident_tax_notice_employee_year "
        "ON resident_tax_notices(employee_code, fiscal_year, revision_number)",
    ),
    "idx_resident_tax_notice_source": (
        "resident_tax_notices",
        ("source_document_id", "id"),
        False,
        "CREATE INDEX idx_resident_tax_notice_source "
        "ON resident_tax_notices(source_document_id, id)",
    ),
}


def get_connection(db_path: Path = DB_PATH) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _table_names(conn: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }


def _table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    return {
        str(row[1])
        for row in conn.execute(f'PRAGMA table_info("{table_name}")').fetchall()
    }


def _index_signature(
    conn: sqlite3.Connection,
    index_name: str,
) -> tuple[str, tuple[str, ...], bool, bool] | None:
    master = conn.execute(
        "SELECT tbl_name FROM sqlite_master WHERE type='index' AND name=?",
        (index_name,),
    ).fetchone()
    if master is None:
        return None
    table_name = str(master[0])
    index_rows = conn.execute(f'PRAGMA index_list("{table_name}")').fetchall()
    index_row = next((row for row in index_rows if str(row[1]) == index_name), None)
    if index_row is None:
        return None
    columns = tuple(
        str(row[2])
        for row in conn.execute(f'PRAGMA index_info("{index_name}")').fetchall()
    )
    return table_name, columns, bool(index_row[2]), bool(index_row[4])


def _index_is_current(conn: sqlite3.Connection, index_name: str) -> bool:
    expected_table, expected_columns, expected_unique, _sql = EXPECTED_INDEXES[index_name]
    signature = _index_signature(conn, index_name)
    return signature == (expected_table, expected_columns, expected_unique, False)


def init_db(db_path: Path = DB_PATH) -> None:
    conn = get_connection(db_path)
    try:
        tables = _table_names(conn)
        missing_tables = [name for name in TABLE_CREATE_SQL if name not in tables]

        payroll_columns = (
            _table_columns(conn, "payroll_history")
            if "payroll_history" in tables else set()
        )
        missing_payroll_columns = {
            column: definition
            for column, definition in {**ALLOWANCE_COLUMNS, **FEATURE_COLUMNS}.items()
            if "payroll_history" in tables and column not in payroll_columns
        }
        notice_columns = (
            _table_columns(conn, "resident_tax_notices")
            if "resident_tax_notices" in tables else set()
        )
        missing_notice_columns = {
            column: definition
            for column, definition in NOTICE_FEATURE_COLUMNS.items()
            if "resident_tax_notices" in tables and column not in notice_columns
        }

        legacy_index = _index_signature(conn, "idx_resident_tax_notice_sha")
        drop_legacy_index = bool(
            legacy_index
            and legacy_index[0] == "resident_tax_notices"
            and legacy_index[1] == ("source_sha256",)
        )
        indexes_to_rebuild = [
            index_name
            for index_name in EXPECTED_INDEXES
            if not _index_is_current(conn, index_name)
        ]

        has_unlinked_notices = False
        if "resident_tax_notices" in tables and "source_document_id" in notice_columns:
            has_unlinked_notices = conn.execute(
                "SELECT 1 FROM resident_tax_notices "
                "WHERE source_document_id IS NULL LIMIT 1"
            ).fetchone() is not None
        elif "resident_tax_notices" in tables:
            has_unlinked_notices = conn.execute(
                "SELECT 1 FROM resident_tax_notices LIMIT 1"
            ).fetchone() is not None

        repair_health_rows = False
        repair_issue_rows = False
        if "payroll_history" in tables:
            if "health_insurance_deduct_enabled" in payroll_columns:
                repair_health_rows = conn.execute(
                    "SELECT 1 FROM payroll_history "
                    "WHERE health_insurance_deduct_enabled IS NULL LIMIT 1"
                ).fetchone() is not None
            if "issue_status" in payroll_columns:
                repair_issue_rows = conn.execute(
                    "SELECT 1 FROM payroll_history "
                    "WHERE issue_status IS NULL OR issue_status = '' LIMIT 1"
                ).fetchone() is not None

        current_version = int(conn.execute("PRAGMA user_version").fetchone()[0])
        update_version = current_version < SCHEMA_VERSION
        changes_required = any((
            missing_tables,
            missing_payroll_columns,
            missing_notice_columns,
            drop_legacy_index,
            indexes_to_rebuild,
            has_unlinked_notices,
            repair_health_rows,
            repair_issue_rows,
            update_version,
        ))
        if not changes_required:
            return

        conn.execute("BEGIN IMMEDIATE")
        try:
            for table_name in missing_tables:
                conn.execute(TABLE_CREATE_SQL[table_name])

            for column, definition in missing_notice_columns.items():
                conn.execute(
                    f'ALTER TABLE resident_tax_notices ADD COLUMN "{column}" {definition}'
                )
            for column, definition in missing_payroll_columns.items():
                conn.execute(
                    f'ALTER TABLE payroll_history ADD COLUMN "{column}" {definition}'
                )

            if drop_legacy_index:
                conn.execute("DROP INDEX idx_resident_tax_notice_sha")
            for index_name in indexes_to_rebuild:
                signature = _index_signature(conn, index_name)
                if signature is not None:
                    conn.execute(f'DROP INDEX "{index_name}"')
                conn.execute(EXPECTED_INDEXES[index_name][3])

            # v2以前の通知書も、原本1件と従業員別通知の関係を壊さず移行する。
            if has_unlinked_notices:
                legacy_rows = conn.execute(
                    "SELECT * FROM resident_tax_notices WHERE source_document_id IS NULL"
                ).fetchall()
                for legacy in legacy_rows:
                    source_row = conn.execute(
                        "SELECT id FROM resident_tax_source_documents WHERE source_sha256=?",
                        (legacy["source_sha256"],),
                    ).fetchone()
                    if source_row is None:
                        source_cursor = conn.execute(
                            """
                            INSERT INTO resident_tax_source_documents (
                                source_filename, stored_filename, source_sha256, source_mime,
                                detected_document_type, document_type, fiscal_year, municipality,
                                imported_at, page_count, raw_extracted_text, ocr_result_json
                            ) VALUES (?, ?, ?, ?, 'unknown', 'unknown', ?, ?, ?, 1, ?, '{}')
                            """,
                            (
                                legacy["source_filename"], legacy["stored_filename"],
                                legacy["source_sha256"], legacy["source_mime"],
                                legacy["fiscal_year"], legacy["municipality"],
                                legacy["imported_at"], legacy["raw_extracted_text"],
                            ),
                        )
                        source_id = int(source_cursor.lastrowid)
                    else:
                        source_id = int(source_row["id"])
                    conn.execute(
                        "UPDATE resident_tax_notices SET source_document_id=? "
                        "WHERE id=? AND source_document_id IS NULL",
                        (source_id, legacy["id"]),
                    )

            if repair_health_rows:
                conn.execute(
                    "UPDATE payroll_history SET health_insurance_deduct_enabled = 1 "
                    "WHERE health_insurance_deduct_enabled IS NULL"
                )
            if repair_issue_rows:
                conn.execute(
                    "UPDATE payroll_history SET issue_status = 'issued' "
                    "WHERE issue_status IS NULL OR issue_status = ''"
                )
            if update_version:
                conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    finally:
        conn.close()


def save_payroll_result(
    result: Any,
    db_path: Path = DB_PATH,
    issue_status: str = "issued",
    reissue_source_id: int | None = None,
) -> int:
    init_db(db_path)
    record = result.to_record()
    issue_status = issue_status if issue_status in {"issued", "reissued"} else "issued"
    conn = get_connection(db_path)
    try:
        cur = conn.execute(
            """
            INSERT INTO payroll_history (
                employee_name, payroll_month, payment_date, gross_pay,
                work_days,
                site_allowance, attendance_allowance, holiday_work_allowance,
                night_allowance, half_night_allowance,
                holiday_work_days, night_work_days, half_night_work_days,
                variable_allowance,
                health_insurance, care_insurance, child_support, pension_insurance,
                employment_insurance, social_insurance_total, withholding_tax_base,
                withholding_income_tax, resident_tax, meal_deduction, other_deduction,
                total_deductions, net_pay, note, result_json, calculated_at,
                health_insurance_deduct_enabled, issue_status, reissue_source_id,
                dependent_count, resident_tax_notice_id, resident_tax_original_amount,
                resident_tax_used_amount, resident_tax_override,
                resident_tax_override_reason, resident_tax_override_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record["employee_name"],
                record["payroll_month"],
                record["payment_date"],
                record["gross_pay"],
                record.get("work_days", 0),
                record.get("site_allowance", 0),
                record.get("attendance_allowance", 0),
                record.get("holiday_work_allowance", 0),
                record.get("night_allowance", 0),
                record.get("half_night_allowance", 0),
                record.get("holiday_work_days", 0),
                record.get("night_work_days", 0),
                record.get("half_night_work_days", 0),
                record.get("variable_allowance", 0),
                record["health_insurance"],
                record["care_insurance"],
                record["child_support"],
                record["pension_insurance"],
                record["employment_insurance"],
                record["social_insurance_total"],
                record["withholding_tax_base"],
                record["withholding_income_tax"],
                record["resident_tax"],
                record["meal_deduction"],
                record["other_deduction"],
                record["total_deductions"],
                record["net_pay"],
                record["note"],
                json.dumps(record, ensure_ascii=False),
                datetime.now().isoformat(timespec="seconds"),
                record.get("health_insurance_deduct_enabled", 1),
                issue_status,
                reissue_source_id,
                record.get("dependents"),
                record.get("resident_tax_notice_id"),
                record.get("resident_tax_original_amount"),
                record.get("resident_tax_used_amount", record.get("resident_tax")),
                int(record.get("resident_tax_override") or 0),
                record.get("resident_tax_override_reason", ""),
                record.get("resident_tax_override_at"),
            ),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def fetch_history(limit: int = 100, db_path: Path = DB_PATH) -> list[dict[str, Any]]:
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            """
            SELECT id, employee_name, payroll_month, payment_date, gross_pay,
                   work_days,
                   site_allowance, attendance_allowance, holiday_work_allowance,
                   night_allowance, half_night_allowance,
                   holiday_work_days, night_work_days, half_night_work_days,
                   total_deductions, net_pay, resident_tax, calculated_at, note,
                   COALESCE(health_insurance_deduct_enabled, 1) AS health_insurance_deduct_enabled,
                   COALESCE(issue_status, 'issued') AS issue_status,
                   reissue_source_id,
                   dependent_count,
                   resident_tax_notice_id, resident_tax_original_amount,
                   resident_tax_used_amount, resident_tax_override,
                   resident_tax_override_reason, resident_tax_override_at,
                   result_json
            FROM payroll_history
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def delete_payroll_history(ids: list[int], db_path: Path = DB_PATH) -> int:
    init_db(db_path)
    clean_ids = sorted({int(history_id) for history_id in ids})
    if not clean_ids:
        return 0
    placeholders = ",".join("?" for _ in clean_ids)
    conn = get_connection(db_path)
    try:
        cur = conn.execute(
            f"DELETE FROM payroll_history WHERE id IN ({placeholders})",
            clean_ids,
        )
        conn.commit()
        return int(cur.rowcount)
    finally:
        conn.close()


def create_resident_tax_source_document(
    source_filename: str,
    stored_filename: str,
    source_sha256: str,
    source_mime: str,
    extracted_notices: list[dict[str, Any]],
    raw_text: str,
    warnings: list[str],
    confidence: float | None,
    detected_document_type: str = "unknown",
    document_type: str | None = None,
    fiscal_year: str = "",
    municipality: str = "",
    page_count: int = 1,
    ocr_result: dict[str, Any] | None = None,
    db_path: Path = DB_PATH,
) -> tuple[int, list[int]]:
    # UIを経由しない呼出しでも、未読取値を0円へ変換して保存しない。
    for candidate in extracted_notices or []:
        extracted = candidate.get("fields", candidate)
        annual_raw = extracted.get("annual_amount")
        monthly = extracted.get("monthly_amounts") or {}
        values = [monthly.get(month) for month in RESIDENT_TAX_MONTHS]
        if annual_raw is None:
            raise ValueError("年税額を特定できない通知書は保存できません。")
        if any(value is None for value in values):
            raise ValueError("12か月分の月別税額を特定できない通知書は保存できません。")
        annual = int(annual_raw)
        amounts = [int(value) for value in values]
        if annual > 0 and all(amount == 0 for amount in amounts):
            raise ValueError("月別税額がすべて0円の重大な読取失敗を検出したため保存しません。")
        if 0 < annual < 100:
            raise ValueError("年税額の誤読可能性が高いため保存しません。")
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        duplicate = conn.execute(
            "SELECT id FROM resident_tax_source_documents WHERE source_sha256 = ?",
            (source_sha256,),
        ).fetchone()
        if duplicate:
            raise ValueError(
                f"同じ通知書が既に取り込まれています（原本ID: {duplicate['id']}）"
            )
        now = datetime.now().isoformat(timespec="seconds")
        actual_type = document_type or detected_document_type or "unknown"
        source_cursor = conn.execute(
            """
            INSERT INTO resident_tax_source_documents (
                source_filename, stored_filename, source_sha256, source_mime,
                detected_document_type, document_type, fiscal_year, municipality,
                imported_at, page_count, raw_extracted_text, ocr_result_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                source_filename, stored_filename, source_sha256, source_mime,
                detected_document_type or "unknown", actual_type, fiscal_year, municipality,
                now, max(1, int(page_count or 1)), raw_text,
                json.dumps(ocr_result or {}, ensure_ascii=False),
            ),
        )
        source_id = int(source_cursor.lastrowid)
        notice_ids: list[int] = []
        candidates = extracted_notices or [{"fields": {}}]
        for candidate in candidates:
            extracted = candidate.get("fields", candidate)
            employee_code = str(extracted.get("employee_code") or "")
            employee_year = str(extracted.get("fiscal_year") or fiscal_year or "")
            revision_row = conn.execute(
                """
                SELECT COALESCE(MAX(revision_number), 0) + 1 AS next_revision
                FROM resident_tax_notices
                WHERE employee_code = ? AND fiscal_year = ?
                """,
                (employee_code, employee_year),
            ).fetchone()
            revision = int(revision_row["next_revision"] or 1)
            candidate_warnings = list(dict.fromkeys(candidate.get("warnings", warnings) or []))
            candidate_confidence = candidate.get("confidence", confidence)
            cursor = conn.execute(
                """
                INSERT INTO resident_tax_notices (
                    employee_code, employee_name, fiscal_year, municipality,
                    designation_number, annual_amount, source_filename, stored_filename,
                    source_sha256, source_mime, raw_extracted_text, auto_result_json,
                    confirmed_result_json, warnings_json, confidence, imported_at,
                    revision_number, notes, source_document_id, detected_name,
                    document_type, page_number, region_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '{}', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    employee_code, extracted.get("employee_name", ""), employee_year,
                    extracted.get("municipality", municipality),
                    extracted.get("designation_number", ""),
                    int(extracted.get("annual_amount") or 0), source_filename, stored_filename,
                    source_sha256, source_mime, raw_text,
                    json.dumps(extracted, ensure_ascii=False),
                    json.dumps(candidate_warnings, ensure_ascii=False), candidate_confidence,
                    now, revision, extracted.get("notes", ""), source_id,
                    extracted.get("recognized_name", ""), actual_type,
                    max(1, int(candidate.get("page_number", 1) or 1)),
                    json.dumps(candidate.get("region") or {}, ensure_ascii=False),
                ),
            )
            notice_id = int(cursor.lastrowid)
            notice_ids.append(notice_id)
            for month, amount in (extracted.get("monthly_amounts") or {}).items():
                conn.execute(
                    """INSERT INTO resident_tax_monthly_amounts
                       (notice_id, deduction_month, auto_read_amount, confirmed_amount)
                       VALUES (?, ?, ?, ?)""",
                    (notice_id, month, int(amount or 0), int(amount or 0)),
                )
        conn.commit()
        return source_id, notice_ids
    finally:
        conn.close()


def create_resident_tax_notice(
    source_filename: str,
    stored_filename: str,
    source_sha256: str,
    source_mime: str,
    extracted: dict[str, Any],
    raw_text: str,
    warnings: list[str],
    confidence: float | None,
    db_path: Path = DB_PATH,
) -> int:
    """v2呼出し互換。新規実装は原本1件＋通知書複数件APIを使用する。"""
    source_id, notice_ids = create_resident_tax_source_document(
        source_filename, stored_filename, source_sha256, source_mime,
        [{"fields": extracted, "warnings": warnings, "confidence": confidence}],
        raw_text, warnings, confidence,
        detected_document_type="individual_single",
        fiscal_year=str(extracted.get("fiscal_year") or ""),
        municipality=str(extracted.get("municipality") or ""),
        db_path=db_path,
    )
    del source_id
    return notice_ids[0]


def fetch_resident_tax_notices(db_path: Path = DB_PATH) -> list[dict[str, Any]]:
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            """
            SELECT n.*,
                   s.detected_document_type AS source_detected_document_type,
                   s.document_type AS source_document_type,
                   s.page_count AS source_page_count,
                   s.ocr_result_json AS source_ocr_result_json
            FROM resident_tax_notices n
            LEFT JOIN resident_tax_source_documents s ON s.id=n.source_document_id
            ORDER BY n.imported_at DESC, n.source_document_id DESC, n.id ASC
            """
        ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            amounts = conn.execute(
                "SELECT * FROM resident_tax_monthly_amounts WHERE notice_id = ? ORDER BY id",
                (item["id"],),
            ).fetchall()
            item["monthly_amounts"] = [dict(amount) for amount in amounts]
            result.append(item)
        return result
    finally:
        conn.close()


def fetch_resident_tax_source_documents(db_path: Path = DB_PATH) -> list[dict[str, Any]]:
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            """
            SELECT s.*, COUNT(n.id) AS notice_count
            FROM resident_tax_source_documents s
            LEFT JOIN resident_tax_notices n ON n.source_document_id=s.id
            GROUP BY s.id
            ORDER BY s.imported_at DESC, s.id DESC
            """
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def update_resident_tax_source_document_type(
    source_document_id: int,
    document_type: str,
    db_path: Path = DB_PATH,
    reason: str = "",
) -> None:
    if document_type not in {"company_multi", "individual_single", "unknown"}:
        raise ValueError("帳票種類が不正です。")
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        source = conn.execute(
            "SELECT * FROM resident_tax_source_documents WHERE id=?",
            (int(source_document_id),),
        ).fetchone()
        if not source:
            raise ValueError("対象の原本が見つかりません。")
        current_type = str(source["document_type"] or "unknown")
        if current_type == document_type:
            return
        if not reason.strip():
            raise ValueError("帳票種類の訂正理由を入力してください。")
        confirmed_count = conn.execute(
            "SELECT COUNT(*) FROM resident_tax_notices "
            "WHERE source_document_id=? AND is_confirmed=1",
            (int(source_document_id),),
        ).fetchone()[0]
        if int(confirmed_count or 0):
            raise ValueError("確認済み通知書を含む原本の帳票種類は変更できません。")
        notice_ids = [
            int(row["id"])
            for row in conn.execute(
                "SELECT id FROM resident_tax_notices WHERE source_document_id=?",
                (int(source_document_id),),
            ).fetchall()
        ]
        now = datetime.now().isoformat(timespec="seconds")
        conn.execute(
            "UPDATE resident_tax_source_documents SET document_type=? WHERE id=?",
            (document_type, int(source_document_id)),
        )
        conn.execute(
            "UPDATE resident_tax_notices SET document_type=? WHERE source_document_id=?",
            (document_type, int(source_document_id)),
        )
        for notice_id in notice_ids:
            conn.execute(
                """INSERT INTO resident_tax_corrections
                   (notice_id, field_name, before_value, after_value, reason, corrected_at)
                   VALUES (?, 'document_type', ?, ?, ?, ?)""",
                (notice_id, current_type, document_type, reason.strip(), now),
            )
        conn.commit()
    finally:
        conn.close()


def update_resident_tax_notice_employee_link(
    notice_id: int,
    employee_code: str,
    employee_name: str,
    reason: str,
    db_path: Path = DB_PATH,
) -> None:
    """未確定通知書の紐付け先だけを訂正し、読取金額は変更しない。"""
    employee_code = str(employee_code or "").zfill(6)
    employee_name = str(employee_name or "").strip()
    with (BASE_DIR / "config" / "employee_master.json").open("r", encoding="utf-8") as handle:
        employees = json.load(handle)
    expected_code = str(employees.get(employee_name, {}).get("employee_id", "")).zfill(6)
    if not expected_code or employee_code != expected_code:
        raise ValueError("対象従業員が不正です。")
    if not reason.strip():
        raise ValueError("紐付け先の訂正理由を入力してください。")
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        current = conn.execute(
            "SELECT * FROM resident_tax_notices WHERE id=?",
            (int(notice_id),),
        ).fetchone()
        if not current:
            raise ValueError("対象の通知書が見つかりません。")
        if int(current["is_confirmed"] or 0) or int(current["is_active"] or 0):
            raise ValueError("確認済み通知書の紐付け先は変更できません。改訂版を取り込んでください。")
        old_code = str(current["employee_code"] or "")
        old_name = str(current["employee_name"] or "")
        if old_code == employee_code and old_name == employee_name:
            return
        now = datetime.now().isoformat(timespec="seconds")
        conn.execute(
            "UPDATE resident_tax_notices SET employee_code=?, employee_name=?, corrected_at=?, "
            "manual_corrected=1, correction_reason=? WHERE id=?",
            (employee_code, employee_name, now, reason.strip(), int(notice_id)),
        )
        conn.execute(
            """INSERT INTO resident_tax_corrections
               (notice_id, field_name, before_value, after_value, reason, corrected_at)
               VALUES (?, 'employee_link', ?, ?, ?, ?)""",
            (
                int(notice_id),
                f"{old_name or '未照合'}（{old_code or '未選択'}）",
                f"{employee_name}（{employee_code}）",
                reason.strip(),
                now,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def delete_unconfirmed_resident_tax_source(
    source_document_id: int,
    db_path: Path = DB_PATH,
) -> dict[str, Any]:
    """テスト取込の後片付け用。確定済み又は給与参照済みなら削除しない。"""
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        source = conn.execute(
            "SELECT * FROM resident_tax_source_documents WHERE id=?",
            (int(source_document_id),),
        ).fetchone()
        if not source:
            return {"deleted": False, "reason": "原本が見つかりません。"}
        notices = conn.execute(
            "SELECT * FROM resident_tax_notices WHERE source_document_id=?",
            (int(source_document_id),),
        ).fetchall()
        notice_ids = [int(row["id"]) for row in notices]
        if any(int(row["is_confirmed"] or 0) or int(row["is_active"] or 0) for row in notices):
            return {"deleted": False, "reason": "確認済み又は有効な通知書を含みます。"}
        if notice_ids:
            marks = ",".join("?" for _ in notice_ids)
            refs = conn.execute(
                f"SELECT COUNT(*) FROM payroll_history WHERE resident_tax_notice_id IN ({marks})",
                notice_ids,
            ).fetchone()[0]
            if int(refs or 0):
                return {"deleted": False, "reason": "給与履歴から参照されています。"}
            conn.execute(
                f"DELETE FROM resident_tax_corrections WHERE notice_id IN ({marks})",
                notice_ids,
            )
            conn.execute(
                f"DELETE FROM resident_tax_monthly_amounts WHERE notice_id IN ({marks})",
                notice_ids,
            )
            conn.execute(
                f"DELETE FROM resident_tax_notices WHERE id IN ({marks})",
                notice_ids,
            )
        conn.execute(
            "DELETE FROM resident_tax_source_documents WHERE id=?",
            (int(source_document_id),),
        )
        conn.commit()
        return {
            "deleted": True,
            "notice_ids": notice_ids,
            "stored_filename": source["stored_filename"],
        }
    finally:
        conn.close()


def confirm_resident_tax_notice(
    notice_id: int,
    values: dict[str, Any],
    reason: str = "",
    db_path: Path = DB_PATH,
) -> None:
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        current = conn.execute(
            "SELECT * FROM resident_tax_notices WHERE id = ?", (int(notice_id),)
        ).fetchone()
        if not current:
            raise ValueError("対象の通知書が見つかりません。")
        before = json.loads(current["auto_result_json"] or "{}")
        now = datetime.now().isoformat(timespec="seconds")
        employee_code = str(values.get("employee_code") or "")
        fiscal_year = str(values.get("fiscal_year") or "")
        revision = int(current["revision_number"] or 1)
        if (
            not int(current["is_confirmed"] or 0)
            or str(current["employee_code"] or "") != employee_code
            or str(current["fiscal_year"] or "") != fiscal_year
        ):
            revision_row = conn.execute(
                """
                SELECT COALESCE(MAX(revision_number), 0) + 1 AS next_revision
                FROM resident_tax_notices
                WHERE employee_code=? AND fiscal_year=? AND id<>?
                """,
                (employee_code, fiscal_year, int(notice_id)),
            ).fetchone()
            revision = int(revision_row["next_revision"] or 1)
        monthly = {k: int(v or 0) for k, v in (values.get("monthly_amounts") or {}).items()}
        changed_fields: list[tuple[str, Any, Any]] = []
        for field in ["employee_code", "employee_name", "fiscal_year", "municipality", "annual_amount", "notes"]:
            old = before.get(field, "")
            new = values.get(field, "")
            if str(old) != str(new):
                changed_fields.append((field, old, new))
        old_monthly = before.get("monthly_amounts") or {}
        for month, new in monthly.items():
            old = int(old_monthly.get(month) or 0)
            if old != new:
                changed_fields.append((month, old, new))
        manual = 1 if changed_fields else 0
        conn.execute(
            "UPDATE resident_tax_notices SET is_active = 0 WHERE employee_code = ? AND fiscal_year = ?",
            (employee_code, fiscal_year),
        )
        conn.execute(
            """
            UPDATE resident_tax_notices
            SET employee_code=?, employee_name=?, fiscal_year=?, municipality=?,
                annual_amount=?, confirmed_result_json=?, corrected_at=?, confirmed_at=?,
                is_confirmed=1, is_active=1, revision_number=?, manual_corrected=?, correction_reason=?, notes=?,
                all_months_manually_checked=?
            WHERE id=?
            """,
            (
                employee_code, values.get("employee_name", ""), fiscal_year,
                values.get("municipality", ""), int(values.get("annual_amount") or 0),
                json.dumps(values, ensure_ascii=False), now if manual else None, now,
                revision, manual, reason, values.get("notes", ""),
                int(bool(values.get("all_months_manually_checked"))), int(notice_id),
            ),
        )
        for month, confirmed in monthly.items():
            auto = int(old_monthly.get(month) or 0)
            conn.execute(
                """
                INSERT INTO resident_tax_monthly_amounts
                    (notice_id, deduction_month, auto_read_amount, confirmed_amount, manual_corrected, correction_note)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(notice_id, deduction_month) DO UPDATE SET
                    confirmed_amount=excluded.confirmed_amount,
                    manual_corrected=excluded.manual_corrected,
                    correction_note=excluded.correction_note
                """,
                (int(notice_id), month, auto, confirmed, int(auto != confirmed), reason),
            )
        for field, old, new in changed_fields:
            conn.execute(
                """INSERT INTO resident_tax_corrections
                   (notice_id, field_name, before_value, after_value, reason, corrected_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (int(notice_id), field, str(old), str(new), reason, now),
            )
        conn.commit()
    finally:
        conn.close()


def activate_resident_tax_notice(notice_id: int, db_path: Path = DB_PATH) -> None:
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT employee_code, fiscal_year, is_confirmed FROM resident_tax_notices WHERE id=?",
            (int(notice_id),),
        ).fetchone()
        if not row or not int(row["is_confirmed"] or 0):
            raise ValueError("確認済みの通知書だけを有効化できます。")
        conn.execute(
            "UPDATE resident_tax_notices SET is_active=0 WHERE employee_code=? AND fiscal_year=?",
            (row["employee_code"], row["fiscal_year"]),
        )
        conn.execute("UPDATE resident_tax_notices SET is_active=1 WHERE id=?", (int(notice_id),))
        conn.commit()
    finally:
        conn.close()


def get_confirmed_resident_tax(
    employee_code: str,
    payment_date: str | datetime | Any,
    db_path: Path = DB_PATH,
) -> dict[str, Any] | None:
    from payroll_core import resident_tax_year_and_month

    target_date = payment_date.date() if isinstance(payment_date, datetime) else payment_date
    fiscal_year, target_month = resident_tax_year_and_month(target_date)
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            """
            SELECT n.*, m.confirmed_amount
            FROM resident_tax_notices n
            JOIN resident_tax_monthly_amounts m ON m.notice_id=n.id
            WHERE n.employee_code=? AND n.fiscal_year=? AND n.is_confirmed=1
              AND n.is_active=1 AND m.deduction_month=?
            ORDER BY n.revision_number DESC LIMIT 1
            """,
            (str(employee_code), fiscal_year, target_month),
        ).fetchone()
        if not row:
            return None
        result = dict(row)
        result["target_month"] = target_month
        return result
    finally:
        conn.close()


def record_issued_file(
    payroll_history_id: int,
    file_type: str,
    path: Path,
    db_path: Path = DB_PATH,
) -> dict[str, Any]:
    init_db(db_path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    now = datetime.now().isoformat(timespec="seconds")
    conn = get_connection(db_path)
    try:
        current = conn.execute(
            "SELECT reissue_count FROM issued_files WHERE payroll_history_id=? AND file_type=?",
            (int(payroll_history_id), file_type),
        ).fetchone()
        count = int(current["reissue_count"] or 0) + 1 if current else 0
        conn.execute(
            """
            INSERT INTO issued_files
                (payroll_history_id,file_type,issued_at,reissue_count,filename,file_sha256,stored_path)
            VALUES (?,?,?,?,?,?,?)
            ON CONFLICT(payroll_history_id,file_type) DO UPDATE SET
                issued_at=excluded.issued_at,
                reissue_count=excluded.reissue_count,
                filename=excluded.filename,
                file_sha256=excluded.file_sha256,
                stored_path=excluded.stored_path
            """,
            (int(payroll_history_id), file_type, now, count, path.name, digest, str(path)),
        )
        conn.commit()
        return {"issued_at": now, "reissue_count": count, "filename": path.name, "sha256": digest, "path": str(path)}
    finally:
        conn.close()


def fetch_issued_files(payroll_history_id: int | None = None, db_path: Path = DB_PATH) -> list[dict[str, Any]]:
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        if payroll_history_id is None:
            rows = conn.execute("SELECT * FROM issued_files ORDER BY issued_at DESC").fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM issued_files WHERE payroll_history_id=? ORDER BY file_type",
                (int(payroll_history_id),),
            ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()
