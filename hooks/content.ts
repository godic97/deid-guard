// Pure helpers for deid-guard's hooks: reading and rewriting message content,
// and recognising the paths the plugin guards.

export type Block = { type: string; [key: string]: unknown }
export type Content = string | readonly Block[]

const DATA_EXT = /\.(csv|tsv|xlsx|jsonl?)$/i
const GENERATED = /(^|[\\/])\.deid[\\/]+(out|cards)[\\/]/
const STATE = /\.deid(?![\\/]+(out|cards)\b)|map\.sqlite/i
const TOKEN = /\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*_\d{6}\b/
const PATH_IN_COMMAND =
  /(['"])([^'"\n]*?\.(?:csv|tsv|xlsx|jsonl?))\1|([^\s'"()=;&|<>,]+\.(?:csv|tsv|xlsx|jsonl?))(?![\w.])/gi

export const isDataFile = (path: string): boolean => DATA_EXT.test(path)

export const isGeneratedPath = (path: string): boolean => GENERATED.test(path)

export const touchesState = (text: string): boolean => STATE.test(text)

export const hasToken = (text: string): boolean => TOKEN.test(text)

export function dataPathsIn(command: string): string[] {
  const found: string[] = []
  for (const m of command.matchAll(PATH_IN_COMMAND)) {
    const path = m[2] ?? m[3]
    if (path && !found.includes(path)) found.push(path)
  }
  return found
}

function resultTexts(content: unknown): string[] {
  if (typeof content === 'string') return [content]
  if (!Array.isArray(content)) return []
  return content.filter(b => b?.type === 'text').map(b => String(b.text))
}

/** Every text the model reads in a row's content, in order. */
export function textsOf(content: Content): string[] {
  if (typeof content === 'string') return [content]
  const out: string[] = []
  for (const b of content) {
    if (b.type === 'text') out.push(String(b.text))
    else if (b.type === 'tool_result') out.push(...resultTexts(b.content))
  }
  return out
}

/** The same content with its texts replaced, in the order textsOf read them. */
export function withTexts(content: Content, texts: readonly string[]): Content {
  let i = 0
  const next = () => texts[i++]
  if (typeof content === 'string') return next()
  return content.map(b => {
    if (b.type === 'text') return { ...b, text: next() }
    if (b.type !== 'tool_result') return b
    if (typeof b.content === 'string') return { ...b, content: next() }
    if (!Array.isArray(b.content)) return b
    return { ...b, content: b.content.map(x => (x?.type === 'text' ? { ...x, text: next() } : x)) }
  })
}
