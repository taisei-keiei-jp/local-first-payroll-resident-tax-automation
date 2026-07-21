from __future__ import annotations

from datetime import date
from io import BytesIO
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


def create_pdf_zip_bytes(pdf_paths: list[Path]) -> bytes:
    """正常に存在するPDFだけを、サブフォルダなしでメモリ上のZIPにまとめる。"""
    buffer = BytesIO()
    added_names: set[str] = set()
    with ZipFile(buffer, mode="w", compression=ZIP_DEFLATED) as archive:
        for path in pdf_paths:
            if path.suffix.lower() != ".pdf" or not path.is_file() or path.name in added_names:
                continue
            archive.write(path, arcname=path.name)
            added_names.add(path.name)
    return buffer.getvalue()


def payroll_documents_zip_filename(payment_month: date, company_name: str) -> str:
    legal_name = str(company_name).removesuffix("様")
    return f"{payment_month.year}年{payment_month.month}月分_{legal_name}_給与関連資料一式.zip"
