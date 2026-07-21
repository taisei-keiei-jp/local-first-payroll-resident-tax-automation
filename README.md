# Local-First Payroll & Resident Tax Automation

OpenAI Build Week demo for local-first Japanese payroll and resident tax automation.

The application reads a Japanese resident tax notice, separates one source document into employee-level records, verifies monthly tax amounts, links confirmed amounts to payroll, and issues payslip PDFs and management CSV files.

## Demo Video

https://youtu.be/BKkeWcGP8dw

## Project Overview

Japanese payroll operations often require staff to manually transfer monthly resident tax amounts from municipal notices into payroll systems.

This project demonstrates a local-first workflow that:

1. Imports a multi-employee resident tax notice.
2. Extracts PDF text locally.
3. Uses Japanese OCR as a fallback when necessary.
4. Separates one source document into employee records.
5. Lets the operator review annual and monthly tax amounts.
6. Confirms the active notice version.
7. Links the confirmed monthly amount to payroll.
8. Calculates payroll deductions and net pay.
9. Issues a payslip PDF and management CSV.
10. Keeps payroll and tax data on the local PC.

## Key Features

- Local PDF text extraction
- Japanese OCR fallback
- Multi-employee notice separation
- Employee-level resident tax review
- Monthly tax verification from June through the following May
- Active notice and revision management
- Resident tax linkage to payroll calculations
- Executive and employee payroll support
- Payslip PDF generation
- Management CSV generation
- Issuance history and traceability
- Preservation of leading zeros in employee codes
- Local SQLite data storage
- No cloud transmission of payroll or tax data

## Demo Data

All names, companies, documents, municipalities, and amounts in this repository and demonstration are fictional.

### Fictional company

- Company code: `DEMO001`
- Company name: `株式会社サンプルワークス`
- Municipality: `デモ市`

### Fictional employees

#### Yamada Taro

- Employee code: `000101`
- Classification: Executive
- Executive compensation: JPY 320,000
- Dependents: 1
- Annual resident tax: JPY 24,000
- Monthly resident tax: JPY 2,000

#### Sato Hanako

- Employee code: `000102`
- Classification: Employee
- Basic salary: JPY 210,000
- Dependents: 0
- Annual resident tax: JPY 85,000
- June resident tax: JPY 8,000
- July through the following May: JPY 7,000 per month

## Technology

- Python
- Streamlit
- SQLite
- Tesseract OCR
- ReportLab
- Codex
- GPT-5.6

## Local-First Design

The application is designed to run locally on a Windows PC.

Payroll records, resident tax notices, generated PDFs, and CSV data are processed locally. The application does not send payroll or resident tax data to a cloud service.

The submitted demo uses fictional data only.

## Running the Demo

### Requirements

- Windows
- Python
- Required Python packages for Streamlit, PDF processing, OCR, and ReportLab
- Tesseract OCR with Japanese language support

### Start

1. Download or clone this repository.
2. Install the required Python dependencies.
3. Confirm that Tesseract OCR and Japanese OCR data are available.
4. Double-click:

   `起動する.vbs`

5. The application opens at:

   `http://127.0.0.1:8520/`

### Stop

Use the `アプリを終了する` button in the application menu to stop the local Streamlit process cleanly.

## Demo Workflow

1. Open `住民税通知書取込・確認`.
2. Upload the fictional multi-employee resident tax notice.
3. Run local import and automatic extraction.
4. Review the two employee records.
5. Confirm the monthly resident tax amounts.
6. Open `給与計算入力`.
7. Calculate June 2026 payroll for each employee.
8. Save each result to payroll history.
9. Open `給与明細・CSV発行`.
10. Generate the payslip PDF and management CSV.

## Expected Demo Results

### Yamada Taro

- Gross pay: JPY 320,000
- Total deductions: JPY 55,158
- Net pay: JPY 264,842
- Resident tax: JPY 2,000

### Sato Hanako

- Gross pay: JPY 210,000
- Total deductions: JPY 42,552
- Net pay: JPY 167,448
- Resident tax: JPY 8,000

## How Codex Was Used

Codex with GPT-5.6 was used as the primary coding collaborator during the Build Week work.

Codex helped with:

- Reviewing the existing application structure
- Planning minimal-risk changes
- Implementing the fictional Build Week demo environment
- Implementing and reviewing resident tax notice processing
- Handling employee-level notice separation
- Connecting confirmed resident tax amounts to payroll
- Reviewing SQLite data handling
- Fixing the management CSV import key to use the configured company code
- Reviewing the Windows VBS launcher
- Testing application startup and shutdown
- Testing HTTP availability on port 8520
- Checking Streamlit and browser errors
- Checking database integrity
- Verifying that unrelated application folders were not modified
- Running the final regression test suite

The final Codex test run completed:

- Passed: 20
- Failed: 0

## How GPT-5.6 Was Used

GPT-5.6 was used for requirements analysis, implementation review, edge-case checking, and final validation.

It helped with:

- Organizing the Build Week demo requirements
- Preserving leading zeros in employee codes
- Reviewing resident tax month and annual-total consistency
- Reviewing payroll and notice traceability
- Checking fictional-data and privacy requirements
- Reviewing PDF and CSV output requirements
- Reviewing the demo workflow
- Reviewing the final demonstration video
- Checking submission materials for consistency

## Prior Work and Build Week Contribution

This submission is based on an existing local payroll application foundation.

The Build Week submission does not claim that the entire payroll system was created from scratch during the event.

The work prepared and validated for Build Week includes:

- A separate Build Week demo application folder
- Fictional company and employee data
- A fictional multi-employee resident tax notice
- Local notice import and extraction demonstration
- Separation of one source document into two employee records
- Employee-level monthly tax verification
- Confirmed-notice linkage to June payroll
- Build Week-specific branding and workflow guidance
- A dedicated local port and launcher
- Management CSV company-code correction
- Regression testing and database integrity verification
- Submission video and public documentation

No production customer data is included.

## Privacy and Security

- All demonstration data is fictional.
- No real customer data is included.
- Employee codes are treated as strings.
- Leading zeros are preserved.
- Payroll and tax data remain local.
- Generated output and local database files should not be committed when they contain runtime data.
- Secrets, credentials, passwords, and API keys must not be stored in this repository.

## Limitations

- The demo is designed for a controlled Windows environment.
- Japanese resident tax notice formats vary by municipality.
- OCR results depend on scan quality and the installed OCR environment.
- The application is a demonstration and is not a substitute for professional payroll, tax, or legal review.

## Repository Use

This repository is published for OpenAI Build Week evaluation and demonstration.

No software license is granted unless a license is added separately.
