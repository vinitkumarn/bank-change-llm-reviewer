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
- PR 2: DuckDB load with real types, Python rules for every signal, regex-only extraction,
  two baselines (naive, rules-only) and the eval against the planted labels.
- PR 3: bank-number tokens, the single Claude extraction call (`--by claude`), guards,
  never-downgrade, response cache, audit log, and a held-out reworded set (P01-P10).

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
| `data/reworded_plan.json` | labels for the 10 held-out reworded cases P01-P10 (never used to tune) |

The same seed always produces byte-identical files. All numbers are random and synthetic.
`build` also loads everything into `data/bankreview.duckdb` (money as DECIMAL(18,2), dates as
DATE).

## Review one case, run the eval

```powershell
python -m bankreview run --case F01 --by rules
```

```powershell
python -m bankreview eval --by naive
```

```powershell
python -m bankreview eval --by rules
```

```powershell
python -m bankreview eval --by rules --set redteam
```

```powershell
python -m bankreview eval --by rules --set reworded
```

`--by` is `naive` (domain check only), `rules` (regex extraction + every rule) or `claude`
(one Claude extraction call per request; Python rules still decide). `--set` is `main`,
`redteam` or `reworded`. `eval` also takes `--cases F01 F02` and `--limit N`. Reports go to
`reports/eval_<by>.md`, `reports/eval_<by>_redteam.md` and `reports/eval_<by>_reworded.md`.
Expected answers come only from the plan files.

## The Claude reviewer (needs your own API key in `.env`)

```powershell
python -m bankreview run --case I01 --by claude
```

```powershell
python -m bankreview eval --by claude --set reworded
```

- Bank numbers are replaced with `BANK_TOKEN_n (...last4)` before anything is sent.
- The model returns facts and exact quotes only; Python checks every quote and token, then the
  rules decide. The result is never lower than the rules-only result.
- Answers are cached in `data/cache/` (key: model + hash of the system prompt + packet).
  Add `--no-cache` to force fresh calls.
- `logs/audit.jsonl` holds ids, hashes, tokens, cost and status only. Full error messages go
  to `logs/bankreview.log`.
- The model comes from `BANKREVIEW_MODEL` in `.env` (default `claude-sonnet-5-5`).

## Run the tests

```powershell
python -m pytest
```
