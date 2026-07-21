# Demo Video Script (under 3 minutes)

## 0:00-0:20 - The problem

Japanese small businesses receive resident-tax notices with annual and monthly deductions. Re-entering these sensitive values into payroll is slow and error-prone.

## 0:20-0:45 - Upload

This is a fully fictional company-level notice for two employees. I upload the text PDF. The same demo also includes a scan-style PNG processed by bundled local Tesseract OCR.

## 0:45-1:15 - Split and extract

The app detects a company multi-employee document, splits it into two records, and shows the annual amount plus all 12 monthly deductions. The expected totals are 24,000 yen and 85,000 yen.

## 1:15-1:40 - Human review

Automation does not bypass control. I compare every value with the source and confirm each record. Until confirmation, no amount can flow into payroll.

## 1:40-2:05 - Payroll link

For the June 2026 payment month, the confirmed values appear automatically: 2,000 yen for the executive and 8,000 yen for the employee. Other deductions still use the existing generic payroll logic.

## 2:05-2:30 - Issue documents

After saving payroll history, I issue files from the saved values without recalculation. The executive receives an executive compensation statement, the employee receives a payslip, and both can produce accounting CSV records with leading-zero employee codes preserved.

## 2:30-2:50 - Privacy

Everything runs on this PC, bound to localhost. PDF extraction, OCR, SQLite storage, payroll calculation, and output generation stay local. No payroll or tax data is sent to a cloud service.

## 2:50-2:58 - Codex

Codex helped anonymize the existing workflow, generalize client-specific logic, build fictional inputs, and automate privacy, database, OCR, payroll, PDF, CSV, launch, and reset checks.
