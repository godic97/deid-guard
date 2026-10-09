import { describe, expect, test } from 'claude-code/testing'
import {
  dataPathsIn,
  hasToken,
  isDataFile,
  isGeneratedPath,
  textsOf,
  touchesState,
  withTexts,
} from '../hooks/content.ts'

describe('content blocks', () => {
  test('a string content is one text', () => {
    expect(textsOf('hello')).toEqual(['hello'])
    expect(withTexts('hello', ['bye'])).toBe('bye')
  })

  test('text and tool_result blocks are read in order, media skipped', () => {
    const content = [
      { type: 'text', text: 'a' },
      { type: 'image', source: { type: 'base64', media_type: 'image/png', data: 'xx' } },
      { type: 'tool_result', tool_use_id: 't1', content: 'b' },
      { type: 'tool_result', tool_use_id: 't2', content: [{ type: 'text', text: 'c' }] },
    ]
    expect(textsOf(content)).toEqual(['a', 'b', 'c'])
    expect(withTexts(content, ['A', 'B', 'C'])).toEqual([
      { type: 'text', text: 'A' },
      { type: 'image', source: { type: 'base64', media_type: 'image/png', data: 'xx' } },
      { type: 'tool_result', tool_use_id: 't1', content: 'B' },
      { type: 'tool_result', tool_use_id: 't2', content: [{ type: 'text', text: 'C' }] },
    ])
  })
})

describe('paths', () => {
  test('data files by extension', () => {
    expect(isDataFile('a/b/Patients.CSV')).toBe(true)
    expect(isDataFile('x.xlsx')).toBe(true)
    expect(isDataFile('x.py')).toBe(false)
  })

  test('generated copies and cards are not guarded again', () => {
    expect(isGeneratedPath('/p/.deid/out/a.csv')).toBe(true)
    expect(isGeneratedPath('/p/.deid/cards/a.csv.md')).toBe(true)
    expect(isGeneratedPath('/p/data/a.csv')).toBe(false)
  })

  test('state is off limits in paths and commands', () => {
    expect(touchesState('/p/.deid/state/map.sqlite')).toBe(true)
    expect(touchesState('sqlite3 .deid/state/map.sqlite "select *"')).toBe(true)
    expect(touchesState('cat .deid/out/a.csv')).toBe(false)
  })

  test('data paths mentioned in a shell command', () => {
    expect(dataPathsIn(`python -c "import pandas as pd; pd.read_csv('data/p.csv')" && wc -l "my file.tsv" x.json`))
      .toEqual(['data/p.csv', 'my file.tsv', 'x.json'])
  })

  test('pseudonym tokens', () => {
    expect(hasToken("df[df.id == 'PATIENT_NO_000001']")).toBe(true)
    expect(hasToken('ERROR_42')).toBe(false)
  })
})
