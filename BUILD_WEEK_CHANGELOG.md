# Build Week Changelog

## Existing application capabilities retained

- Streamlit payroll calculation and history workflow
- SQLite resident-tax source, notice, revision, confirmation, and issuance audit model
- PDF text extraction and bundled local Tesseract OCR paths
- Generic social-insurance and withholding-tax calculation
- Payslip, payroll-report, accounting CSV, and ZIP generation

## New anonymous demo edition

- Created a clean Build Week directory from selected executable modules only.
- Added one fictional company and two fictional employees with leading-zero string codes.
- Created a fresh schema-version-3 SQLite database with no imported notice, payroll history, or issuance history.

## Customer-information removal

- Replaced customer, person, company, code, path, logo, URL, and payroll assumptions with master-driven lookups.
- Excluded databases, notices, histories, generated outputs, backups, tests, logs, caches, virtual environments, and repository metadata from the source selection.

## Fictional notice generation

- Added a two-page text PDF and a scan-style PNG containing the same two fictional records.
- Added a generator that uses content, not filenames or pre-seeded answers.

## Demo reset

- Added a guarded Python reset command and confirmation-based batch launcher.
- Reset scope is derived from the script location and cannot target another application database.

## Relative-path launch and port isolation

- Added Python discovery using `py -3.14` with a `python` fallback.
- Kept the project-local vendor bootstrap and bundled Tesseract discovery.
- Bound the app to `127.0.0.1:8520` without hosts-file or machine-level changes.

## Bilingual guidance

- Added the Build Week title, English helper headings, privacy statement, and five-step demo workflow while retaining the Japanese UI.

## Demo tests

- Added deterministic checks for syntax, imports, schema integrity, idempotence, fictional masters, PDF extraction, one local OCR pass, confirmation gating, June linkage, saved-history PDF/CSV output, honorific handling, reset safety, port configuration, and prohibited-string absence.

## Submission material

- Added an English README, Japanese operator guide, sub-three-minute English video script, this changelog, and privacy-oriented ignore rules.
