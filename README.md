# deid-guard

A Claude Code mod that de-identifies personal data in tabular files (CSV, TSV, XLSX, JSON, JSONL) **locally, before anything reaches the model**. It supports Korean and English data.

Claude decides which columns identify a person from column names and value shapes only. The user confirms. A local Python engine applies the decisions.

## How it works

```
Claude ──Read data.csv──▶ mod ──guard──▶ engine (Python, local)
       ◀── profile card (column names, types, shapes; no values)
Claude ──AskUserQuestion──▶ user: "De-identify patient_no and visit_date too?"
Claude ──mcp__deid-guard__apply──▶ engine writes a de-identified copy to .deid/out/
Claude ──Read data.csv──▶ de-identified copy
Every conversation row (prompts, tool results) ──session.append──▶ engine scrub ──▶ model
```

1. **First read.** Claude gets a profile card instead of the file. The card lists column names, types, unique ratios, value shapes (`A-######`) and what the detectors found. It contains no cell values.
2. **Judge and confirm.** Claude infers identifier columns (patient numbers, employee IDs, member IDs, ...) from the card and asks the user how to treat them with `AskUserQuestion`. If Claude asks to keep a masked column as is, deid-guard asks the user again in its own dialog; without that approval the column stays masked.
3. **Apply.** `mcp__deid-guard__apply` writes a de-identified copy under `.deid/out/`. From then on, reading the original file returns the copy.
4. **Safety net.** The engine scrubs every row the model reads: prompts, Read/Bash/Grep results and attachments. Known original values become their pseudonyms, and detected patterns become tokens.

### Treatment by column kind

| Kind | Examples | Default | Before a decision |
|---|---|---|---|
| Mandatory PII (forced) | Korean resident/foreigner registration number, phone, email, card number (Luhn), driver's license, passport and bank account (with context words), US SSN | Pseudonymize; cannot be kept | Masked |
| Direct identifier | Name, patient number, any ID column | Pseudonymize (`PATIENT_000123`) | Masked |
| Strong quasi-identifier | Birth date, address | Generalize (birth year; city/district) | Masked with the generalized value |
| Quasi-identifier | ZIP code, age, sex, dates | ZIP to first 3 digits; others kept | Unchanged |
| Free text | Memo, clinical notes | Keep; detectors and known values replaced inside cells | Detectors only |

- Pseudonyms are stable. The same value gets the same token in every file, so joins and counts still work.
- All state lives in the project's `.deid/` directory, which ignores itself in git. The model can read only `.deid/out` and `.deid/cards`.
- Headers are shown as `col_N` when a file has no header row (its first row is data) or when headers look like person names (pivot tables).

## Requirements

- Claude Code **2.1.290 or later** (mods and `prompt.mention`). Tested with 2.1.295.
- Python **3.9 or later**. The engine uses only the standard library, so nothing needs `pip install`.
- Detection and transformation run locally. No external API or LLM is called.

## Install

Inside Claude Code:

```
/plugin install deid-guard --marketplace godic97/deid-guard
```

Or from your shell:

```bash
claude plugin marketplace add godic97/deid-guard
claude plugin install deid-guard@godic97
```

To try a local checkout for one session:

```bash
git clone https://github.com/godic97/deid-guard.git
claude --plugin-dir ./deid-guard
```

To use another Python, change the `deid-guard.python` row in `/config` (default `python3`).

## What deid-guard runs and sends

- It runs one local program: `python3 <plugin>/engine/deid.py`, started by the mod for each check.
- It reads the data files Claude touches and writes only under `.deid/` in your project: the mapping database, profile cards and de-identified copies.
- It makes no network requests and sends nothing anywhere. The only text that leaves your machine is what Claude Code already sends to the model, after deid-guard has scrubbed it.
- It adds one tool (`mcp__deid-guard__apply`), two commands, a short system prompt section that explains the workflow to Claude, and a status line entry.

## Commands

- `/deid-status`: list the data files deid-guard guards and their copies.
- `/deid-reveal PATIENT_NO_000012`: show the original value in a **local toast only**. It is never sent to the model.

## Limitations (v0.1)

- **Unstructured documents (txt, docx, pdf) are not handled.** Free-text cells inside tables are scrubbed only with the detectors and values already known from data files. A person's name typed into a prompt is sent as is unless it appears in a guarded data file.
- Pasted images and PDFs that Claude reads natively are not inspected.
- If one Bash command creates a data file and prints it right away, only the detectors apply to that output. The file is profiled from the next command on.
- The local transcript file (`~/.claude/projects/.../*.jsonl`) keeps original values in its screen-only fields (`toolUseResult`) and its input queue records (`queue-operation`). Those fields are not part of model requests.
- If the Python engine cannot run, deid-guard refuses every Read, search and shell command that names an existing data file, but it cannot scrub other conversation rows. The status line then shows `ENGINE UNAVAILABLE`.
- deid-guard guards against accidental exposure, not against a model that sets out to defeat it. Bash runs arbitrary code with your permissions, so a command could still tamper with `.deid/` in ways the path checks do not recognise. Keep permission prompts on for Bash when you work with sensitive data.
- Mods are an early-access Claude Code API and may change between releases.
- This is a supplementary safeguard. It does not guarantee compliance with privacy law (PIPA, GDPR, HIPAA, ...).

## Development

```bash
cd engine && python3 -m unittest discover -s tests -t .   # engine tests
claude plugin test .                                        # mod tests
claude plugin validate .                                    # static checks
```

All test data is synthetic (`engine/tests/fixtures.py`).
