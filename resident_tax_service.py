from __future__ import annotations

import hashlib
import io
import json
import re
import shutil
import subprocess
import sys
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parent
NOTICE_DIR = BASE_DIR / "data" / "resident_tax_notices"
OCR_LOG_PATH = BASE_DIR / "logs" / "ocr_runtime.log"
SUPPORTED_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png"}
MONTHS = [
    "6月分", "7月分", "8月分", "9月分", "10月分", "11月分", "12月分",
    "翌年1月分", "翌年2月分", "翌年3月分", "翌年4月分", "翌年5月分",
]
with (BASE_DIR / "config" / "employee_master.json").open("r", encoding="utf-8") as _employee_file:
    _employee_master = json.load(_employee_file)
EMPLOYEES = {
    name: str(employee.get("employee_id", "")).zfill(6)
    for name, employee in _employee_master.items()
}
DOCUMENT_TYPE_LABELS = {
    "company_multi": "会社用・複数人",
    "individual_single": "個人用・1人",
    "unknown": "判定不能",
}


def normalize_text(value: str) -> str:
    return unicodedata.normalize("NFKC", value or "").replace("\u3000", " ")


def normalize_name(value: str) -> str:
    normalized = normalize_text(value).removesuffix("様")
    return re.sub(r"[^0-9A-Za-zぁ-んァ-ヶ一-龠々ー]", "", normalized)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def safe_filename(filename: str) -> str:
    name = Path(filename).name
    stem = re.sub(r"[^0-9A-Za-zぁ-んァ-ヶ一-龠々ー_.-]+", "_", Path(name).stem).strip("._") or "notice"
    return f"{stem}{Path(name).suffix.lower()}"


class PdfPasswordRequiredError(ValueError):
    """PDFの閲覧パスワードが必要な場合だけ利用者へ返す安全な例外。"""


class PdfProcessingError(ValueError):
    """内部ライブラリ名を画面へ出さないPDF処理例外。"""


def save_source_file(data: bytes, filename: str) -> tuple[Path, str, bool]:
    extension = Path(filename).suffix.lower()
    if extension not in SUPPORTED_EXTENSIONS:
        raise ValueError("PDF、JPG、JPEG、PNGだけ取り込めます。")
    digest = sha256_bytes(data)
    NOTICE_DIR.mkdir(parents=True, exist_ok=True)
    existing = next(NOTICE_DIR.glob(f"{digest[:16]}_*"), None)
    if existing and existing.is_file():
        return existing, digest, False
    path = NOTICE_DIR / f"{digest[:16]}_{safe_filename(filename)}"
    created = False
    if not path.exists():
        path.write_bytes(data)
        created = True
    return path, digest, created


def inspect_pdf_security(data: bytes, password: str | None = None) -> dict[str, Any]:
    """暗号化状態だけを安全に確認する。パスワードや本文はログへ残さない。"""
    status: dict[str, Any] = {
        "encrypted": False,
        "accessible": False,
        "accessible_for_text": False,
        "ocr_fallback_allowed": True,
        "auto_unlocked": False,
        "password_required": False,
        "password_invalid": False,
        "security_check": "not_attempted",
        "text_extraction": "not_attempted",
        "ocr_fallback": "not_used",
        "message": "",
    }
    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        status["encrypted"] = bool(reader.is_encrypted)
        if not reader.is_encrypted:
            status["accessible"] = True
            status["accessible_for_text"] = True
            status["security_check"] = "success"
            return status
        supplied = password if password is not None else ""
        result = reader.decrypt(supplied)
        if result:
            status["accessible"] = True
            status["accessible_for_text"] = True
            status["security_check"] = "success"
            status["auto_unlocked"] = supplied == ""
            return status
        status["password_required"] = True
        status["password_invalid"] = password is not None and password != ""
        status["ocr_fallback_allowed"] = False
        status["security_check"] = "password_required"
        status["message"] = "このPDFは暗号化されています。読み取るにはPDFのパスワードが必要です。"
        return status
    except ModuleNotFoundError as exc:
        _write_ocr_log("PDF暗号化状態の確認に必要なライブラリがありません", repr(exc))
        status["security_check"] = "text_provider_unavailable"
        status["ocr_fallback"] = "required"
        status["message"] = "PDF内テキストを確認できないため、画像OCRへ切り替えます。"
        return status
    except Exception as exc:
        _write_ocr_log("PDF暗号化状態の確認に失敗しました", f"type={type(exc).__name__}")
        # pypdfのAESプロバイダだけが失敗した場合でも、PDFiumが明示する
        # Incorrect password はパスワード必須の確定材料として利用できる。
        # それ以外のPDFiumエラーは、後続の画像化・OCRで改めて判定する。
        try:
            import pypdfium2 as pdfium

            supplied = password if password is not None else ""
            document = (
                pdfium.PdfDocument(data, password=supplied)
                if supplied
                else pdfium.PdfDocument(data)
            )
            document.close()
        except Exception as pdfium_exc:
            if "incorrect password" in str(pdfium_exc).lower():
                status["encrypted"] = True
                status["password_required"] = True
                status["password_invalid"] = password is not None and password != ""
                status["ocr_fallback_allowed"] = False
                status["security_check"] = "password_required"
                status["message"] = (
                    "このPDFは暗号化されています。読み取るにはPDFのパスワードが必要です。"
                )
                return status
        # AESプロバイダやpypdfだけの失敗でPDF全体を拒否しない。
        # 本当に画像化もできないかは、後続のpypdfium2で判定する。
        status["security_check"] = "text_provider_failed"
        status["ocr_fallback"] = "required"
        status["message"] = "PDF内テキストを確認できないため、画像OCRへ切り替えます。"
        return status


def _extract_pdf_text(
    data: bytes,
    password: str | None = None,
) -> tuple[str, list[str], dict[str, Any]]:
    warnings: list[str] = []
    status = inspect_pdf_security(data, password)
    if status["password_required"]:
        raise PdfPasswordRequiredError(status["message"])
    if not status.get("accessible_for_text", status["accessible"]):
        status["text_extraction"] = "failed"
        status["ocr_fallback"] = "required"
        return "", [status["message"] or "PDF内テキストを抽出できないため、画像OCRへ切り替えます。"], status
    try:
        from pypdf import PdfReader
    except ModuleNotFoundError:
        status["text_extraction"] = "failed"
        status["ocr_fallback"] = "required"
        return "", ["PDF内テキストを抽出できないため、画像OCRへ切り替えます。"], status
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            reader.decrypt(password if password is not None else "")
        pages = [(page.extract_text() or "") for page in reader.pages]
        text = "\n\n".join(pages)
        status["text_extraction"] = "success" if text.strip() else "empty"
        status["ocr_fallback"] = "not_used" if text.strip() else "required"
        return text, warnings, status
    except Exception as exc:
        _write_ocr_log("PDF内テキスト抽出に失敗しました", f"type={type(exc).__name__}")
        status["text_extraction"] = "failed"
        status["ocr_fallback"] = "required"
        return "", ["PDF内テキストを抽出できなかったため、画像OCRへ切り替えました。"], status


def _find_tesseract() -> str | None:
    bundled = BASE_DIR / "tools" / "tesseract" / "tesseract.exe"
    if bundled.is_file():
        return str(bundled)
    return shutil.which("tesseract")


def _write_ocr_log(event: str, detail: str) -> None:
    """利用者画面には出さないOCR診断情報をプロジェクト内へ記録する。"""
    try:
        OCR_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().isoformat(timespec="seconds")
        with OCR_LOG_PATH.open("a", encoding="utf-8") as log_file:
            log_file.write(f"[{timestamp}] {event}\n{detail.rstrip()}\n\n")
    except OSError:
        # ログ書込み失敗を理由に手動入力フォールバックまで停止させない。
        pass


def _ocr_failure(status: dict[str, Any], reason: str, detail: str) -> dict[str, Any]:
    status["reason"] = reason
    _write_ocr_log(reason, detail)
    return status


def ocr_status() -> dict[str, Any]:
    status: dict[str, Any] = {
        "available": False,
        "source": "未検出",
        "executable": "",
        "version": "",
        "languages": [],
        "jpn_available": False,
        "eng_available": False,
        "self_test": "未実行",
        "python_executable": sys.executable,
        "reason": "",
    }
    try:
        import pytesseract
        from PIL import Image, ImageDraw, ImageFont
    except ModuleNotFoundError as exc:
        if exc.name == "pytesseract":
            reason = "pytesseractが実行環境にありません"
        elif exc.name and (exc.name == "PIL" or exc.name.startswith("PIL.")):
            reason = "Pillowが実行環境にありません"
        else:
            reason = "OCRに必要なPythonライブラリが実行環境にありません"
        return _ocr_failure(status, reason, repr(exc))
    executable = _find_tesseract()
    if not executable:
        return _ocr_failure(
            status,
            "ローカルTesseract実行ファイルが見つかりません",
            f"python={sys.executable}",
        )
    executable_path = Path(executable).resolve()
    bundled_path = (BASE_DIR / "tools" / "tesseract" / "tesseract.exe").resolve()
    status["executable"] = str(executable_path)
    status["source"] = "プロジェクト同梱版" if executable_path == bundled_path else "PATH検出版"
    creationflags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
    try:
        version_result = subprocess.run(
            [str(executable_path), "--version"], capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=10, check=False,
            creationflags=creationflags,
        )
        version_lines = (version_result.stdout or version_result.stderr).splitlines()
        status["version"] = version_lines[0].strip() if version_lines else ""
        if version_result.returncode != 0:
            return _ocr_failure(
                status,
                "Tesseractが異常終了しました",
                f"command=--version returncode={version_result.returncode}\n"
                f"stdout={version_result.stdout}\nstderr={version_result.stderr}",
            )
        language_result = subprocess.run(
            [str(executable_path), "--list-langs"], capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=10, check=False,
            creationflags=creationflags,
        )
        if language_result.returncode != 0:
            return _ocr_failure(
                status,
                "Tesseractが異常終了しました",
                f"command=--list-langs returncode={language_result.returncode}\n"
                f"stdout={language_result.stdout}\nstderr={language_result.stderr}",
            )
        languages = [
            line.strip() for line in language_result.stdout.splitlines()
            if line.strip() and not line.lower().startswith("list of available languages")
        ]
        status["languages"] = languages
        status["jpn_available"] = "jpn" in languages
        status["eng_available"] = "eng" in languages
        if not status["jpn_available"]:
            return _ocr_failure(status, "日本語言語データjpnがありません", language_result.stdout)
        if not status["eng_available"]:
            return _ocr_failure(status, "英語言語データengがありません", language_result.stdout)

        # ファイルの存在確認だけではなく、実際に jpn+eng OCR を1回呼び出す。
        pytesseract.pytesseract.tesseract_cmd = str(executable_path)
        test_image = Image.new("RGB", (640, 160), "white")
        test_draw = ImageDraw.Draw(test_image)
        test_font = ImageFont.load_default(size=48)
        test_draw.text((24, 48), "OCR 123", fill="black", font=test_font)
        test_text = pytesseract.image_to_string(test_image, lang="jpn+eng", config="--psm 7")
        if not test_text.strip():
            return _ocr_failure(
                status,
                "Tesseractの簡易OCR呼出しに失敗しました",
                "self-test returned empty text",
            )
        status["self_test"] = "成功"
        status["available"] = True
    except subprocess.TimeoutExpired as exc:
        return _ocr_failure(status, "Tesseractの応答がタイムアウトしました", repr(exc))
    except OSError as exc:
        return _ocr_failure(
            status,
            "Tesseractに必要なランタイムまたはDLLが不足しています",
            repr(exc),
        )
    except (subprocess.SubprocessError, RuntimeError) as exc:
        return _ocr_failure(status, "Tesseractが異常終了しました", repr(exc))
    except Exception as exc:
        return _ocr_failure(status, "Tesseractの簡易OCR呼出しに失敗しました", repr(exc))
    return status


def ocr_available() -> tuple[bool, str]:
    status = ocr_status()
    if status["available"]:
        return True, str(status["executable"])
    return False, str(status["reason"] or "日本語OCRを利用できません")


def _image_objects(
    data: bytes,
    extension: str,
    password: str | None = None,
) -> list[Any]:
    from PIL import Image

    if extension == ".pdf":
        try:
            import pypdfium2 as pdfium
        except ModuleNotFoundError:
            return []
        document = pdfium.PdfDocument(data, password=password or None)
        try:
            return [page.render(scale=2.5).to_pil().convert("RGB").copy() for page in document]
        finally:
            document.close()
    return [Image.open(io.BytesIO(data)).convert("RGB")]


def _prepare_ocr_image(image: Any) -> Any:
    from PIL import Image, ImageOps

    prepared = ImageOps.autocontrast(ImageOps.grayscale(image))
    if prepared.width < 1800:
        scale = 1800 / max(prepared.width, 1)
        prepared = prepared.resize(
            (1800, max(1, round(prepared.height * scale))),
            resample=Image.Resampling.LANCZOS,
        )
    return prepared


def _ocr_text(
    data: bytes,
    extension: str,
    password: str | None = None,
) -> tuple[str, list[str], float | None]:
    available, detail = ocr_available()
    if not available:
        return "", [f"OCRは利用できません（{detail}）。プレビューを確認して手動入力できます。"], None
    import pytesseract

    pytesseract.pytesseract.tesseract_cmd = detail
    try:
        images = _image_objects(data, extension, password)
    except Exception as exc:
        _write_ocr_log("PDF画像化に失敗しました", f"type={type(exc).__name__}")
        return "", ["PDFを画像化できませんでした。プレビューを確認して手動入力してください。"], None
    if not images:
        return "", ["PDF画像化ライブラリがpypdfium2が利用できないためOCRできません。手動入力してください。"], None
    texts: list[str] = []
    for image in images:
        try:
            prepared = _prepare_ocr_image(image)
            texts.append(pytesseract.image_to_string(prepared, lang="jpn+eng", config="--psm 6"))
        except Exception as exc:
            _write_ocr_log("ローカルOCRに失敗しました", f"type={type(exc).__name__}")
            return "\n".join(texts), ["ローカルOCRに失敗しました。プレビューを確認して手動入力してください。"], None
    return "\n\n".join(texts), [], None


def _ocr_positioned_words(
    data: bytes,
    extension: str,
    password: str | None = None,
) -> tuple[list[dict[str, Any]], list[str], float | None]:
    """座標付きOCR。数字の出現順ではなく、氏名行と月セルの位置関係にだけ使う。"""
    available, detail = ocr_available()
    if not available:
        return [], [f"座標OCRは利用できません（{detail}）。"], None
    import pytesseract

    pytesseract.pytesseract.tesseract_cmd = detail
    try:
        images = _image_objects(data, extension, password)
    except Exception as exc:
        _write_ocr_log("座標OCR用PDF画像化に失敗しました", f"type={type(exc).__name__}")
        return [], ["PDFを画像化できないため座標OCRを実行できませんでした。"], None
    words: list[dict[str, Any]] = []
    confidences: list[float] = []
    try:
        for page_index, image in enumerate(images, start=1):
            prepared = _prepare_ocr_image(image)
            result = pytesseract.image_to_data(
                prepared,
                lang="jpn+eng",
                config="--psm 11",
                output_type=pytesseract.Output.DICT,
            )
            for index, raw_text in enumerate(result.get("text", [])):
                text = str(raw_text or "").strip()
                if not text:
                    continue
                confidence = float(result.get("conf", [0])[index] or 0)
                if confidence >= 0:
                    confidences.append(confidence)
                words.append(
                    {
                        "page": page_index,
                        "text": text,
                        "left": int(result["left"][index]),
                        "top": int(result["top"][index]),
                        "width": int(result["width"][index]),
                        "height": int(result["height"][index]),
                        "confidence": confidence,
                        "page_width": prepared.width,
                        "page_height": prepared.height,
                    }
                )
    except Exception as exc:
        _write_ocr_log("座標OCRに失敗しました", repr(exc))
        return [], ["座標OCRに失敗しました。プレビューを確認して手動入力してください。"], None
    mean_confidence = sum(confidences) / len(confidences) if confidences else None
    return words, [], mean_confidence


def _amount(value: str | None) -> int:
    if not value:
        return 0
    normalized = normalize_text(value).replace("円", "")
    normalized = re.sub(r"(?<=\d)[,.](?=\d{3}(?:\D|$))", "", normalized)
    match = re.search(r"\d+", normalized)
    return int(match.group()) if match else 0


def extract_fields(text: str) -> tuple[dict[str, Any], list[str]]:
    normalized = normalize_text(text)
    compact = re.sub(r"[ \t]", "", normalized)
    warnings: list[str] = []
    fiscal_match = re.search(r"令和\s*(\d+)\s*年度", normalized)
    fiscal_year = f"令和{int(fiscal_match.group(1))}年度" if fiscal_match else ""

    notice_name_match = re.search(
        r"(?:納税義務者氏名|氏名)\s*[:：]?\s*([^\n\r]+)", normalized
    )
    recognized_name = notice_name_match.group(1).strip() if notice_name_match else ""

    matched_names = [name for name in EMPLOYEES if normalize_name(name) in normalize_name(normalized)]
    employee_name = matched_names[0] if len(matched_names) == 1 else ""
    employee_code = EMPLOYEES.get(employee_name, "")
    code_match = re.search(r"(?:従業員コード|社員コード|コード)\s*[:：]?\s*(\d{6})", normalized)
    if code_match and code_match.group(1) in EMPLOYEES.values():
        code_name = next(name for name, code in EMPLOYEES.items() if code == code_match.group(1))
        if employee_name and employee_name != code_name:
            warnings.append("氏名と従業員コードの照合結果が一致しません。")
        else:
            employee_name, employee_code = code_name, code_match.group(1)

    municipality_match = re.search(r"(?:市区町村(?:名)?|自治体)\s*[:：]?\s*([^\n\r]+)", normalized)
    municipality = municipality_match.group(1).strip() if municipality_match else ""
    designation_match = re.search(r"(?:指定番号|事業所番号)\s*[:：]?\s*([^\s\n\r]+)", normalized)
    annual_match = re.search(r"(?:年税額|特別徴収税額)\s*[:：]?\s*([0-9,.]+)\s*円?", normalized)
    annual_amount = _amount(annual_match.group(1)) if annual_match else None

    monthly: dict[str, int | None] = {}
    for month in MONTHS:
        label = re.escape(month).replace("翌年", r"(?:翌年)?") if month.startswith("翌年") else re.escape(month)
        match = re.search(label + r"\s*[:：]?\s*([0-9,.]+)\s*円?", compact)
        monthly[month] = _amount(match.group(1)) if match else None
    same_match = re.search(r"7月(?:分)?\s*(?:から|~|～|－|-)\s*翌年?5月(?:分)?\s*(?:同額|各月)?\s*[:：]?\s*([0-9,.]+)", compact)
    if same_match:
        same_amount = _amount(same_match.group(1))
        for month in MONTHS[1:]:
            if monthly[month] is None:
                monthly[month] = same_amount

    if not fiscal_year:
        warnings.append("年度を自動認識できませんでした。")
    if not employee_name:
        warnings.append("氏名を従業員マスターの対象者へ自動照合できませんでした。")
    if not municipality:
        warnings.append("市区町村名を自動認識できませんでした。")
    if annual_amount is None:
        warnings.append("年税額を自動認識できませんでした。")
    if any(monthly[month] is None for month in MONTHS):
        warnings.append("自動認識できない月額があります。元資料を確認してください。")
    if annual_amount is not None and all(value is not None for value in monthly.values()) and sum(monthly.values()) != annual_amount:
        warnings.append("月別合計と年税額が一致しません。")

    return {
        "employee_code": employee_code,
        "employee_name": employee_name,
        "recognized_name": recognized_name or employee_name,
        "fiscal_year": fiscal_year,
        "municipality": municipality,
        "designation_number": designation_match.group(1).strip() if designation_match else "",
        "annual_amount": annual_amount,
        "monthly_amounts": monthly,
        "notes": "",
    }, warnings


def document_type_label(document_type: str) -> str:
    return DOCUMENT_TYPE_LABELS.get(document_type, DOCUMENT_TYPE_LABELS["unknown"])


def detect_document_type(text: str, matched_names: list[str] | None = None) -> str:
    compact = normalize_name(text)
    names = matched_names if matched_names is not None else [
        name for name in EMPLOYEES if normalize_name(name) in compact
    ]
    if "特別徴収義務者用" in compact or len(set(names)) >= 2:
        return "company_multi"
    if "納税義務者用" in compact or len(set(names)) == 1:
        return "individual_single"
    return "unknown"


def _extract_fiscal_year(text: str) -> str:
    normalized = normalize_text(text)
    match = re.search(r"令和\s*([0-9０-９]+)\s*年度", normalized)
    if not match:
        match = re.search(r"令和\s*([0-9０-９]+)", normalized)
    return f"令和{int(normalize_text(match.group(1)))}年度" if match else ""


def _extract_municipality(text: str) -> str:
    normalized = normalize_text(text)
    explicit = re.search(r"(?:市区町村(?:名)?|自治体)\s*[:：]?\s*([^\s\n\r]+(?:市|区|町|村))", normalized)
    if explicit and _valid_municipality(explicit.group(1)):
        return explicit.group(1)
    candidates = []
    for pattern in [
        r"([一-龠々ヶ]{1,12}(?:市|区|町|村))\s*長",
        r"令和\s*[0-9０-９]+\s*(?:年度)?\s+([一-龠々ヶ]{1,12}(?:市|区|町|村))",
    ]:
        candidates.extend(match.group(1) for match in re.finditer(pattern, normalized))
    for candidate in candidates:
        if candidate not in {"市町村", "市区町村"}:
            return candidate
    return ""


_MUNICIPALITY_EXCLUDED_TERMS = (
    "所得割", "均等割", "森林環境税", "市民税", "県民税", "都民税", "府民税", "道民税",
    "控除", "税額", "課税", "所得", "納付", "特別徴収", "普通徴収",
)


def _valid_municipality(value: str | None) -> bool:
    compact = re.sub(r"\s+", "", normalize_text(value or ""))
    if not compact or any(term in compact for term in _MUNICIPALITY_EXCLUDED_TERMS):
        return False
    return bool(re.fullmatch(r"[ぁ-んァ-ヶ一-龠々ヶー]{1,12}(?:市|区|町|村)", compact))


def _clean_municipality(value: str | None) -> str:
    compact = re.sub(r"\s+", "", normalize_text(value or ""))
    return compact if _valid_municipality(compact) else ""


def _month_name(label: str) -> str | None:
    compact = re.sub(r"[\s\u3000]+", "", normalize_text(label))
    match = re.fullmatch(r"(1[0-2]|[1-9])月(?:分)?", compact)
    if not match:
        return None
    number = int(match.group(1))
    return f"{number}月分" if number >= 6 else f"翌年{number}月分"


def _numeric_cell(value: str | None) -> int | None:
    if not value or not re.search(r"[0-9０-９]", value):
        return None
    compact = normalize_text(value).replace("円", "")
    compact = re.sub(r"[\s,，.．|｜:：'’`´()（）\[\]{}]+", "", compact)
    if not re.fullmatch(r"[0-9]+", compact):
        return None
    return _amount(compact)


def _field_warnings(fields: dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    if not fields.get("employee_name"):
        warnings.append("氏名を従業員マスターの対象者へ自動照合できませんでした。")
    if not fields.get("fiscal_year"):
        warnings.append("年度を自動認識できませんでした。")
    if not fields.get("municipality"):
        warnings.append("市区町村名を自動認識できませんでした。")
    annual_raw = fields.get("annual_amount")
    annual = int(annual_raw or 0)
    if annual_raw is None:
        warnings.append("年税額を自動認識できませんでした。")
    monthly = fields.get("monthly_amounts") or {}
    unread = [month for month in MONTHS if month not in monthly or monthly.get(month) is None]
    if unread:
        warnings.append("12か月のうち自動認識できない月があります。元資料を確認してください。")
    values = [int(monthly.get(month) or 0) for month in MONTHS]
    if any(0 < value < 100 for value in values):
        warnings.append("100円未満の月額があり、誤読の可能性が高いため確認が必要です。")
    total = sum(values)
    if annual_raw is not None and annual == 0 and total > 0:
        warnings.append("年税額が0円ですが月別税額が存在します。")
    if annual_raw is not None and not unread and total != annual:
        warnings.append("月別合計と年税額が一致しません。")
    return warnings


def _extract_table_fields(
    table_data: list[list[str | None]],
    raw_text: str,
    fiscal_year: str,
    municipality: str,
) -> dict[str, Any] | None:
    flattened = "\n".join(str(cell or "") for row in table_data for cell in row)
    normalized_table = normalize_name(flattened)
    matched_names = [name for name in EMPLOYEES if normalize_name(name) in normalized_table]
    if len(matched_names) != 1:
        return None
    employee_name = matched_names[0]
    monthly: dict[str, int] = {}
    annual_amount: int | None = None
    annual_label_used: dict[str, Any] | None = None
    designation_number = ""
    recognized_name = employee_name
    for row in table_data:
        for index, cell in enumerate(row):
            text = str(cell or "")
            month = _month_name(text)
            if month and index + 1 < len(row):
                amount = _numeric_cell(row[index + 1])
                if amount is not None:
                    monthly[month] = amount
            compact = normalize_name(text)
            if ("特別徴収税額" in compact or "年税額" in compact) and index + 1 < len(row):
                amount = _numeric_cell(row[index + 1])
                if amount is not None:
                    annual_amount = amount
            if "指定番号" in compact and index + 1 < len(row):
                designation_number = normalize_text(str(row[index + 1] or "")).strip()
    return {
        "employee_code": EMPLOYEES[employee_name],
        "employee_name": employee_name,
        "recognized_name": recognized_name,
        "fiscal_year": fiscal_year,
        "municipality": municipality,
        "designation_number": designation_number,
        "annual_amount": annual_amount,
        "monthly_amounts": monthly,
        "notes": "",
        "extraction_method": "PDF表セル位置抽出",
    }


def _individual_fields_complete(fields: dict[str, Any], require_total_match: bool = True) -> bool:
    monthly = fields.get("monthly_amounts") or {}
    values = [monthly.get(month) for month in MONTHS]
    annual = fields.get("annual_amount")
    if not fields.get("fiscal_year"):
        return False
    if not (fields.get("employee_name") or fields.get("recognized_name")):
        return False
    if not _valid_municipality(fields.get("municipality")):
        return False
    if annual is None or any(value is None for value in values):
        return False
    amounts = [int(value) for value in values]
    if int(annual) != 0 and all(value == 0 for value in amounts):
        return False
    return not require_total_match or sum(amounts) == int(annual)


def _individual_fields_score(fields: dict[str, Any]) -> int:
    monthly = fields.get("monthly_amounts") or {}
    values = [monthly.get(month) for month in MONTHS]
    score = sum(value is not None for value in values) * 2
    score += 5 if fields.get("annual_amount") is not None else 0
    score += 3 if _valid_municipality(fields.get("municipality")) else 0
    score += 2 if fields.get("fiscal_year") else 0
    score += 2 if fields.get("employee_name") else 0
    if fields.get("annual_amount") is not None and all(value is not None for value in values):
        score += 6 if sum(int(value) for value in values) == int(fields["annual_amount"]) else 0
    return score


def _individual_fields_conflict(first: dict[str, Any], second: dict[str, Any]) -> bool:
    for key in ("annual_amount", "fiscal_year", "employee_code"):
        first_value = first.get(key)
        second_value = second.get(key)
        if first_value not in (None, "") and second_value not in (None, "") and first_value != second_value:
            return True
    first_municipality = _clean_municipality(first.get("municipality"))
    second_municipality = _clean_municipality(second.get("municipality"))
    if first_municipality and second_municipality and first_municipality != second_municipality:
        return True
    first_monthly = first.get("monthly_amounts") or {}
    second_monthly = second.get("monthly_amounts") or {}
    return any(
        first_monthly.get(month) is not None
        and second_monthly.get(month) is not None
        and int(first_monthly[month]) != int(second_monthly[month])
        for month in MONTHS
    )


def _merge_individual_fields(*candidates: dict[str, Any] | None) -> dict[str, Any] | None:
    available = [candidate for candidate in candidates if candidate]
    if not available:
        return None
    ranked = sorted(available, key=_individual_fields_score, reverse=True)
    merged = dict(ranked[0])
    merged["monthly_amounts"] = dict(merged.get("monthly_amounts") or {})
    methods: list[str] = []
    annual_candidates: list[int] = []
    for candidate in ranked:
        method = str(candidate.get("extraction_method") or "")
        if method and method not in methods:
            methods.append(method)
        for key in (
            "employee_code", "employee_name", "recognized_name", "fiscal_year",
            "designation_number", "notes",
        ):
            if not merged.get(key) and candidate.get(key):
                merged[key] = candidate[key]
        municipality = _clean_municipality(candidate.get("municipality"))
        if not _valid_municipality(merged.get("municipality")) and municipality:
            merged["municipality"] = municipality
        for month, value in (candidate.get("monthly_amounts") or {}).items():
            if month in MONTHS and merged["monthly_amounts"].get(month) is None and value is not None:
                merged["monthly_amounts"][month] = int(value)
        if candidate.get("annual_amount") is not None:
            annual_candidates.append(int(candidate["annual_amount"]))
    values = [merged["monthly_amounts"].get(month) for month in MONTHS]
    if all(value is not None for value in values):
        total = sum(int(value) for value in values)
        matching = next((value for value in annual_candidates if value == total), None)
        if matching is not None:
            merged["annual_amount"] = matching
    if merged.get("annual_amount") is None and annual_candidates:
        merged["annual_amount"] = annual_candidates[0]
    merged["municipality"] = _clean_municipality(merged.get("municipality"))
    if methods:
        merged["extraction_method"] = "＋".join(methods)
    return merged


def _table_label_locations(
    table_data: list[list[str | None]],
    matcher: Any,
    max_parts: int,
) -> list[tuple[int, int, int, int]]:
    locations: list[tuple[int, int, int, int]] = []
    seen: set[tuple[int, int, int, int]] = set()
    for row_index, row in enumerate(table_data):
        for column_index in range(len(row)):
            for direction in ("horizontal", "vertical"):
                pieces: list[str] = []
                for offset in range(max_parts):
                    target_row = row_index + (offset if direction == "vertical" else 0)
                    target_column = column_index + (offset if direction == "horizontal" else 0)
                    if target_row >= len(table_data) or target_column >= len(table_data[target_row]):
                        break
                    value = str(table_data[target_row][target_column] or "")
                    if not value.strip():
                        break
                    pieces.append(value)
                    compact = re.sub(r"[\s\u3000]+", "", normalize_text("".join(pieces)))
                    if matcher(compact):
                        location = (row_index, column_index, target_row, target_column)
                        if location not in seen:
                            seen.add(location)
                            locations.append(location)
    return locations


def _adjacent_table_amounts(
    table_data: list[list[str | None]],
    location: tuple[int, int, int, int],
) -> list[int]:
    row0, column0, row1, column1 = location
    coordinates: list[tuple[int, int]] = []
    if row0 == row1:
        coordinates.append((row1, column1 + 1))
        coordinates.append((row0, column0 - 1))
        coordinates.extend((row1 + 1, column) for column in range(column0, column1 + 1))
        coordinates.extend((row0 - 1, column) for column in range(column0, column1 + 1))
    if column0 == column1:
        coordinates.append((row1 + 1, column1))
        coordinates.append((row0 - 1, column0))
        coordinates.extend((row, column1 + 1) for row in range(row0, row1 + 1))
        coordinates.extend((row, column0 - 1) for row in range(row0, row1 + 1))
    amounts: list[int] = []
    for row, column in coordinates:
        if row < 0 or row >= len(table_data) or column < 0 or column >= len(table_data[row]):
            continue
        amount = _numeric_cell(table_data[row][column])
        if amount is not None and amount not in amounts:
            amounts.append(amount)
    return amounts


def _annual_label(value: str) -> bool:
    compact = re.sub(r"[\s\u3000]+", "", normalize_text(value))
    compact = re.sub(r"(?:[①-⑳]|[0-9]{1,2})$", "", compact)
    excluded = (
        "税額控除前所得割額", "税額控除額", "所得割額", "均等割額", "森林環境税額",
        "控除不足額", "既充当額", "差引納付額", "給与収入", "所得金額", "課税標準額",
    )
    if any(term in compact for term in excluded):
        return False
    targets = ("特別徴収税額", "特別徴収年税額", "年税額")
    if compact in targets:
        return True
    # OCRの1文字誤認識だけを許容する。短い一般語や他の税額欄は対象にしない。
    return len(compact) >= 6 and max(
        SequenceMatcher(None, compact, target).ratio() for target in targets
    ) >= 0.8


def _extract_individual_table_fields(
    table_data: list[list[str | None]],
    raw_text: str,
    fiscal_year: str,
    municipality: str,
) -> dict[str, Any] | None:
    """個人用だけ、表セルの右隣・下隣と分割見出しを使って従来抽出を補完する。"""
    base = _extract_table_fields(table_data, raw_text, fiscal_year, municipality)
    if not base:
        return None
    monthly = dict(base.get("monthly_amounts") or {})
    for location in _table_label_locations(table_data, _month_name, 3):
        row0, column0, row1, column1 = location
        label = "".join(
            str(table_data[row][column] or "")
            for row, column in (
                [(row0, column) for column in range(column0, column1 + 1)]
                if row0 == row1
                else [(row, column0) for row in range(row0, row1 + 1)]
            )
        )
        month = _month_name(label)
        amounts = _adjacent_table_amounts(table_data, location)
        if month and amounts and monthly.get(month) is None:
            monthly[month] = amounts[0]
    annual_candidates: list[int] = []
    if base.get("annual_amount") is not None:
        annual_candidates.append(int(base["annual_amount"]))
    for location in _table_label_locations(table_data, _annual_label, 8):
        annual_candidates.extend(
            value for value in _adjacent_table_amounts(table_data, location)
            if value not in annual_candidates
        )
    annual_amount = base.get("annual_amount")
    values = [monthly.get(month) for month in MONTHS]
    if all(value is not None for value in values):
        total = sum(int(value) for value in values)
        annual_amount = next((value for value in annual_candidates if value == total), annual_amount)
    if annual_amount is None and annual_candidates:
        annual_amount = annual_candidates[0]
    municipality_value = ""
    municipality_labels = _table_label_locations(
        table_data,
        lambda value: value in {"市区町村", "市区町村名", "自治体", "自治体名"},
        5,
    )
    for location in municipality_labels:
        row0, column0, row1, column1 = location
        candidates: list[str] = []
        if row0 == row1 and column1 + 1 < len(table_data[row1]):
            candidates.append(str(table_data[row1][column1 + 1] or ""))
        if row1 + 1 < len(table_data) and column1 < len(table_data[row1 + 1]):
            candidates.append(str(table_data[row1 + 1][column1] or ""))
        municipality_value = next(
            (_clean_municipality(value) for value in candidates if _clean_municipality(value)),
            "",
        )
        if municipality_value:
            break
    if not municipality_value:
        municipality_value = _clean_municipality(base.get("municipality"))
    fields = dict(base)
    fields.update(
        {
            "municipality": municipality_value,
            "annual_amount": annual_amount,
            "monthly_amounts": monthly,
            "extraction_method": "PDF表セル位置抽出",
        }
    )
    return fields


def _word_box(word: dict[str, Any]) -> dict[str, Any]:
    x0 = float(word.get("x0", word.get("left", 0)))
    top = float(word.get("top", 0))
    x1 = float(word.get("x1", x0 + float(word.get("width", 0))))
    bottom = float(word.get("bottom", top + float(word.get("height", 0))))
    return {**word, "x0": x0, "top": top, "x1": x1, "bottom": bottom}


def _combined_box(parts: list[dict[str, Any]], label: str) -> dict[str, Any]:
    return {
        "text": "".join(str(part.get("text") or "") for part in parts),
        "x0": min(float(part["x0"]) for part in parts),
        "top": min(float(part["top"]) for part in parts),
        "x1": max(float(part["x1"]) for part in parts),
        "bottom": max(float(part["bottom"]) for part in parts),
        "label": label,
    }


def _positioned_label_candidates(
    words: list[dict[str, Any]],
    matcher: Any,
    max_parts: int,
) -> list[dict[str, Any]]:
    boxes = [_word_box(word) for word in words]
    candidates: list[dict[str, Any]] = []
    seen: set[tuple[float, float, float, float, str]] = set()

    def add(parts: list[dict[str, Any]]) -> None:
        compact = re.sub(
            r"[\s\u3000]+", "", normalize_text("".join(str(part.get("text") or "") for part in parts))
        )
        label = matcher(compact)
        if not label:
            return
        box = _combined_box(parts, str(label))
        key = (box["x0"], box["top"], box["x1"], box["bottom"], box["label"])
        if key not in seen:
            seen.add(key)
            candidates.append(box)

    for start in boxes:
        add([start])
        horizontal = sorted(
            [
                box for box in boxes
                if box is not start
                and float(box["x0"]) >= float(start["x0"])
                and abs(
                    (float(box["top"]) + float(box["bottom"])) / 2
                    - (float(start["top"]) + float(start["bottom"])) / 2
                ) <= max(6.0, float(start["bottom"]) - float(start["top"]))
            ],
            key=lambda box: float(box["x0"]),
        )
        parts = [start]
        previous = start
        for box in horizontal[: max_parts - 1]:
            gap = float(box["x0"]) - float(previous["x1"])
            if gap < -1 or gap > max(24.0, (float(previous["bottom"]) - float(previous["top"])) * 2.5):
                break
            parts.append(box)
            add(parts)
            previous = box
        vertical = sorted(
            [
                box for box in boxes
                if box is not start
                and float(box["top"]) >= float(start["top"])
                and abs(
                    (float(box["x0"]) + float(box["x1"])) / 2
                    - (float(start["x0"]) + float(start["x1"])) / 2
                ) <= max(8.0, float(start["x1"]) - float(start["x0"]))
            ],
            key=lambda box: float(box["top"]),
        )
        parts = [start]
        previous = start
        for box in vertical[: max_parts - 1]:
            gap = float(box["top"]) - float(previous["bottom"])
            if gap < -1 or gap > max(24.0, (float(previous["bottom"]) - float(previous["top"])) * 2.5):
                break
            parts.append(box)
            add(parts)
            previous = box
    return candidates


def _directional_amount_candidates(
    words: list[dict[str, Any]],
    label: dict[str, Any],
    page_width: float,
    page_height: float,
    excluded_labels: list[dict[str, Any]] | None = None,
) -> dict[str, list[tuple[float, int, dict[str, Any]]]]:
    boxes = [_word_box(word) for word in words]
    label_height = max(1.0, float(label["bottom"]) - float(label["top"]))
    label_width = max(1.0, float(label["x1"]) - float(label["x0"]))
    label_center_y = (float(label["top"]) + float(label["bottom"])) / 2
    label_center_x = (float(label["x0"]) + float(label["x1"])) / 2
    candidates: dict[str, list[tuple[float, int, dict[str, Any]]]] = defaultdict(list)
    excluded = excluded_labels or [label]
    for word in boxes:
        amount = _numeric_cell(str(word.get("text") or ""))
        if amount is None:
            continue
        if any(
            float(word["x0"]) < float(item["x1"])
            and float(word["x1"]) > float(item["x0"])
            and float(word["top"]) < float(item["bottom"])
            and float(word["bottom"]) > float(item["top"])
            for item in excluded
        ):
            continue
        word_center_y = (float(word["top"]) + float(word["bottom"])) / 2
        word_center_x = (float(word["x0"]) + float(word["x1"])) / 2
        same_row = abs(word_center_y - label_center_y) <= max(
            label_height, float(word["bottom"]) - float(word["top"])
        )
        same_column = abs(word_center_x - label_center_x) <= max(
            label_width, float(word["x1"]) - float(word["x0"])
        )
        horizontal_distance = min(
            abs(float(word["x0"]) - float(label["x1"])),
            abs(float(label["x0"]) - float(word["x1"])),
        )
        vertical_distance = min(
            abs(float(word["top"]) - float(label["bottom"])),
            abs(float(label["top"]) - float(word["bottom"])),
        )
        if same_row and horizontal_distance <= max(page_width * 0.3, label_width * 8.0):
            direction = "right" if float(word["x0"]) >= float(label["x1"]) else "left"
            candidates[direction].append(
                (horizontal_distance / max(page_width, 1.0), amount, word)
            )
        elif same_column and vertical_distance <= max(page_height * 0.15, label_height * 10.0):
            direction = "down" if float(word["top"]) >= float(label["bottom"]) else "up"
            candidates[direction].append(
                (vertical_distance / max(page_height, 1.0), amount, word)
            )
    for direction in candidates:
        candidates[direction].sort(key=lambda item: item[0])
    return candidates


def _positioned_amount_candidate(
    words: list[dict[str, Any]],
    label: dict[str, Any],
    page_width: float,
    page_height: float,
    excluded_labels: list[dict[str, Any]] | None = None,
) -> tuple[int, dict[str, Any], float] | None:
    """行列を固定せず、見出しの上下左右にある最寄り金額候補を返す。"""
    candidates = _directional_amount_candidates(
        words, label, page_width, page_height, excluded_labels
    )
    for direction in ("right", "left", "down", "up"):
        if candidates.get(direction):
            score, amount, word = candidates[direction][0]
            return amount, word, score
    return None


def _positioned_row_amount(
    words: list[dict[str, Any]],
    label: dict[str, Any],
    max_x: float,
) -> int | None:
    """互換用ラッパー。max_xはページ幅として扱い、上下左右を探索する。"""
    page_height = max(
        [float(_word_box(word)["bottom"]) for word in words] + [float(label["bottom"]) + 1.0]
    )
    candidate = _positioned_amount_candidate(words, label, max_x, page_height)
    return candidate[0] if candidate else None


def _extract_individual_pdf_page(
    page: Any,
    raw_text: str,
    fiscal_year: str,
    municipality: str,
) -> dict[str, Any] | None:
    """個人用帳票を座標で補助抽出する。部分結果は上位で表セル/OCRと統合する。"""
    words = page.extract_words(x_tolerance=2, y_tolerance=2, keep_blank_chars=False)
    page_raw_text = page.extract_text() or ""
    page_text = normalize_name(page_raw_text)
    matched_names = [name for name in EMPLOYEES if normalize_name(name) in page_text]
    if len(matched_names) != 1:
        return None
    employee_name = matched_names[0]
    month_labels = _positioned_label_candidates(words, _month_name, 3)
    monthly: dict[str, int] = {}
    for label in month_labels:
        candidate = _positioned_amount_candidate(
            words,
            label,
            float(page.width),
            float(page.height),
            month_labels,
        )
        if candidate is not None:
            monthly[str(label["label"])] = candidate[0]

    annual_amount: int | None = None
    annual_label_used: dict[str, Any] | None = None
    annual_labels = _positioned_label_candidates(
        words,
        lambda value: value if _annual_label(value) else None,
        8,
    )
    annual_candidates: list[tuple[int, dict[str, Any]]] = []
    for label in annual_labels:
        candidate = _positioned_amount_candidate(
            words,
            label,
            float(page.width),
            float(page.height),
            month_labels + annual_labels,
        )
        if candidate is not None:
            annual_candidates.append((candidate[0], label))
    if annual_candidates:
        plausible_annuals = [candidate for candidate in annual_candidates if candidate[0] >= 100]
        if plausible_annuals:
            annual_candidates = plausible_annuals
        monthly_values = [monthly.get(month) for month in MONTHS]
        monthly_total = (
            sum(int(value) for value in monthly_values)
            if all(value is not None for value in monthly_values)
            else None
        )
        annual_amount, annual_label_used = next(
            ((amount, label) for amount, label in annual_candidates if amount == monthly_total),
            annual_candidates[0],
        )
    if not monthly and annual_amount is None:
        return None

    relevant = month_labels + ([annual_label_used] if annual_label_used else [])
    region = {}
    if relevant:
        region = {
            "page": int(page.page_number),
            "x0": max(0.0, min(float(word["x0"]) for word in relevant) - 8.0),
            "top": max(0.0, min(float(word["top"]) for word in relevant) - 8.0),
            "x1": float(page.width),
            "bottom": min(float(page.height), max(float(word["bottom"]) for word in relevant) + 12.0),
            "page_width": float(page.width),
            "page_height": float(page.height),
            "individual_month_table": True,
        }
    fields = {
        "employee_code": EMPLOYEES[employee_name],
        "employee_name": employee_name,
        "recognized_name": employee_name,
        "fiscal_year": fiscal_year,
        "municipality": _clean_municipality(municipality) or _clean_municipality(_extract_municipality(page_raw_text)),
        "designation_number": "",
        "annual_amount": annual_amount,
        "monthly_amounts": monthly,
        "notes": "",
        "extraction_method": "PDF個人用月別税額表位置抽出",
    }
    diagnostics = []
    if annual_amount is None:
        diagnostics.append("annual amount missing")
    if len(monthly) != len(MONTHS):
        diagnostics.append(f"monthly amount count不足: {len(monthly)}/{len(MONTHS)}")
    return {"fields": fields, "region": region, "diagnostics": diagnostics}


def _extract_pdf_table_notices(
    data: bytes,
    raw_text: str,
    password: str | None = None,
) -> tuple[list[dict[str, Any]], int, list[str]]:
    try:
        import pdfplumber
    except ModuleNotFoundError:
        return [], 1, ["pdfplumberが利用できないため、PDF表セルの位置抽出を実行できません。"]
    fiscal_year = _extract_fiscal_year(raw_text)
    municipality = _extract_municipality(raw_text)
    notices: list[dict[str, Any]] = []
    warnings: list[str] = []
    try:
        with pdfplumber.open(io.BytesIO(data), password=password or "") as document:
            page_count = len(document.pages)
            for page_number, page in enumerate(document.pages, start=1):
                page_text = normalize_name(page.extract_text() or "")
                page_names = [name for name in EMPLOYEES if normalize_name(name) in page_text]
                if len(page_names) == 1:
                    individual_candidates: list[dict[str, Any]] = []
                    for table_index, table in enumerate(page.find_tables()):
                        table_data = table.extract()
                        fields = _extract_individual_table_fields(
                            table_data, raw_text, fiscal_year, municipality
                        )
                        if not fields:
                            continue
                        bbox = table.bbox
                        individual_candidates.append(
                            {
                                "fields": fields,
                                "region": {
                                    "page": page_number,
                                    "x0": max(0.0, float(bbox[0]) - 5.0),
                                    "top": max(0.0, float(bbox[1]) - 5.0),
                                    "x1": min(float(page.width), float(bbox[2]) + 5.0),
                                    "bottom": min(float(page.height), float(bbox[3]) + 5.0),
                                    "page_width": float(page.width),
                                    "page_height": float(page.height),
                                    "table_index": table_index,
                                },
                            }
                        )
                    try:
                        positioned = _extract_individual_pdf_page(
                            page, raw_text, fiscal_year, municipality
                        )
                    except Exception as exc:
                        _write_ocr_log(
                            "個人用PDF位置抽出に失敗しました",
                            f"type={type(exc).__name__}",
                        )
                        positioned = None
                        warnings.append("個人用通知書の位置抽出が不完全なため、表セルまたはOCRで補完します。")
                    if positioned:
                        individual_candidates.append(positioned)
                    extraction_conflict = any(
                        _individual_fields_conflict(first["fields"], second["fields"])
                        for index, first in enumerate(individual_candidates)
                        for second in individual_candidates[index + 1:]
                    )
                    merged_fields = _merge_individual_fields(
                        *(candidate["fields"] for candidate in individual_candidates)
                    )
                    if merged_fields:
                        best_candidate = max(
                            individual_candidates,
                            key=lambda candidate: _individual_fields_score(candidate["fields"]),
                        )
                        if not _individual_fields_complete(merged_fields):
                            warnings.append("個人用通知書の表抽出が不完全なため、画像OCRで補完します。")
                        if extraction_conflict:
                            warnings.append("個人用通知書の表セル抽出と位置抽出に相違があるため、画像OCRで再確認します。")
                        notices.append(
                            {
                                "fields": merged_fields,
                                "warnings": _field_warnings(merged_fields),
                                "confidence": None,
                                "page_number": page_number,
                                "region": best_candidate.get("region") or {},
                                "extraction_conflict": extraction_conflict,
                            }
                        )
                        continue
                # 会社用ページは従来どおり、従業員別の表セルをそのまま抽出する。
                for table_index, table in enumerate(page.find_tables()):
                    table_data = table.extract()
                    fields = _extract_table_fields(table_data, raw_text, fiscal_year, municipality)
                    if not fields:
                        continue
                    bbox = table.bbox
                    region = {
                        "page": page_number,
                        "x0": max(0.0, float(bbox[0]) - 5.0),
                        "top": max(0.0, float(bbox[1]) - 5.0),
                        "x1": min(float(page.width), float(bbox[2]) + 5.0),
                        "bottom": min(float(page.height), float(bbox[3]) + 5.0),
                        "page_width": float(page.width),
                        "page_height": float(page.height),
                        "table_index": table_index,
                    }
                    notices.append(
                        {
                            "fields": fields,
                            "warnings": _field_warnings(fields),
                            "confidence": None,
                            "page_number": page_number,
                            "region": region,
                        }
                    )
            return notices, page_count, warnings
    except Exception as exc:
        _write_ocr_log("PDF表セル位置抽出に失敗しました", repr(exc))
        return [], 1, ["PDF表セルの位置抽出に失敗したため、文字抽出または手動入力へ切り替えました。"]


def _group_ocr_lines(words: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    groups: dict[tuple[int, int], list[dict[str, Any]]] = defaultdict(list)
    for word in words:
        unit = max(8, int(word.get("height") or 8))
        groups[(int(word["page"]), round(int(word["top"]) / unit))].append(word)
    return [sorted(group, key=lambda item: int(item["left"])) for group in groups.values()]


class _OcrPageAdapter:
    def __init__(self, words: list[dict[str, Any]], page_number: int) -> None:
        self._words = [_word_box(word) for word in words]
        self.page_number = page_number
        self.width = max((float(word.get("page_width") or word["x1"]) for word in self._words), default=1.0)
        self.height = max((float(word.get("page_height") or word["bottom"]) for word in self._words), default=1.0)

    def extract_words(self, **_: Any) -> list[dict[str, Any]]:
        return self._words

    def extract_text(self) -> str:
        return "\n".join(
            " ".join(str(item.get("text") or "") for item in line)
            for line in _group_ocr_lines(self._words)
        )


def _targeted_ocr_amount(image: Any, word: dict[str, Any]) -> int | None:
    """月・年税額見出しに結び付いた金額セルだけを局所再OCRする。"""
    import pytesseract
    from PIL import Image

    source_width = max(float(word.get("page_width") or image.width), 1.0)
    source_height = max(float(word.get("page_height") or image.height), 1.0)
    scale_x = image.width / source_width
    scale_y = image.height / source_height
    height = max(float(word.get("height") or 1), 1.0)
    left = max(0, round((float(word.get("left", word.get("x0", 0))) - height * 2.0) * scale_x))
    top = max(0, round((float(word.get("top", 0)) - height * 0.7) * scale_y))
    right = min(
        image.width,
        round((float(word.get("left", word.get("x0", 0))) + float(word.get("width", 0)) + height * 2.0) * scale_x),
    )
    bottom = min(
        image.height,
        round((float(word.get("top", 0)) + float(word.get("height", height)) + height * 0.7) * scale_y),
    )
    if right <= left or bottom <= top:
        return None
    crop = image.crop((left, top, right, bottom))
    crop = crop.resize((max(1, crop.width * 4), max(1, crop.height * 4)), Image.Resampling.LANCZOS)
    variants = [
        crop,
        crop.point(lambda pixel: 255 if pixel > 170 else 0),
        crop.point(lambda pixel: 255 if pixel > 205 else 0),
    ]
    results: list[int] = []
    for variant in variants:
        for psm in (7, 8, 10):
            text = pytesseract.image_to_string(
                variant,
                lang="eng",
                config=f"--psm {psm} -c tessedit_char_whitelist=0123456789",
            )
            digits = re.sub(r"\D", "", text)
            if digits:
                results.append(int(digits))
    if not results:
        return None
    value, votes = Counter(results).most_common(1)[0]
    return value if votes >= 2 else None


def _month_label_density(
    label: dict[str, Any],
    labels: list[dict[str, Any]],
    page_width: float,
    page_height: float,
) -> int:
    center_x = (float(label["x0"]) + float(label["x1"])) / 2
    center_y = (float(label["top"]) + float(label["bottom"])) / 2
    nearby = {
        str(other["label"])
        for other in labels
        if (
            ((center_x - (float(other["x0"]) + float(other["x1"])) / 2) / max(page_width, 1.0)) ** 2
            + ((center_y - (float(other["top"]) + float(other["bottom"])) / 2) / max(page_height, 1.0)) ** 2
        ) ** 0.5 <= 0.35
    }
    return len(nearby)


def _choose_monthly_options(
    options: dict[str, list[tuple[int, float]]],
    annual_candidates: list[tuple[int, float]],
) -> tuple[dict[str, int], int | None]:
    """月番号をキーに並べ、年税額と一致する候補組合せを優先する。"""
    normalized: dict[str, list[tuple[int, float]]] = {}
    for month in MONTHS:
        best_by_value: dict[int, float] = {}
        for amount, score in options.get(month, []):
            best_by_value[amount] = max(score, best_by_value.get(amount, float("-inf")))
        normalized[month] = sorted(
            best_by_value.items(), key=lambda item: item[1], reverse=True
        )[:6]
    plausible_annuals = sorted(
        [(amount, score) for amount, score in annual_candidates if amount >= 100],
        key=lambda item: item[1],
        reverse=True,
    )
    if all(normalized[month] for month in MONTHS) and plausible_annuals:
        states: dict[int, tuple[float, dict[str, int]]] = {0: (0.0, {})}
        max_annual = max(amount for amount, _ in plausible_annuals)
        for month in MONTHS:
            next_states: dict[int, tuple[float, dict[str, int]]] = {}
            for total, (score, selected) in states.items():
                for amount, option_score in normalized[month]:
                    new_total = total + amount
                    if new_total > max_annual:
                        continue
                    new_score = score + option_score
                    if new_total not in next_states or new_score > next_states[new_total][0]:
                        next_states[new_total] = (new_score, {**selected, month: amount})
            states = next_states
        matches = [
            (states[annual][0] + annual_score, annual, states[annual][1])
            for annual, annual_score in plausible_annuals
            if annual in states
        ]
        if matches:
            _, annual, selected = max(matches, key=lambda item: item[0])
            return selected, annual
    selected = {
        month: normalized[month][0][0]
        for month in MONTHS
        if normalized[month]
    }
    total = sum(selected.values()) if len(selected) == len(MONTHS) else None
    annual = next((amount for amount, _ in plausible_annuals if amount == total), None)
    if annual is None and plausible_annuals:
        annual = plausible_annuals[0][0]
    return selected, annual


def _extract_adaptive_ocr_fields(
    page_words: list[dict[str, Any]],
    raw_text: str,
    fiscal_year: str,
    municipality: str,
    image: Any | None,
) -> dict[str, Any] | None:
    adapter = _OcrPageAdapter(page_words, int(page_words[0].get("page") or 1))
    page_text = normalize_name(f"{raw_text}\n{adapter.extract_text()}")
    matched_names = [name for name in EMPLOYEES if normalize_name(name) in page_text]
    if len(matched_names) != 1:
        return None
    month_labels = _positioned_label_candidates(adapter.extract_words(), _month_name, 3)
    annual_labels = _positioned_label_candidates(
        adapter.extract_words(), lambda value: value if _annual_label(value) else None, 9
    )
    excluded = month_labels + annual_labels
    directional_by_label: list[tuple[dict[str, Any], dict[str, list[tuple[float, int, dict[str, Any]]]]]] = []
    direction_months: dict[str, set[str]] = defaultdict(set)
    direction_distances: dict[str, list[float]] = defaultdict(list)
    for label in month_labels:
        directional = _directional_amount_candidates(
            adapter.extract_words(), label, adapter.width, adapter.height, excluded
        )
        directional_by_label.append((label, directional))
        for direction, candidates in directional.items():
            if candidates:
                direction_months[direction].add(str(label["label"]))
                direction_distances[direction].append(candidates[0][0])
    direction_order = {"right": 4, "left": 3, "down": 2, "up": 1}
    preferred_direction = max(
        direction_order,
        key=lambda direction: (
            len(direction_months.get(direction, set())),
            -sum(direction_distances.get(direction, [1.0])) / len(direction_distances.get(direction, [1.0])),
            direction_order[direction],
        ),
    )
    month_options: dict[str, list[tuple[int, float]]] = defaultdict(list)
    for label, directional in directional_by_label:
        choices = directional.get(preferred_direction) or next(
            (directional.get(direction) for direction in ("right", "left", "down", "up") if directional.get(direction)),
            [],
        )
        if not choices:
            continue
        distance, amount, word = choices[0]
        confidence = float(word.get("confidence") or 0)
        density = _month_label_density(label, month_labels, adapter.width, adapter.height)
        score = density * 20.0 + confidence - distance * 100.0
        month = str(label["label"])
        month_options[month].append((amount, score))
        if image is not None and confidence < 80:
            refined = _targeted_ocr_amount(image, word)
            if refined is not None:
                month_options[month].append((refined, score + 40.0))
    annual_options: list[tuple[int, float]] = []
    for label in annual_labels:
        directional = _directional_amount_candidates(
            adapter.extract_words(), label, adapter.width, adapter.height, excluded
        )
        candidate = next(
            (
                item
                for direction in ("right", "down", "left", "up")
                for item in directional.get(direction, [])
                if item[1] >= 100
            ),
            None,
        )
        if candidate is None:
            continue
        distance, amount, word = candidate
        confidence = float(word.get("confidence") or 0)
        score = confidence - distance * 100.0
        annual_options.append((amount, score))
        if image is not None and confidence < 80:
            refined = _targeted_ocr_amount(image, word)
            if refined is not None:
                annual_options.append((refined, score + 40.0))
    monthly, annual_amount = _choose_monthly_options(month_options, annual_options)
    employee_name = matched_names[0]
    return {
        "employee_code": EMPLOYEES[employee_name],
        "employee_name": employee_name,
        "recognized_name": employee_name,
        "fiscal_year": fiscal_year,
        "municipality": _clean_municipality(municipality),
        "designation_number": "",
        "annual_amount": annual_amount,
        "monthly_amounts": monthly,
        "notes": "",
        "extraction_method": "適応型座標付きローカルOCR",
    }


def _extract_individual_ocr_notices(
    words: list[dict[str, Any]],
    raw_text: str,
    confidence: float | None,
    data: bytes | None = None,
    extension: str = ".pdf",
    password: str | None = None,
) -> list[dict[str, Any]]:
    fiscal_year = _extract_fiscal_year(raw_text)
    municipality = _clean_municipality(_extract_municipality(raw_text))
    by_page: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for word in words:
        by_page[int(word.get("page") or 1)].append(word)
    images: list[Any] = []
    if data is not None:
        try:
            images = [_prepare_ocr_image(image) for image in _image_objects(data, extension, password)]
        except Exception as exc:
            _write_ocr_log("個人用通知書のセル再OCR用画像化に失敗しました", f"type={type(exc).__name__}")
    candidates: list[dict[str, Any]] = []
    for page_number, page_words in by_page.items():
        positioned = _extract_individual_pdf_page(
            _OcrPageAdapter(page_words, page_number),
            raw_text,
            fiscal_year,
            municipality,
        )
        if positioned:
            positioned["fields"]["extraction_method"] = "座標付きローカルOCR"
            candidates.append(positioned)
        adaptive = _extract_adaptive_ocr_fields(
            page_words,
            raw_text,
            fiscal_year,
            municipality,
            images[page_number - 1] if page_number <= len(images) else None,
        )
        if adaptive:
            candidates.append({"fields": adaptive, "region": (positioned or {}).get("region") or {}})
    text_fields, _ = extract_fields(raw_text)
    text_fields["municipality"] = _clean_municipality(text_fields.get("municipality")) or municipality
    text_fields["extraction_method"] = "ローカルOCR文字抽出"
    if text_fields.get("employee_name"):
        candidates.append({"fields": text_fields, "region": {}})
    merged = _merge_individual_fields(*(candidate["fields"] for candidate in candidates))
    if not merged:
        return []
    best = max(candidates, key=lambda candidate: _individual_fields_score(candidate["fields"]))
    return [
        {
            "fields": merged,
            "warnings": _field_warnings(merged),
            "confidence": confidence,
            "page_number": int((best.get("region") or {}).get("page") or 1),
            "region": best.get("region") or {},
        }
    ]


def _extract_ocr_person_notices(
    words: list[dict[str, Any]],
    raw_text: str,
    confidence: float | None,
    document_type: str | None = None,
    data: bytes | None = None,
    extension: str = ".pdf",
    password: str | None = None,
) -> list[dict[str, Any]]:
    if document_type == "individual_single":
        return _extract_individual_ocr_notices(
            words, raw_text, confidence, data, extension, password
        )
    lines = _group_ocr_lines(words)
    hits: list[tuple[str, list[dict[str, Any]]]] = []
    for line in lines:
        joined = normalize_name("".join(str(item["text"]) for item in line))
        for employee_name in EMPLOYEES:
            if normalize_name(employee_name) in joined:
                hits.append((employee_name, line))
    notices: list[dict[str, Any]] = []
    fiscal_year = _extract_fiscal_year(raw_text)
    municipality = _extract_municipality(raw_text)
    by_page: dict[int, list[tuple[str, list[dict[str, Any]]]]] = defaultdict(list)
    for hit in hits:
        by_page[int(hit[1][0]["page"])].append(hit)
    for page, page_hits in by_page.items():
        page_hits.sort(key=lambda hit: min(int(item["top"]) for item in hit[1]))
        for index, (employee_name, name_line) in enumerate(page_hits):
            name_top = min(int(item["top"]) for item in name_line)
            page_height = int(name_line[0]["page_height"])
            previous_top = min(int(item["top"]) for item in page_hits[index - 1][1]) if index else 0
            next_top = min(int(item["top"]) for item in page_hits[index + 1][1]) if index + 1 < len(page_hits) else page_height
            block_top = max(0, (previous_top + name_top) // 2 if index else name_top - 180)
            block_bottom = min(page_height, (name_top + next_top) // 2 if index + 1 < len(page_hits) else name_top + 180)
            block_words = [
                word for word in words
                if int(word["page"]) == page and block_top <= int(word["top"]) <= block_bottom
            ]
            block_text = "\n".join(
                " ".join(str(item["text"]) for item in line)
                for line in _group_ocr_lines(block_words)
            )
            fields, _ = extract_fields(block_text)
            fields.update(
                {
                    "employee_code": EMPLOYEES[employee_name],
                    "employee_name": employee_name,
                    "recognized_name": employee_name,
                    "fiscal_year": fields.get("fiscal_year") or fiscal_year,
                    "municipality": fields.get("municipality") or municipality,
                    "extraction_method": "座標付きローカルOCR",
                }
            )
            region = {
                "page": page,
                "x0": 0,
                "top": block_top,
                "x1": int(name_line[0]["page_width"]),
                "bottom": block_bottom,
                "page_width": int(name_line[0]["page_width"]),
                "page_height": page_height,
                "pixel_coordinates": True,
            }
            notices.append(
                {
                    "fields": fields,
                    "warnings": _field_warnings(fields),
                    "confidence": confidence,
                    "page_number": page,
                    "region": region,
                }
            )
    return notices


def extract_notice(
    data: bytes,
    filename: str,
    password: str | None = None,
) -> dict[str, Any]:
    extension = Path(filename).suffix.lower()
    if extension not in SUPPORTED_EXTENSIONS:
        raise ValueError("PDF、JPG、JPEG、PNGだけ取り込めます。")
    warnings: list[str] = []
    confidence: float | None = None
    text = ""
    extraction_method = "手動入力"
    page_count = 1
    notices: list[dict[str, Any]] = []
    pdf_status: dict[str, Any] = {
        "encrypted": False,
        "accessible": True,
        "accessible_for_text": True,
        "ocr_fallback_allowed": True,
        "auto_unlocked": False,
        "password_required": False,
        "text_extraction": "not_applicable",
        "ocr_fallback": "not_used",
    }
    direct_document_type = "unknown"
    if extension == ".pdf":
        text, direct_warnings, pdf_status = _extract_pdf_text(data, password)
        warnings.extend(direct_warnings)
        if text.strip():
            extraction_method = "PDF内テキスト直接抽出"
            direct_names = [name for name in EMPLOYEES if normalize_name(name) in normalize_name(text)]
            direct_document_type = detect_document_type(text, direct_names)
            notices, page_count, table_warnings = _extract_pdf_table_notices(
                data, text, password
            )
            warnings.extend(table_warnings)
    needs_individual_ocr = (
        extension == ".pdf"
        and direct_document_type == "individual_single"
        and (
            not notices
            or any(candidate.get("extraction_conflict") for candidate in notices)
            or not any(_individual_fields_complete(candidate.get("fields") or {}) for candidate in notices)
        )
    )
    ocr_used = not text.strip() or needs_individual_ocr
    ocr_text = ""
    if ocr_used:
        ocr_text, ocr_warnings, confidence = _ocr_text(data, extension, password)
        warnings.extend(ocr_warnings)
        if ocr_text.strip():
            if text.strip():
                extraction_method = "PDF内テキスト直接抽出＋ローカルOCR"
                text = f"{text}\n\n{ocr_text}"
            else:
                extraction_method = "ローカルOCR"
                text = ocr_text
            if extension == ".pdf":
                pdf_status["ocr_fallback"] = "success"
        elif extension == ".pdf":
            pdf_status["ocr_fallback"] = "failed"
    matched_names = [name for name in EMPLOYEES if normalize_name(name) in normalize_name(text)]
    document_type = detect_document_type(text, matched_names)
    if document_type == "unknown" and direct_document_type != "unknown":
        document_type = direct_document_type
    ocr_words: list[dict[str, Any]] = []
    ocr_notices: list[dict[str, Any]] = []
    if ocr_used and ocr_text.strip():
        ocr_words, positioned_warnings, positioned_confidence = _ocr_positioned_words(
            data, extension, password
        )
        warnings.extend(positioned_warnings)
        confidence = positioned_confidence if positioned_confidence is not None else confidence
        ocr_notices = _extract_ocr_person_notices(
            ocr_words,
            text,
            confidence,
            document_type=document_type,
            data=data,
            extension=extension,
            password=password,
        )
    if document_type == "individual_single" and (notices or ocr_notices):
        all_candidates = notices + ocr_notices
        merged_fields = _merge_individual_fields(
            *(candidate.get("fields") or {} for candidate in all_candidates)
        )
        if merged_fields:
            best_candidate = max(
                all_candidates,
                key=lambda candidate: _individual_fields_score(candidate.get("fields") or {}),
            )
            notices = [
                {
                    "fields": merged_fields,
                    "warnings": _field_warnings(merged_fields),
                    "confidence": confidence,
                    "page_number": best_candidate.get("page_number") or 1,
                    "region": best_candidate.get("region") or {},
                }
            ]
    elif not notices and ocr_notices:
        notices = ocr_notices
    if not notices:
        fields, field_warnings = extract_fields(text)
        if document_type == "individual_single":
            fields["municipality"] = _clean_municipality(fields.get("municipality"))
        warnings.extend(field_warnings)
        fields["extraction_method"] = extraction_method
        notices = [{"fields": fields, "warnings": _field_warnings(fields), "confidence": confidence, "page_number": 1, "region": {}}]
    if document_type == "company_multi":
        detected = {candidate["fields"].get("employee_name") for candidate in notices}
        missing = [name for name in EMPLOYEES if name not in detected]
        if missing:
            warning = "会社用帳票ですが、対象従業員を全員検出できませんでした。"
            warnings.append(warning)
            for candidate in notices:
                candidate.setdefault("warnings", []).append(warning)
    if document_type == "unknown":
        warning = "帳票種類を自動判定できませんでした。種類を選択して元資料を確認してください。"
        warnings.append(warning)
        for candidate in notices:
            candidate.setdefault("warnings", []).append(warning)
    for candidate in notices:
        candidate["warnings"] = list(dict.fromkeys(candidate.get("warnings", []) + warnings))
        candidate["fields"]["document_type"] = document_type
    primary = notices[0]["fields"]
    primary["extraction_method"] = primary.get("extraction_method") or extraction_method
    return {
        "fields": primary,
        "notices": notices,
        "document_type": document_type,
        "document_type_label": document_type_label(document_type),
        "raw_text": text,
        "warnings": list(dict.fromkeys(warnings)),
        "confidence": confidence,
        "page_count": page_count,
        "ocr_result": {
            "positioned_word_count": len(ocr_words),
            "pdf_security": pdf_status,
        },
    }


def validate_notice_import(result: dict[str, Any]) -> list[str]:
    """重大な誤読をDB保存前に止める。警告修正可能な軽微差異とは分離する。"""
    failures: list[str] = []
    pdf_status = (result.get("ocr_result") or {}).get("pdf_security") or {}
    if pdf_status.get("password_required"):
        failures.append("PDFを復号できませんでした。")
    text_accessible = pdf_status.get(
        "accessible_for_text", pdf_status.get("accessible", True)
    )
    if (
        not text_accessible
        and pdf_status.get("ocr_fallback_allowed", True)
        and pdf_status.get("ocr_fallback") != "success"
    ):
        failures.append("PDF内テキスト抽出と画像OCRの両方に失敗しました。")
    candidates = result.get("notices") or []
    if not candidates:
        failures.append("通知書の確認データを作成できませんでした。")
    for candidate in candidates:
        fields = candidate.get("fields") or {}
        annual_raw = fields.get("annual_amount")
        monthly = fields.get("monthly_amounts") or {}
        values = [monthly.get(month) for month in MONTHS]
        if annual_raw is None:
            failures.append("年税額を特定できませんでした。")
        if any(value is None for value in values):
            failures.append("6月から翌年5月までの月別税額表を完全に特定できませんでした。")
            continue
        amounts = [int(value) for value in values]
        annual = int(annual_raw or 0)
        if annual > 0 and all(amount == 0 for amount in amounts):
            failures.append("月別税額がすべて0円となる重大な読取失敗を検出しました。")
        if 0 < annual < 100:
            failures.append("年税額欄以外の数字を誤認識した可能性が高いため保存しません。")
        if fields.get("extraction_method") == "手動入力":
            failures.append("テキスト抽出と画像OCRの両方で月別税額表を読み取れませんでした。")
    return list(dict.fromkeys(failures))


def preview_images(
    data: bytes,
    filename: str,
    password: str | None = None,
) -> tuple[list[bytes], list[str]]:
    extension = Path(filename).suffix.lower()
    if extension in {".jpg", ".jpeg", ".png"}:
        return [data], []
    if extension != ".pdf":
        return [], ["プレビュー非対応の形式です。"]
    try:
        import pypdfium2 as pdfium
    except ModuleNotFoundError:
        return [], ["pypdfium2が利用できないためPDFページを画像表示できません。元PDFは保存されています。"]
    try:
        document = pdfium.PdfDocument(data, password=password or None)
        try:
            images: list[bytes] = []
            for page in document:
                buffer = io.BytesIO()
                page.render(scale=1.75).to_pil().convert("RGB").save(buffer, format="PNG")
                images.append(buffer.getvalue())
            return images, []
        finally:
            document.close()
    except Exception as exc:
        _write_ocr_log("PDFプレビュー作成に失敗しました", f"type={type(exc).__name__}")
        return [], ["PDFプレビューを作成できませんでした。パスワード又はPDF形式を確認してください。"]


def preview_region_image(
    data: bytes,
    filename: str,
    region: dict[str, Any],
    password: str | None = None,
) -> tuple[bytes | None, list[str]]:
    """従業員別の氏名・年税額・12か月表を原本から拡大表示する。"""
    if not region:
        return None, ["該当領域を自動特定できませんでした。原本全体を確認してください。"]
    extension = Path(filename).suffix.lower()
    try:
        images = _image_objects(data, extension, password)
        page_index = max(0, int(region.get("page", 1)) - 1)
        if page_index >= len(images):
            return None, ["該当ページを表示できませんでした。"]
        image = images[page_index]
        source_width = float(region.get("page_width") or image.width)
        source_height = float(region.get("page_height") or image.height)
        scale_x = image.width / max(source_width, 1.0)
        scale_y = image.height / max(source_height, 1.0)
        left = max(0, round(float(region.get("x0", 0)) * scale_x))
        top = max(0, round(float(region.get("top", 0)) * scale_y))
        right = min(image.width, round(float(region.get("x1", source_width)) * scale_x))
        bottom = min(image.height, round(float(region.get("bottom", source_height)) * scale_y))
        if right <= left or bottom <= top:
            return None, ["該当領域の座標が不正なため、原本全体を確認してください。"]
        cropped = image.crop((left, top, right, bottom)).convert("RGB")
        buffer = io.BytesIO()
        cropped.save(buffer, format="PNG")
        return buffer.getvalue(), []
    except Exception as exc:
        _write_ocr_log("該当領域プレビュー作成に失敗しました", repr(exc))
        return None, ["該当領域の拡大画像を作成できませんでした。原本全体を確認してください。"]


def notice_debug_json(result: dict[str, Any]) -> str:
    return json.dumps(result, ensure_ascii=False, indent=2)
