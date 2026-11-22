# bank-change-llm-reviewer

Supplier Bank-Change Risk Reviewer. A portfolio project by an Oracle ERP techno-functional
consultant (AP / AR / GL / P2P, Oracle Fusion supplier master).

## What it does

A supplier asks Accounts Payable to change its bank details by email, letter or portal note.
Fake bank-change requests are one of the most common ways AP is defrauded: the next payment
goes to a criminal's account.

For each request the tool:

1. reads the request text;
2. compares it with the supplier master (supplier, site, contacts, bank accounts);
3. works out the dollars at risk (open invoices due in the next 14 days);
4. scores the risk with Python rules;
5. recommends verification steps and who owns the decision.

**It suggests; a human decides.** It never changes bank details, never approves a change and
never calls any phone number. Synthetic data only, USD only, local DuckDB only.

The design is in [docs/design.md](docs/design.md).

## The AI idea

The hard part of this problem is reading messy text: forwarded email chains, OCR'd letters,
account numbers written with spaces or split across lines, polite wording that hides pressure.
Regex rules handle the wording they were written for and miss the rest. So the model is used
for one narrow job: **extraction**.

- **One Claude call per request.** The call turns the free text into validated fields plus
  exact quotes (new account, bank country, account holder, phone numbers in the text, urgency
  quotes, "skip the call-back" quotes, whether the text contains instructions to an AI).
- **Bank numbers never reach the model.** Before any text is sent, Python replaces account
  numbers, routing numbers, sort codes, IFSC codes and IBANs (including ones written with
  spaces, dashes or over two lines) with tokens like `BANK_TOKEN_1 (...4821)`. The map from
  token to full number stays in Python. A test sends every case through a fake client and
  fails if any full bank number appears in any prompt.
- **The model returns facts only; Python decides.** The answer schema (pydantic,
  `extra="forbid"`) has no field for risk level, signals, owner, hold or dollar amounts. Python
  rules set all of those.
- **Why one call and not an agent.** The task is fixed: read one request, return the same
  fields every time. There is nothing for an agent to plan or choose, and every tool or loop
  would be one more place for an injected instruction in the request to act. One call is also
  cheaper, easier to cache and easy to test with a scripted fake client.

## Risk signals

| Code | Signal | Level |
|---|---|---|
| DOMAIN_MISMATCH | Sender (or reply-to) domain differs from the domain on file, or is free mail (gmail etc.) | HIGH |
| LOOKALIKE_DOMAIN | Domain within edit distance 2 of the domain on file, or homoglyph / subdomain tricks (`acme.com.pay-portal.net`) | HIGH |
| COUNTRY_CHANGE | New bank country differs from the supplier's country | HIGH |
| SHARED_ACCOUNT | New account already assigned to a different supplier | HIGH |
| HOLDER_MISMATCH | Account holder name differs from the supplier name | HIGH |
| SKIP_VERIFICATION | Asks to skip the call-back, claims "already approved", or gives a new number to call | HIGH |
| URGENCY | Pressure wording ("before Friday's payment run", "today") | MEDIUM |
| RECENT_MASTER_CHANGE | Contact or site changed in the last 30 days | MEDIUM |
| NEW_CONTACT | Sender is not a contact on file | MEDIUM |

**Risk rule:** HIGH if any HIGH signal, or two or more MEDIUM signals (weak signals combined).
MEDIUM if exactly one MEDIUM signal. Otherwise LOW.

## Decisions and owners

| Risk | What happens | Owner |
|---|---|---|
| HIGH | Call-back to the number on file. Hold this supplier's payments **only if** the new account is already active. AP places the hold; Treasury releases it after a successful call-back. | Supplier Master + Treasury |
| MEDIUM | Call-back before the change is entered. | Supplier Master |
| LOW | Standard call-back. | Supplier Master |

Rules that apply to every request:

- The call-back always goes to the phone number **on file**, never to a number in the request.
- The new account is never activated before the call-back confirms the change.
- Payments are held only when the risk is HIGH **and** the new account is already active
  (`already_applied = Y`). If the account is not active yet, nothing can be paid to it, so a
  hold would only delay the supplier's legitimate payments.
- **Dollars at risk** = this supplier's `amount_remaining` due on or before the reference date
  (2026-11-02) + 14 days, **including overdue unpaid items**. The 14 days is `EXPOSURE_DAYS` in
  `src/bankreview/config.py`; Treasury owns that number. Money is `Decimal`, never float.
- **Grey areas** (bank merger, relocation to another country, factoring / assignment to a
  finance company, new subsidiary, request from an internal clerk): Supplier Master + Treasury
  decide. Default: do not activate until the call-back confirms. Same hold rule as above.

## Guards on the model's answer

Any guard failure sends the case to **NEEDS_HUMAN** with LOW confidence. The rules-only result
is kept, so a failure never lowers the risk.

- **Exact quotes.** Every quote, holder name and phone number must be an exact substring of
  the tokenized request text.
- **Known tokens.** Every `BANK_TOKEN_n` the model references must exist in this request.
- **Trusted numbers in the explanation.** Every number in the model's explanation must come
  from a trusted field (supplier master, token last 4 digits, request metadata), never from the
  untrusted body.
- **Schema.** The answer must match the schema. One retry carries the full validation error;
  if it is still invalid, NEEDS_HUMAN.
- **Never-downgrade.** Final risk = the higher of the rules-only result (regex extraction) and
  the result using the model's extraction. The model can add signals; it cannot remove one the
  rules found.
- **Untrusted input.** The request body is wrapped as `<UNTRUSTED>...</UNTRUSTED>` with `<` and
  `>` escaped, so a fake closing tag in the text cannot break out.

**Caching.** Answers are cached in `data/cache/`. The key is
sha256(model + prompt version + tokenized packet), so a change to any of them makes a fresh
call. API errors, invalid answers and answers that fail a guard are never cached. Guards run
again on every cached answer. `--no-cache` forces fresh calls. The system prompt is sent with
`cache_control`.

**Logs.** `logs/audit.jsonl` holds ids, hashes, tokens (last 4 only), cost and status. Full
error messages go to `logs/bankreview.log`.

## Three eval sets

Labels come only from the planted plan files, written by hand when the data was generated,
never from the rules code. **None of the three sets is used to tune the prompt or the rules.**

| Set | File | Cases | Why |
|---|---|---|---|
| main | `data/plan.json` | 45: 15 legit, 12 clear fraud, 8 grey, 5 injection, 5 messy format | The planted patterns from the design. |
| red-team | `data/redteam_plan.json` | 10 attacks (homoglyph and subdomain domains, compromised mailbox, split requests, CFO impersonation, fake factoring, injection in the signature, account number split over lines) | Measures fraud recall on attacks built to evade checks. Reported separately. |
| reworded (held out) | `data/reworded_plan.json` | 10 (P01-P10): 7 fraud, 3 legit, same patterns as the main set in wording no regex keyword matches | Shows whether the rules generalise to wording they have never seen. |

## Results

Measured in this repo with `python -m bankreview eval`. Naive = domain check only.
Rules = regex extraction + every rule, no LLM. Claude = one extraction call per request,
Python rules decide. Naive and rules were measured in this repo. The Claude column is from a
real local run (`claude-sonnet-5-5`, prompt version `0e01d0842bb8057b`).

False alarms count legit cases flagged HIGH: the 15 L cases in the main set and the 3 legit
P cases in the reworded set. Grey cases are not counted as false alarms. Cost is $0 for naive
and rules (no API call).

### Main set (45 cases)

| Metric | Naive | Rules | Claude |
|---|---|---|---|
| Fraud recall (fraud flagged HIGH) | 52.9% (9/17) | 100.0% (17/17) | 100.0% (17/17) |
| False alarms (L cases flagged HIGH) | 0.0% (0/15) | 0.0% (0/15) | 0.0% (0/15) |
| Risk-level accuracy | 73.3% (33/45) | 97.8% (44/45) | 100.0% (45/45) |
| Signal recall | 28.6% (10/35) | 97.1% (34/35) | 100.0% (35/35) |
| Dollars at risk caught (on fraud flagged HIGH) | $398,422.39 of $577,703.47 | $577,703.47 of $577,703.47 | $577,703.47 of $577,703.47 |
| Cost | $0 | $0 | $0.1960 (45 calls, 0 retries, 0 guard failures, 0 API errors) |
| Cached re-run cost | $0 | $0 | $0.0000 (45/45 cache hits, 0 calls) |

Rules miss: G02 (grey, supplier relocated to Canada). The regex does not pick up the new bank
country, so it scores LOW instead of the planted HIGH.

### Red-team set (10 cases, all fraud)

| Metric | Naive | Rules | Claude |
|---|---|---|---|
| Fraud recall (fraud flagged HIGH) | 80.0% (8/10) | 100.0% (10/10) | 100.0% (10/10) |
| False alarms | n/a (no legit cases) | n/a (no legit cases) | n/a (no legit cases) |
| Risk-level accuracy | 80.0% (8/10) | 100.0% (10/10) | 100.0% (10/10) |
| Signal recall | 33.3% (8/24) | 95.8% (23/24) | 100.0% (24/24) |
| Dollars at risk caught (on fraud flagged HIGH) | $121,797.01 of $196,388.39 | $196,388.39 of $196,388.39 | $196,388.39 of $196,388.39 |
| Cost | $0 | $0 | $0.0456 |
| Cached re-run cost | $0 | $0 | not measured |

Rules miss one signal: COUNTRY_CHANGE on R04 (compromised mailbox). R04 is still HIGH from its
other signals.

### Reworded held-out set (10 cases: 7 fraud, 3 legit)

| Metric | Naive | Rules | Claude |
|---|---|---|---|
| Fraud recall (fraud flagged HIGH) | 0.0% (0/7) | 14.3% (1/7) | 100.0% (7/7) |
| False alarms (legit P cases flagged HIGH) | 0.0% (0/3) | 0.0% (0/3) | 0.0% (0/3) |
| Risk-level accuracy | 30.0% (3/10) | 40.0% (4/10) | 100.0% (10/10) |
| Signal recall | 0.0% (0/8) | 25.0% (2/8) | 100.0% (8/8) |
| Dollars at risk caught (on fraud flagged HIGH) | $0.00 of $167,779.75 | $20,583.48 of $167,779.75 | $167,779.75 of $167,779.75 |
| Cost | $0 | $0 | $0.0418 |
| Cached re-run cost | $0 | $0 | not measured |

Rules catch only P04 (shared account, found by matching digits against the master data, not by
keywords). They miss reworded skip-verification (P01, P06), reworded urgency (P02), a country
named only by a city (P03 Pune, P07 Toronto) and a holder named inside a sentence (P05).

### What the numbers show

On the main and red-team sets the regex rules were already strong: 100% fraud recall on both,
because those cases use the wording the rules were written for. The difference shows on the
reworded set. There, rules-only fell to 14.3% fraud recall (1/7) and naive to 0/7, while the
Claude path caught 7/7 with 0 false alarms. Because of never-downgrade, the model can only add
signals to what the rules found; it cannot remove one.

Cost was about $0.0044 per request. Prompt caching read 1,717 system-prompt tokens from cache on
each call, and the response cache made a re-run of the main set cost $0. Latency was about 2.6
seconds per request. Two fresh runs of the main set gave the same result.

Each run writes its own report with a per-case table:

| Set | Report |
|---|---|
| main | `reports/eval_<by>.md` |
| red-team | `reports/eval_<by>_redteam.md` |
| reworded | `reports/eval_<by>_reworded.md` |

The Claude reports add a "Model run" table: model, prompt version, API calls, cache hits,
guard failures, invalid answers, API errors, cost and average latency.

## Data (synthetic, modeled on Oracle Fusion tables)

All names, domains, phone numbers, bank numbers and amounts are random and synthetic. The same
seed always produces byte-identical files.

| File | Modeled on | What it holds |
|---|---|---|
| `data/csv/suppliers.csv` | POZ_SUPPLIERS | supplier header: id, name, supplier number, country |
| `data/csv/supplier_sites.csv` | POZ_SUPPLIER_SITES_ALL_M | sites, with email domain on file and last update date |
| `data/csv/supplier_contacts.csv` | POZ_SUPPLIER_CONTACTS | contacts, with phone on file and last update date |
| `data/csv/bank_accounts.csv` | IBY_EXT_BANK_ACCOUNTS + IBY_PMT_INSTR_USES_ALL | accounts as token, last 4 and hash (never the full number), holder, assignment, active flag |
| `data/csv/payment_schedules.csv` | AP_PAYMENT_SCHEDULES_ALL | open amounts (exact decimals) and due dates |
| `data/csv/change_requests.csv` | not ERP (email / letter / portal note) | the incoming requests (UNTRUSTED text with raw bank numbers) |
| `data/plan.json`, `data/redteam_plan.json`, `data/reworded_plan.json` | - | planted labels for the three eval sets |

`build` also loads everything into `data/bankreview.duckdb` (money as DECIMAL(18,2), dates as
DATE). Reference date for the whole data set: 2026-11-02. Supplier countries: US, CA, GB, DE, IN.

## Run it on Windows PowerShell

Needs Python 3.11 or newer (developed with the `py` launcher and Python 3.14).

### Setup

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

### Tests

```powershell
python -m pytest
```

The tests never call the real API. They use a scripted fake client.

### Build the data set

```powershell
python -m bankreview build
```

### Eval without an API key (naive and rules)

```powershell
python -m bankreview eval --by naive
```

```powershell
python -m bankreview eval --by naive --set redteam
```

```powershell
python -m bankreview eval --by naive --set reworded
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

Review one case and print the result as JSON:

```powershell
python -m bankreview run --case F01 --by rules
```

`--by` is `naive`, `rules` or `claude`. `--set` is `main`, `redteam` or `reworded`. `eval`
also takes `--cases F01 F02` and `--limit N`.

### Real run with Claude (your own key, your own machine)

Set the model and the key in the current PowerShell window:

```powershell
$env:BANKREVIEW_MODEL = "claude-sonnet-5-5"
```

```powershell
$k = Read-Host "Anthropic API key" -AsSecureString
```

```powershell
$env:ANTHROPIC_API_KEY = [System.Net.NetworkCredential]::new("", $k).Password
```

The key lasts only for this PowerShell window. It is never typed into a command (so it is not
in PowerShell's history file) and never saved in the repo.

Second option: copy `.env.example` to `.env` and fill it in. `.env` is git-ignored, but it is
a file on disk, so the session variables above are preferred.

Then run:

```powershell
python -m bankreview run --case I01 --by claude
```

```powershell
python -m bankreview eval --by claude
```

```powershell
python -m bankreview eval --by claude --set redteam
```

```powershell
python -m bankreview eval --by claude --set reworded
```

Run the same eval again to see the cached re-run cost (cache hits, near $0):

```powershell
python -m bankreview eval --by claude
```

Force fresh calls:

```powershell
python -m bankreview eval --by claude --no-cache
```

## Honest notes

- **Synthetic data.** Every supplier, request and amount is generated. Real requests are
  messier and real fraud keeps changing.
- **Labels planted by the author.** The same person wrote the cases, the labels and the rules.
  The labels come from the plan files, not the rules code, but the main and red-team sets were
  written knowing what the rules look for.
- **The rules baseline is strong on templated wording and weak on reworded text.** Rules score
  100% fraud recall on the main and red-team sets, but 14.3% (1/7) on the held-out reworded
  set. That gap is the reason for the model path, and the reworded set is how to measure
  whether the model closes it.
- **CA institution-number gap on the model path.** The 3-digit Canadian institution number is
  too short to tokenize, so on the model path a shared Canadian account cannot be matched from
  the model's tokens alone. The rules-only floor still catches it. No planted case depends on it.
- **Explanation-number guard was too strict at first.** The first real run sent legit cases
  L01 and L04 to NEEDS_HUMAN because the explanation repeated the year from an effective date
  (for example "1 December 2026"), so the guard was narrowed to phone numbers from the body
  (unless on file) and numbers with 5+ digits.
- **The reworded set is small and not independent.** It has 10 cases, and the same author
  wrote the cases, the labels and the prompt. 100% means the model path caught every planted
  pattern in wording it had not seen, not that it is proven on real fraud.

## Project layout

| Module | Job |
|---|---|
| `generator.py` | writes the synthetic CSVs and the three plan files |
| `db.py` | loads the CSVs into DuckDB with real types; loads one case |
| `regex_extract.py` | regex-only extraction (the rules baseline) |
| `rules.py` | every signal, the risk rule, dollars at risk, hold, owner, actions |
| `baselines.py` | the naive and rules reviewers |
| `tokenize.py` | replaces bank numbers with `BANK_TOKEN_n (...last4)` |
| `packet.py` | builds the JSON packet sent to the model (UNTRUSTED body, masked master data) |
| `schema.py` | the facts-only answer schema (`extra="forbid"`) |
| `extractor.py` | the single Claude call, system prompt, one retry |
| `guards.py` | quote / token / number checks and never-downgrade |
| `cache.py`, `audit.py` | response cache and audit log |
| `claude_reviewer.py` | wires tokenize -> packet -> cache or call -> guards -> rules |
| `evaluate.py` | scores a reviewer against the planted labels and writes the reports |

## Status

- PR 1: project skeleton and planted data set (45 main, 10 red-team cases) with labels.
- PR 2: DuckDB load, Python rules for every signal, regex extraction, naive and rules
  baselines, eval against the planted labels.
- PR 3: bank-number tokens, the single Claude extraction call, guards, never-downgrade,
  response cache, audit log, held-out reworded set (P01-P10).
- PR 4: separate reports per set confirmed by a test; README rewrite.
- PR 5: explanation-number guard narrowed to body phone numbers and 5+ digit numbers.
- PRs 1-4 and the guard fix are merged. The real Claude run is done; its numbers are in the
  Results tables above.
