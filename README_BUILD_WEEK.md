# Local-First Payroll & Resident Tax Automation

## Project overview

A Windows-local demonstration for Japanese small businesses that connects resident-tax notice ingestion, human review, payroll calculation, payslip generation, and accounting CSV export. Every company, person, document, identifier, and amount in this repository is fictional.

## Problem

Japanese payroll teams receive municipality notices containing annual and monthly resident-tax amounts. Manual transcription into payroll is repetitive, error-prone, and sensitive because the source contains personal and tax information.

## Solution

The app reads a company-level multi-employee notice from PDF text or a local Tesseract OCR path, splits it into employee records, requires a human to review all 12 monthly values, and only then links the confirmed amount to payroll.

## Demo workflow

1. Upload `demo_inputs/resident_tax_notice_demo_multi_employee_R8.pdf` or the PNG scan.
2. Review the two extracted employee records and all monthly values.
3. Confirm each record after visual comparison.
4. Run the June 2026 payment-month payroll for both fictional employees.
5. Save the results, then issue the executive compensation statement, employee payslip, and accounting CSV.

## Architecture

- Streamlit local UI
- SQLite local audit and payroll-history store
- PDF text extraction with pypdf/pdfplumber
- Local OCR with the bundled Tesseract runtime and Japanese language data
- ReportLab PDF output and standard-library CSV output
- JSON company, employee, rate, and resident-tax masters

## Local-first privacy

The app binds only to `127.0.0.1`. It does not call an external payroll, OCR, or AI API. Uploaded documents, extracted text, confirmations, payroll records, and generated files remain in the application folder on the PC.

## Built with

Python 3.14, Streamlit, SQLite, ReportLab, pdfplumber, pypdf, Pillow, and Tesseract OCR. Runtime dependencies are loaded from the project-relative `vendor` and `tools/tesseract` directories.

## How to run

Double-click `起動する.vbs` or `起動する.bat`. The launcher first tries `py -3.14`, falls back to `python`, starts Streamlit on port 8520, and opens `http://127.0.0.1:8520/`.

## How to reset demo data

Double-click `デモデータ初期化.bat` and confirm the prompt. It clears only this demo's imported notices, payroll history, issuance history, and generated output. It preserves `demo_inputs`.

## Sample input files

- `resident_tax_notice_demo_multi_employee_R8.pdf`: two-page text PDF
- `resident_tax_notice_demo_scan_R8.png`: same fictional values rendered as a scan-style image

Both display `DEMO / FICTIONAL DATA / 架空データ` prominently and contain no real municipality marks or logos.

## Expected resident tax results

- Executive (`000101`): annual JPY 24,000; JPY 2,000 each month from June through the following May.
- Employee (`000102`): annual JPY 85,000; JPY 8,000 in June and JPY 7,000 for each remaining month.

## Codex contribution

Codex selected the minimum runtime files, removed client-specific assumptions, generalized employee and company lookups, created fictional masters and notices, implemented reset and relative-path launchers, added bilingual demo guidance, and built deterministic privacy and workflow tests.

## Human decisions and domain expertise

Human review remains mandatory before a notice becomes active. Payroll tax interpretation, rate-master updates, exception reasons, and production approval remain human responsibilities.

## Known limitations

- This is a two-employee demonstration, not a production onboarding package.
- OCR accuracy varies with scan quality; the confirmation screen is authoritative.
- The bundled rate master covers the included demo period and configuration only.
- No digital filing or municipality submission is included.

## No real client data statement

This demo was created from selected application logic only. No production database, source notice, payroll history, output file, customer logo, or real-person record is included.
