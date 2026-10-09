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
