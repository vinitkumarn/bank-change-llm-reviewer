# bank-change-llm-reviewer

Supplier Bank-Change Risk Reviewer — a portfolio project.

A supplier asks to change bank details by email, letter or portal note. This tool reads the
request, compares it with the supplier master (synthetic CSVs modeled on Oracle Fusion
POZ / IBY / AP_PAYMENT_SCHEDULES tables), works out the dollars at risk, scores the risk with
Python rules and recommends verification steps.

**It suggests; a human decides.** It never changes bank details, never approves a change and
never calls any phone number. Synthetic data only, USD only, local files only.

The design is in [docs/design.md](docs/design.md).

## Status
- PR 1: project skeleton + planted data set (45 main cases, 10 red-team cases) and labels.

## Setup on Windows (PowerShell)

Needs Python 3.11 or newer (developed with the `py` launcher and Python 3.14).

```powershell
py -3.14 -m venv .venv
```

```powershell
Set-ExecutionPolicy -Scope Process Bypass
```

```powershell
.venv\Scripts\Activate.ps1
```

```powershell
python -m pip install -r requirements.txt
```

Copy `.env.example` to `.env` and fill in your own key (never commit `.env`):

```powershell
Copy-Item .env.example .env
```

## Build the data set

```powershell
python -m bankreview build
```

This writes:

| File | What it is |
|---|---|
| `data/csv/suppliers.csv` | POZ_SUPPLIERS-style supplier header |
| `data/csv/supplier_sites.csv` | POZ_SUPPLIER_SITES_ALL_M-style sites, with email domain on file |
| `data/csv/supplier_contacts.csv` | POZ_SUPPLIER_CONTACTS-style contacts, with phone on file |
| `data/csv/bank_accounts.csv` | IBY_EXT_BANK_ACCOUNTS + IBY_PMT_INSTR_USES_ALL-style accounts (token, last 4, hash — never the full number) |
| `data/csv/payment_schedules.csv` | AP_PAYMENT_SCHEDULES_ALL-style open amounts (exact decimals) |
| `data/csv/change_requests.csv` | the incoming requests (UNTRUSTED text with raw bank numbers) |
| `data/plan.json` | labels for the 45 main cases |
| `data/redteam_plan.json` | labels for the 10 red-team cases (reported separately) |

The same seed always produces byte-identical files. All numbers are random and synthetic.

## Run the tests

```powershell
python -m pytest
```
