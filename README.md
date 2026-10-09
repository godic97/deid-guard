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
- A JSON file counts as a table when it holds at least two records: a list of objects, objects keyed by ID, or rows as lists (pandas `orient="split"` or `"values"`). Other JSON, such as `package.json`, is read as it is, but values under personal-looking keys (`성명`, `phone`, `patient.name`, ...) are still masked.
- Masks only grow. Rewriting or shrinking a file does not unmask values it held before; only a user-approved "keep" does.
- A file is re-profiled when its size, modification time, change time or inode changes, so restoring the modification time after an edit does not hide it.
- deid-guard profiles every data file in the project when a session starts and again before each shell command, so a file a command reaches through a glob or a script is known before its output is read. Unchanged files cost one `stat`.
- Headers are shown as `col_N` when a file has no header row (its first row is data) or when headers look like person names (pivot tables).

## Requirements

- Claude Code **2.1.290 or later** (mods and `prompt.mention`). Tested with 2.1.295.
- Python **3.9 or later**. The engine uses only the standard library, so nothing needs `pip install`.
- Detection and transformation run locally. No external API or LLM is called.

## Install

Inside Claude Code:

```
/plugin marketplace add gingoa-ai/deid-guard
/plugin install deid-guard@godic97
```

Or from your shell:

```bash
claude plugin marketplace add gingoa-ai/deid-guard
claude plugin install deid-guard@godic97
```

To try a local checkout for one session:

```bash
git clone https://github.com/gingoa-ai/deid-guard.git
claude --plugin-dir ./deid-guard
```

To use another Python, change the `deid-guard.python` row in `/config` (default `python3`).

## What deid-guard runs and sends

- It runs one local program: `python3 <plugin>/src/deid.py`, started by the mod for each check.
- It reads the data files Claude touches and writes only under `.deid/` in your project: the mapping database, profile cards and de-identified copies.
- It makes no network requests and sends nothing anywhere. The only text that leaves your machine is what Claude Code already sends to the model, after deid-guard has scrubbed it.
- It adds one tool (`mcp__deid-guard__apply`), two commands, a short system prompt section that explains the workflow to Claude, and a status line entry.

## Commands

- `/deid-status`: list the data files deid-guard guards and their copies.
- `/deid-reveal PATIENT_NO_000012`: show the original value in a **local toast only**. It is never sent to the model.

## Limitations (v0.1)

- **Unstructured documents (txt, docx, pdf) are not handled.** Free-text cells inside tables are scrubbed only with the detectors and values already known from data files. A person's name typed into a prompt is sent as is unless it appears in a guarded data file.
- Pasted images and PDFs that Claude reads natively are not inspected.
- If one Bash command creates a data file and prints it right away, only the detectors apply to that output. The file is profiled before the next command.
- In code repositories, JSON arrays of objects (test fixtures, for example) are profiled like any table, so a `name` field there may be masked until you clear the file.
- The local transcript file (`~/.claude/projects/.../*.jsonl`) keeps original values in its screen-only fields (`toolUseResult`) and its input queue records (`queue-operation`). Those fields are not part of model requests.
- If the Python engine cannot run, deid-guard refuses every Read, search and shell command that names an existing data file and withholds all tool output from the model. Prompts you type still go through unscrubbed, so you can ask Claude about the problem. The status line then shows `ENGINE UNAVAILABLE`.
- deid-guard guards against accidental exposure, not against a model that sets out to defeat it. Bash runs arbitrary code with your permissions, so a command could still tamper with `.deid/` in ways the path checks do not recognise. Keep permission prompts on for Bash when you work with sensitive data.
- Mods are an early-access Claude Code API and may change between releases.
- This is a supplementary safeguard. It does not guarantee compliance with privacy law (PIPA, GDPR, HIPAA, ...).

## Development

```bash
cd src && python3 -m unittest discover -s tests -t .      # engine tests, standard library only
claude plugin test .                                        # mod tests
claude plugin validate .                                    # static checks
```

Mutation testing uses [mutmut](https://github.com/boxed/mutmut) 3 through [mutation-gate](https://github.com/gingoa-ai/mutation-gate), with the settings in `pyproject.toml`:

```bash
uv venv .venv && uv pip install --python .venv/bin/python pytest mutmut
mutation-gate test src/deidlib
```

All test data is synthetic (`src/tests/fixtures.py`).

## License

MIT. See [LICENSE](LICENSE).
