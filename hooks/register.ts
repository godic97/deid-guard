// deid-guard: keeps personal data in tabular files away from the model.
//
// Data files are read through a profile card until the user decides how to
// treat each column, then through a de-identified copy. Every row the model
// will read is scrubbed against the values the engine knows. The engine is
// the Python program in ../engine; nothing here leaves the machine.
import type { EngineInterface, Register } from 'claude-code'
import {
  type Content,
  dataPathsIn,
  tokensIn,
  isDataFile,
  isGeneratedPath,
  textsOf,
  touchesState,
  withTexts,
} from './content.ts'

type EngineResult = Record<string, any>
type Decision = { column: string; action: string; [key: string]: unknown }

const KEEP_MASKED = 'Keep masked'
const SEND_UNMASKED = 'Send unmasked'

const DENY_STATE =
  'deid-guard: .deid/ holds the re-identification state and is off limits. ' +
  'Read a data file directly to get its profile or its de-identified copy.'
const DENY_ENGINE =
  'deid-guard: the engine runs only through the plugin, so that unmasking always needs the user. ' +
  'Use mcp__deid-guard__apply.'
const RUNS_ENGINE = /deid\.py|deidlib/
// Rows a person typed. They pass even when the engine is down, so the user
// can still talk to Claude about the problem.
const TYPED_DOORS = new Set(['prompt', 'command'])
const WITHHELD = '[deid-guard: content withheld because the de-identification engine failed]'
// Rows the model never reads, or wrote itself.
const SKIP_DOORS = new Set(['response', 'notice'])
// Attachments the engine writes from its own state, never from user data.
const SAFE_ATTACHMENTS = new Set([
  'skill_listing', 'deferred_tools_delta', 'todo_reminder', 'auto_mode', 'auto_mode_exit',
  'plan_mode', 'plan_mode_exit', 'critical_system_reminder', 'output_style',
])
const PATH_FIELDS = ['file_path', 'path', 'notebook_path', 'pattern', 'glob', 'command']

const SYSTEM_NOTE = `deid-guard is active in this session. Tabular data files (.csv, .tsv, .xlsx, .json, .jsonl) may hold personal data and are de-identified locally before you see them.
- Reading a data file you have not cleared returns a profile card (column names, types, shapes) instead of values.
- From the card, judge which columns identify a person, ask the user with AskUserQuestion how to treat them, then call mcp__deid-guard__apply with the decisions.
- After that, reading the file returns the de-identified copy under .deid/out/. Run analysis code against that copy.
- Values like PATIENT_NO_000012 or PHONE_000003 are stable pseudonyms: equal tokens mean equal originals, so joins and counts still work. Never try to recover originals; the user can see them with /deid-reveal.
- Everything under .deid/ except out/ and cards/ is off limits.`

const APPLY_SCHEMA = {
  type: 'object',
  properties: {
    file: { type: 'string', description: 'The data file, as you read it' },
    decisions: {
      type: 'array',
      description: 'One entry per column you or the user decided on; other columns get the card\'s default',
      items: {
        type: 'object',
        properties: {
          column: { type: 'string', description: 'Column name as the card shows it, or #index' },
          action: { type: 'string', enum: ['pseudonymize', 'generalize', 'drop', 'keep'] },
          entity: { type: 'string', description: 'Pseudonym prefix, e.g. PATIENT; reuse it across files to keep joins' },
          kind: {
            type: 'string',
            enum: ['birthdate', 'date', 'address', 'zip', 'age'],
            description: 'What to generalize the column as, when the card did not detect it',
          },
          table: { type: 'string', description: 'Sheet name, for workbooks with repeated column names' },
        },
        required: ['column', 'action'],
      },
    },
  },
  required: ['file'],
}

let python = 'python3'
let engineReady = true

async function engine($: EngineInterface, args: string[], input?: unknown): Promise<EngineResult> {
  const root = await $.session.root()
  const init = input === undefined ? { timeoutMs: 600_000 } : { timeoutMs: 600_000, stdin: JSON.stringify(input) }
  const ran = await $.process.run([python, `${$.plugin.root}/src/deid.py`, ...args, '--root', root], init)
  let out: EngineResult
  try {
    out = JSON.parse(ran.stdout)
  } catch {
    throw new Error(`engine printed no JSON (exit ${ran.exitCode}): ${ran.stderr.slice(0, 200)}`)
  }
  if (ran.exitCode !== 0 || out.error) throw new Error(String(out.error ?? `engine exited ${ran.exitCode}`))
  return out
}

/** Where a path lands after links; the path itself when that is unknown. */
async function realPathOf($: EngineInterface, path: string): Promise<string> {
  try {
    const stat = await $.fs.stat(path, { resolve: true })
    return stat.realPath ?? path
  } catch {
    return path
  }
}

async function readPathFor($: EngineInterface, path: string): Promise<string> {
  const guarded = await engine($, ['guard', path])
  return String(guarded.read_path)
}

/**
 * Profile the data files a tool is about to touch, so the scrubber knows
 * their values. An existing file that cannot be profiled (engine missing,
 * unreadable file) refuses the call: its output could not be scrubbed.
 */
async function guardOrRefuse($: EngineInterface, paths: readonly string[]): Promise<string | undefined> {
  for (const path of paths) {
    if (isGeneratedPath(path)) continue
    try {
      await engine($, ['guard', path])
    } catch (err) {
      if (await $.fs.exists(path)) return failure(path, err)
    }
  }
  return undefined
}

/** Text from a file or the model, made safe to show in a dialog line. */
function forDialog(text: string, max: number): string {
  const flat = text.replace(/[\u0000-\u001f\u007f\u2028\u2029]+/g, ' ').trim()
  return JSON.stringify(flat.length > max ? flat.slice(0, max) + '…' : flat)
}

function failure(what: string, error: unknown): string {
  const reason = error && typeof error === 'object' && 'message' in error ? String(error.message) : 'unknown error'
  return `deid-guard: could not de-identify ${what}, so it was not read (${reason})`
}

export const register: Register = (on, options) => {
  python = String(options.python ?? 'python3')

  on('session.start', async ($, e, next) => {
    await $.tool.register({
      name: 'apply',
      description:
        'Apply de-identification decisions to a data file deid-guard profiled. Call it after asking the user. ' +
        'Writes a de-identified copy under .deid/out/ that later reads of the file return.',
      inputSchema: APPLY_SCHEMA,
    })
    await $.command.register({
      name: 'deid-reveal',
      description: 'Show the original value behind a deid-guard pseudonym, locally (never sent to the model)',
      argumentHint: '<TOKEN>',
      immediate: true,
    })
    await $.command.register({ name: 'deid-status', description: 'List the data files deid-guard guards' })
    try {
      await engine($, ['ping'])
      engineReady = true
      $.ui.status('deid-guard: scanning data files…')
      const scanned = await engine($, ['scan'])
      $.ui.status(`deid-guard: ${scanned.files.length} data file(s) guarded`)
    } catch (err) {
      engineReady = false
      $.ui.status('deid-guard: ENGINE UNAVAILABLE, nothing is scrubbed')
      $.ui.toast(failure('data', err).replace('so it was not read ', '') + ` Check that "${python}" runs Python 3.9+.`, {
        timeoutMs: 15_000,
      })
    }
    return next(e)
  })

  on('prompt.compose', async ($, e, next) => {
    const composed = await next(e)
    return { sections: [...composed.sections, { id: 'deid-guard', text: SYSTEM_NOTE, scope: 'session' }] }
  })

  // Keep every tool away from the re-identification state, and profile the
  // data files a tool names (Read and Bash have their own hooks below).
  on('tool.call', async ($, e, next) => {
    const input = e as unknown as Record<string, unknown>
    const paths = PATH_FIELDS.map(f => input[f]).filter((v): v is string => typeof v === 'string')
    if (paths.some(touchesState)) return { deny: DENY_STATE }
    const tool = String(e.tool)
    if (tool === 'Read' || tool === 'Bash' || tool.startsWith('mcp__deid-guard__')) return next(e)
    const refused = await guardOrRefuse($, paths.filter(p => p !== input.command && isDataFile(p)))
    return refused ? { deny: refused } : next(e)
  }).catch(() => ({ deny: DENY_STATE }))

  on('tool.call', { tool: 'Read' }, async ($, e, next) => {
    const real = await realPathOf($, e.file_path)
    if (touchesState(real)) return { deny: DENY_STATE }
    if (!isDataFile(real) || isGeneratedPath(real)) return next(e)
    if (!engineReady) return { deny: failure(e.file_path, new Error('engine unavailable')) }
    return next({ ...e, file_path: await readPathFor($, real) })
  }).catch(($, e, next) => ({ deny: failure('this data file', next.error) }))

  // Profile the data files a command names, so their values are known to the
  // scrubber before the command prints any of them.
  on('tool.call', { tool: 'Bash' }, async ($, e, next) => {
    if (RUNS_ENGINE.test(e.command)) return { deny: DENY_ENGINE }
    // Shell syntax can name a data file in ways no parser here sees (globs,
    // variables, quoting, find -exec), so every command profiles new and
    // changed data files first. Unchanged files cost the engine one stat.
    await engine($, ['scan'])
    const refused = await guardOrRefuse($, dataPathsIn(e.command))
    return refused ? { deny: refused } : next(e)
  }).catch(($, e, next) => ({ deny: failure('the data files in this project', next.error) }))

  // A pseudonym in old_string stands for the original in the file on disk.
  // Only those pseudonyms are restored, and only when the restored text is
  // really in the file, so an edit cannot write other originals to disk.
  on('tool.call', { tool: 'Edit' }, async ($, e, next) => {
    const tokens = tokensIn(e.old_string)
    if (!tokens.length) return next(e)
    const text = await $.fs.read(e.file_path)
    if (typeof text !== 'string' || text.includes(e.old_string)) return next(e)
    const restored = await engine($, ['restore'], { texts: [e.old_string, ...tokens] })
    const [oldString, ...originals] = restored.texts as string[]
    if (!text.includes(oldString)) return next(e)
    let newString = e.new_string
    tokens.forEach((token, i) => {
      newString = newString.split(token).join(originals[i])
    })
    return next({ ...e, old_string: oldString, new_string: newString })
  }).catch(($, e, next) => next(e))

  // Keeping a pending column sends its raw values to the model, so only the
  // user can allow it, in a dialog the model does not answer.
  on('tool.call', { tool: 'mcp__deid-guard__apply' }, async ($, e) => {
    const input = e as unknown as { file?: string; decisions?: Decision[] }
    if (!input.file) return { result: 'deid-guard: pass the data file as "file".', isError: true }
    let decisions = input.decisions ?? []
    let note = ''
    const plan = await engine($, ['plan', input.file], { decisions })
    const unmasks = (plan.unmasks ?? []) as string[]
    if (unmasks.length) {
      // Name the file and columns as the engine resolved them: the model's
      // own strings could carry text that talks the user into approving.
      const columns = ((plan.columns ?? []) as string[]).map(c => forDialog(c, 40)).join(', ')
      let answer = ''
      try {
        answer = await $.ui.ask(
          `deid-guard (not Claude) asks: send these columns of ${forDialog(String(plan.file), 120)} to the model without masking: ${columns}?`,
          { header: 'Unmask?', options: [KEEP_MASKED, SEND_UNMASKED] },
        )
      } catch {
        // No one could answer: the columns stay masked.
      }
      if (answer !== SEND_UNMASKED) {
        // Drop every keep, so no other spelling of a column (#2) slips through.
        // Keep is the default for unguarded columns, so nothing else changes.
        decisions = decisions.filter(d => d.action !== 'keep')
        note = `\n\nUnmasking ${columns} was not approved by the user, so those columns keep their default treatment.`
      }
    }
    const applied = await engine($, ['apply', input.file], { decisions })
    return { result: String(applied.summary) + note }
  }).catch(($, e, next) => ({ result: `deid-guard: apply failed: ${next.error?.message ?? 'unknown error'}`, isError: true }))

  on('prompt.mention', async ($, e, next) => {
    const real = await realPathOf($, e.path)
    if (touchesState(real)) return { deny: DENY_STATE }
    if (!isDataFile(real) || isGeneratedPath(real)) return next(e)
    return next({ ...e, path: await readPathFor($, real) })
  }).catch(($, e, next) => ({ deny: failure('the mentioned data file', next.error) }))

  // Every row the model reads: prompts, tool results, notes, hook output.
  on('session.append', async ($, e, next) => {
    if (SKIP_DOORS.has(e.door)) return next(e)
    const content = e.message.content as unknown as Content
    if (!engineReady) {
      if (TYPED_DOORS.has(e.door)) return next(e)
      const withheld = withTexts(content, textsOf(content).map(() => WITHHELD)) as unknown as typeof e.message.content
      return next({ ...e, message: { ...e.message, content: withheld } })
    }
    const texts = textsOf(content)
    if (!texts.some(t => t.trim())) return next(e)
    const scrubbed = await engine($, ['scrub'], { texts })
    if (!scrubbed.hits) return next(e)
    const rewritten = withTexts(content, scrubbed.texts) as unknown as typeof e.message.content
    return next({ ...e, message: { ...e.message, content: rewritten } })
  }).catch(($, e, next) => {
    const content = e.message.content as unknown as Content
    const withheld = withTexts(content, textsOf(content).map(() => WITHHELD)) as unknown as typeof e.message.content
    return next({ ...e, message: { ...e.message, content: withheld } })
  })

  // Messages the engine adds per request, such as a file the user mentioned
  // or a file that changed on disk.
  on('prompt.attachment', async ($, e, next) => {
    if (SAFE_ATTACHMENTS.has(e.type) || !e.text.trim()) return next(e)
    if (!engineReady) return next({ ...e, text: WITHHELD })
    const scrubbed = await engine($, ['scrub'], { texts: [e.text] })
    return scrubbed.hits ? next({ ...e, text: scrubbed.texts[0] }) : next(e)
  }).catch(($, e, next) => next({ ...e, text: WITHHELD }))

  on('command.run', { command: 'deid-reveal' }, async ($, e) => {
    const token = e.args.trim()
    const revealed = await engine($, ['reveal', token])
    $.ui.toast(revealed.raw == null ? `deid-guard: no such token ${token}` : `${token} = ${revealed.raw}`, {
      timeoutMs: 20_000,
    })
    return {}
  })

  on('command.run', { command: 'deid-status' }, async $ => {
    const status = await engine($, ['status'])
    const files = status.files as { file: string; status: string; outputs: string[] }[]
    if (!files.length) return { text: 'deid-guard: no data files guarded yet.' }
    const lines = files.map(f => `- ${f.file}: ${f.status}${f.outputs.length ? ` -> ${f.outputs.join(', ')}` : ''}`)
    return { text: ['deid-guard guards these data files:', ...lines].join('\n') }
  })
}
