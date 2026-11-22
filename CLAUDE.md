# Supplier Bank-Change Risk Reviewer — project rules

## What this is
A portfolio project by Vinit (Oracle ERP techno-functional, 12+ years AP/AR/GL/P2P; owner of
Oracle Fusion CDM). A supplier asks to change bank details by email, letter or portal note.
The tool reads the request, compares it with the supplier master (synthetic CSVs modeled on
Oracle Fusion tables), works out the dollars at risk, scores the risk with Python rules, and
recommends verification steps. It SUGGESTS; a human decides. It never changes bank details,
never approves a change, and never calls any phone number. Synthetic data only. USD only.
Local DuckDB only.

The design is in docs/design.md. Follow it. If something in it seems wrong, say so in the PR
description instead of silently changing it.

## Keep it simple (on purpose)
- ONE Claude call per request for extraction. No agent loop, no tools, no MCP.
- Python owns every decision: signals, risk level, dollars at risk, actions, owner.
- Prefer the simplest code that meets the design. Do not add features that are not in it.

## Non-negotiable design rules
- Bank numbers never reach the model. Before any text is sent, Python replaces account
  numbers, routing numbers and IBANs (including ones written with spaces or dashes) with
  tokens like BANK_TOKEN_1 (...4821). A test scans every prompt sent to the (fake) client and
  fails if any full bank number appears. The audit log stores ids and hashes only.
- The model only EXTRACTS (fields + exact quotes) and explains. It does not set risk level,
  hold decisions, owner or dollars at risk. The output schema has no such fields
  (pydantic, extra="forbid").
- Every quote the model extracts must be an exact substring of the (tokenized) request text,
  and every token it references must exist. Otherwise -> NEEDS_HUMAN.
- Never-downgrade: risk = the higher of (regex/rules-only signals) and (signals using the
  model's extraction). The model can add signals, never remove one the rules found.
- Every change requires a call-back to the phone number ON FILE, never to a number in the
  request. The new account is never activated before the call-back. Payments are held only
  when risk is HIGH and the new account is already active.
- Request text is UNTRUSTED: wrap it as <UNTRUSTED>...</UNTRUSTED> with < and > escaped.
- Eval labels come from the planted plan (plan.json, redteam_plan.json), NEVER from the rules
  code. The red-team set is reported separately and is never used to tune the prompt.
- Money is Decimal, never float. Edge-case test data uses exact numbers.
- Caching: response cache keyed by sha256(model + prompt version + tokenized packet). Never
  cache API errors or invalid answers. Guards always run, even on cached answers.
  --no-cache forces fresh calls. System prompt sent with cache_control.
- LLM: Anthropic Claude, model from BANKREVIEW_MODEL in .env (default claude-sonnet-5-5),
  max_tokens 4000, full error messages in logs. One retry with the full validation error.

## Working rules
- Never commit .env or any secret. .gitignore covers .env, .venv/, *.db, *.duckdb, logs/,
  *.egg-info/, data/csv/, data/*.json, data/cache/, reports/, __pycache__/, .pytest_cache/.
- There is no API key in this sandbox. Never call the real Claude API here. Test the model
  call with a scripted fake client. Vinit runs real evals on his laptop.
- One milestone per PR. All tests must pass before you stop.
- Every PR description must include, in simple everyday language:
  1. What this PR adds and why
  2. A short "what to review" checklist (the 3-5 things most worth checking)
  3. Controls/accounting notes where relevant (risk signals, dollars at risk, who owns the
     decision, hold vs no hold, grey areas and the default)
  4. How to test it on Windows PowerShell: py launcher (Python 3.14), venv activated with
     Set-ExecutionPolicy -Scope Process Bypass then .venv\Scripts\Activate.ps1, python -m pip
  5. Any new Python concept explained in 2-3 plain sentences
