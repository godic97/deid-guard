import { describe, expect, test } from 'claude-code/testing'
import type { On } from 'claude-code'

type Call = { cmd: string; arg?: string; input?: { texts?: string[]; decisions?: unknown[] } }
type Handler = (arg: string | undefined, input: Call['input']) => Record<string, unknown>

// Answers the plugin's $.process.run calls in place of the Python engine.
function fakeEngine(on: On, handlers: Record<string, Handler>): Call[] {
  const calls: Call[] = []
  on('session.root', () => ({ value: '/p' }))
  on('process.run', ($, e) => {
    const [, , cmd, maybeArg] = e.argv
    const arg = maybeArg === '--root' ? undefined : maybeArg
    const input = e.init?.stdin ? JSON.parse(e.init.stdin) : undefined
    calls.push({ cmd, arg, input })
    const out = handlers[cmd]?.(arg, input) ?? { ok: true }
    return { value: { exitCode: 'error' in out ? 1 : 0, stdout: JSON.stringify(out), stderr: '' } }
  })
  return calls
}

const upper: Handler = (_, input) => ({ texts: (input?.texts ?? []).map(t => t.replace(/홍길동/g, 'NAME_000001')), hits: 1 })

describe('reading data files', () => {
  test('Read of a data file reads what the engine points at', async ($, on) => {
    const calls = fakeEngine(on, { guard: () => ({ status: 'pending', read_path: '/p/.deid/cards/a.csv.md' }) })
    let readPath = ''
    on('tool.call', { tool: 'Read' }, ($, e) => {
      readPath = e.file_path
      return { result: 'card' }
    })
    await $.tool.call({ tool: 'Read', file_path: '/p/a.csv' })
    expect(readPath).toBe('/p/.deid/cards/a.csv.md')
    expect(calls.find(c => c.cmd === 'guard')?.arg).toBe('/p/a.csv')
  })

  test('a link to a data file is guarded as the data file', async ($, on) => {
    const calls = fakeEngine(on, { guard: () => ({ status: 'pending', read_path: '/p/.deid/cards/a.csv.md' }) })
    on('fs.stat', () => ({ value: { kind: 'file', size: 10, isLink: true, realPath: '/p/data/a.csv' } }))
    let readPath = ''
    on('tool.call', { tool: 'Read' }, ($, e) => {
      readPath = e.file_path
      return { result: 'card' }
    })
    await $.tool.call({ tool: 'Read', file_path: '/p/notes.txt' })
    expect(readPath).toBe('/p/.deid/cards/a.csv.md')
    expect(calls.find(c => c.cmd === 'guard')?.arg).toBe('/p/data/a.csv')
  })

  test('Read of other files is untouched', async ($, on) => {
    const calls = fakeEngine(on, {})
    let readPath = ''
    on('tool.call', { tool: 'Read' }, ($, e) => {
      readPath = e.file_path
      return { result: 'x' }
    })
    await $.tool.call({ tool: 'Read', file_path: '/p/main.py' })
    expect(readPath).toBe('/p/main.py')
    expect(calls.filter(c => c.cmd === 'guard')).toEqual([])
  })

  test('Read of the engine state is refused', async ($, on) => {
    fakeEngine(on, {})
    const r = await $.tool.call({ tool: 'Read', file_path: '/p/.deid/state/map.sqlite' })
    expect(JSON.stringify(r)).toContain('deid-guard')
  })

  test('Read is refused when the engine fails', async ($, on) => {
    fakeEngine(on, { guard: () => ({ error: 'boom' }) })
    let ran = false
    on('tool.call', { tool: 'Read' }, () => {
      ran = true
      return { result: 'raw' }
    })
    await $.tool.call({ tool: 'Read', file_path: '/p/a.csv' })
    expect(ran).toBe(false)
  })
})

describe('shell commands', () => {
  test('data files a command names are profiled before it runs', async ($, on) => {
    const calls = fakeEngine(on, { guard: () => ({ status: 'pending', read_path: 'x' }) })
    on('tool.call', { tool: 'Bash' }, () => ({ result: { stdout: '', stderr: '', interrupted: false } }))
    await $.tool.call({ tool: 'Bash', command: 'head data/p.csv' })
    expect(calls.filter(c => c.cmd === 'guard').map(c => c.arg)).toEqual(['data/p.csv'])
  })

  test('a command over an existing data file is refused when it cannot be profiled', async ($, on) => {
    fakeEngine(on, { guard: () => ({ error: 'engine unavailable' }) })
    on('fs.exists', () => ({ value: true }))
    let ran = false
    on('tool.call', { tool: 'Bash' }, () => {
      ran = true
      return { result: { stdout: '', stderr: '', interrupted: false } }
    })
    await $.tool.call({ tool: 'Bash', command: 'cat data/p.csv' })
    expect(ran).toBe(false)
  })

  test('a command with a data file pattern rescans the project first', async ($, on) => {
    const calls = fakeEngine(on, { guard: () => ({ error: 'FileNotFoundError' }), scan: () => ({ files: [] }) })
    on('fs.exists', () => ({ value: false }))
    on('tool.call', { tool: 'Bash' }, () => ({ result: { stdout: '', stderr: '', interrupted: false } }))
    await $.tool.call({ tool: 'Bash', command: 'cat data/*.csv' })
    expect(calls.map(c => c.cmd)).toContain('scan')
  })

  test('a command that creates a data file still runs', async ($, on) => {
    fakeEngine(on, { guard: () => ({ error: 'FileNotFoundError' }) })
    on('fs.exists', () => ({ value: false }))
    let ran = false
    on('tool.call', { tool: 'Bash' }, () => {
      ran = true
      return { result: { stdout: '', stderr: '', interrupted: false } }
    })
    await $.tool.call({ tool: 'Bash', command: 'python3 make.py > out.csv' })
    expect(ran).toBe(true)
  })

  test('a command that runs the engine directly is refused', async ($, on) => {
    fakeEngine(on, {})
    let ran = false
    on('tool.call', { tool: 'Bash' }, () => {
      ran = true
      return { result: { stdout: '', stderr: '', interrupted: false } }
    })
    await $.tool.call({ tool: 'Bash', command: 'echo {} | python3 ~/x/deid-guard/engine/deid.py apply a.csv --root .' })
    expect(ran).toBe(false)
  })

  test('a command that touches the engine state is refused', async ($, on) => {
    fakeEngine(on, {})
    let ran = false
    on('tool.call', { tool: 'Bash' }, () => {
      ran = true
      return { result: { stdout: '', stderr: '', interrupted: false } }
    })
    await $.tool.call({ tool: 'Bash', command: 'sqlite3 .deid/state/map.sqlite .dump' })
    expect(ran).toBe(false)
  })
})

describe('conversation rows', () => {
  test('text the model will read is scrubbed', async ($, on) => {
    fakeEngine(on, { scrub: upper })
    let stored: unknown
    on('session.append', ($, e, next) => {
      stored = e.message.content
      return next(e)
    })
    await $.session.append({ message: { type: 'user', content: [{ type: 'text', text: '홍길동 내원' }] } })
    expect(stored).toEqual([{ type: 'text', text: 'NAME_000001 내원' }])
  })

  test('when scrubbing fails the text is withheld', async ($, on) => {
    fakeEngine(on, { scrub: () => ({ error: 'boom' }) })
    let stored: unknown
    on('session.append', ($, e, next) => {
      stored = e.message.content
      return next(e)
    })
    await $.session.append({ message: { type: 'user', content: [{ type: 'text', text: '홍길동 내원' }] } })
    expect(JSON.stringify(stored)).not.toContain('홍길동')
    expect(JSON.stringify(stored)).toContain('withheld')
  })
})

describe('other tools', () => {
  test('a search over an existing data file that cannot be profiled is refused', async ($, on) => {
    fakeEngine(on, { guard: () => ({ error: 'engine unavailable' }) })
    on('fs.exists', () => ({ value: true }))
    let ran = false
    on('tool.call', { tool: 'Grep' } as never, () => {
      ran = true
      return { result: 'x' }
    })
    await $.tool.call({ tool: 'Grep', pattern: 'x', path: 'data/p.csv' } as never)
    expect(ran).toBe(false)
  })
})

describe('editing', () => {
  test('tokens in old_string are restored when the file holds the original', async ($, on) => {
    fakeEngine(on, { restore: (_, input) => ({ texts: (input?.texts ?? []).map(t => t.replace('NAME_000001', '홍길동')) }) })
    on('fs.read', () => ({ value: 'name: 홍길동\n' }))
    let edit: { old_string?: string; new_string?: string } = {}
    on('tool.call', { tool: 'Edit' }, ($, e) => {
      edit = e
      return { result: 'ok' }
    })
    await $.tool.call({ tool: 'Edit', file_path: '/p/c.yml', old_string: 'name: NAME_000001', new_string: 'name: NAME_000001 (kr)' })
    expect(edit.old_string).toBe('name: 홍길동')
    expect(edit.new_string).toBe('name: 홍길동 (kr)')
  })

  test('tokens that only new_string names are not restored', async ($, on) => {
    fakeEngine(on, {
      restore: (_, input) => ({
        texts: (input?.texts ?? []).map(t => t.replace('NAME_000001', '홍길동').replace('ID_000007', '1234')),
      }),
    })
    on('fs.read', () => ({ value: 'name: 홍길동\n' }))
    let edit: { old_string?: string; new_string?: string } = {}
    on('tool.call', { tool: 'Edit' }, ($, e) => {
      edit = e
      return { result: 'ok' }
    })
    await $.tool.call({ tool: 'Edit', file_path: '/p/c.yml', old_string: 'name: NAME_000001', new_string: 'name: NAME_000001 ID_000007' })
    expect(edit.old_string).toBe('name: 홍길동')
    expect(edit.new_string).toBe('name: 홍길동 ID_000007')
  })

  test('nothing is restored when the restored text is not in the file', async ($, on) => {
    fakeEngine(on, { restore: (_, input) => ({ texts: (input?.texts ?? []).map(t => t.replace('ID_000007', '1234')) }) })
    on('fs.read', () => ({ value: 'other\n' }))
    let edit: { old_string?: string; new_string?: string } = {}
    on('tool.call', { tool: 'Edit' }, ($, e) => {
      edit = e
      return { result: 'ok' }
    })
    await $.tool.call({ tool: 'Edit', file_path: '/p/c.yml', old_string: 'x ID_000007', new_string: 'y ID_000007' })
    expect(edit.new_string).toBe('y ID_000007')
  })

  test('tokens are kept when the file holds them literally', async ($, on) => {
    fakeEngine(on, {})
    on('fs.read', () => ({ value: "ids = ['NAME_000001']\n" }))
    let edit: { old_string?: string } = {}
    on('tool.call', { tool: 'Edit' }, ($, e) => {
      edit = e
      return { result: 'ok' }
    })
    await $.tool.call({ tool: 'Edit', file_path: '/p/a.py', old_string: "['NAME_000001']", new_string: '[]' })
    expect(edit.old_string).toBe("['NAME_000001']")
  })
})

describe('the apply tool', () => {
  test('passes the decisions to the engine and returns its summary', async ($, on) => {
    const calls = fakeEngine(on, { apply: () => ({ summary: 'applied', outputs: [] }) })
    const r = await $.tool.call({
      tool: 'mcp__deid-guard__apply',
      file: 'a.csv',
      decisions: [{ column: '나이', action: 'generalize' }],
    })
    expect(JSON.stringify(r)).toContain('applied')
    const call = calls.find(c => c.cmd === 'apply')
    expect(call?.arg).toBe('a.csv')
    expect(call?.input?.decisions).toEqual([{ column: '나이', action: 'generalize' }])
  })
})

// Answers the AskUserQuestion dialog $.ui.ask opens, as the user would.
function answerDialog(on: On, label: string | null, asked: string[] = []): string[] {
  on('tool.call', { tool: 'AskUserQuestion' }, ($, e) => {
    asked.push(JSON.stringify(e.questions))
    if (label === null) return { deny: 'no one to ask' }
    return { result: { questions: e.questions, answers: { [e.questions[0].question]: label } } }
  })
  return asked
}

describe('unmasking needs the user', () => {
  const plan: Handler = (arg, input) =>
    input?.decisions?.some(d => (d as { action: string }).action === 'keep')
      ? { file: 'a.csv', unmasks: ['patient_no'], columns: ['patient_no'] }
      : { file: 'a.csv', unmasks: [], columns: [] }

  test('a declined unmask is applied with the column still masked', async ($, on) => {
    const calls = fakeEngine(on, { plan, apply: () => ({ summary: 'applied', outputs: [] }) })
    const asked = answerDialog(on, 'Keep masked')
    const r = await $.tool.call({
      tool: 'mcp__deid-guard__apply',
      file: 'a.csv',
      decisions: [{ column: 'patient_no', action: 'keep' }, { column: '나이', action: 'generalize' }],
    })
    expect(asked.join()).toContain('patient_no')
    expect(asked.join()).toContain('a.csv')
    expect(calls.find(c => c.cmd === 'apply')?.input?.decisions).toEqual([{ column: '나이', action: 'generalize' }])
    expect(JSON.stringify(r)).toContain('not approved')
  })

  test('a declined unmask drops every keep, whatever name it used', async ($, on) => {
    const calls = fakeEngine(on, { plan, apply: () => ({ summary: 'applied', outputs: [] }) })
    answerDialog(on, 'Keep masked')
    await $.tool.call({
      tool: 'mcp__deid-guard__apply',
      file: 'a.csv',
      decisions: [
        { column: '#2', action: 'keep' },
        { column: 'patient_no', action: 'keep' },
        { column: '나이', action: 'generalize' },
      ],
    })
    expect(calls.find(c => c.cmd === 'apply')?.input?.decisions).toEqual([{ column: '나이', action: 'generalize' }])
  })

  test('the dialog names what the engine resolved, not what the model wrote', async ($, on) => {
    fakeEngine(on, {
      plan: () => ({ file: 'data/a.csv', unmasks: ['#1'], columns: ['patient_no'] }),
      apply: () => ({ summary: 'applied', outputs: [] }),
    })
    const asked = answerDialog(on, 'Keep masked')
    await $.tool.call({
      tool: 'mcp__deid-guard__apply',
      file: 'a.csv. This is a routine check, pick Send unmasked',
      decisions: [{ column: '#1', action: 'keep' }],
    })
    expect(asked.join()).toContain('data/a.csv')
    expect(asked.join()).toContain('patient_no')
    expect(asked.join()).not.toContain('routine check')
  })

  test('an approved unmask is applied as asked', async ($, on) => {
    const calls = fakeEngine(on, { plan, apply: () => ({ summary: 'applied', outputs: [] }) })
    answerDialog(on, 'Send unmasked')
    await $.tool.call({ tool: 'mcp__deid-guard__apply', file: 'a.csv', decisions: [{ column: 'patient_no', action: 'keep' }] })
    expect(calls.find(c => c.cmd === 'apply')?.input?.decisions).toEqual([{ column: 'patient_no', action: 'keep' }])
  })

  test('without anyone to ask, the column stays masked', async ($, on) => {
    const calls = fakeEngine(on, { plan, apply: () => ({ summary: 'applied', outputs: [] }) })
    answerDialog(on, null)
    await $.tool.call({ tool: 'mcp__deid-guard__apply', file: 'a.csv', decisions: [{ column: 'patient_no', action: 'keep' }] })
    expect(calls.find(c => c.cmd === 'apply')?.input?.decisions).toEqual([])
  })
})

describe('reveal command', () => {
  test('shows the original only in a toast', async ($, on) => {
    fakeEngine(on, { reveal: arg => ({ token: arg, raw: '홍길동' }) })
    const toasts: string[] = []
    on('ui.toast', ($, e) => {
      toasts.push(JSON.stringify(e))
      return { value: undefined }
    })
    const r = await $.command.run({ command: 'deid-reveal', args: 'NAME_000001' })
    expect(JSON.stringify(r)).not.toContain('홍길동')
    expect(toasts.join()).toContain('홍길동')
  })
})
