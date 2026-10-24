import fs from 'node:fs'
import path from 'node:path'

const root = path.resolve(__dirname)
function walk(dir: string): string[] {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap(e => {
    const p = path.join(dir, e.name)
    return e.isDirectory() ? walk(p) : [p]
  })
}
const files = walk(root).filter(f => /\.(ts|tsx)$/.test(f) && !/\.test\.tsx?$/.test(f) && !f.includes(path.sep + 'test' + path.sep))
const src = files.map(f => ({ f: path.relative(root, f).replaceAll('\\', '/'), text: fs.readFileSync(f, 'utf8') }))

it('only lib/auth.ts calls global fetch', () => {
  const offenders = src.filter(s => /(?<![\w.])fetch\(/.test(s.text)).map(s => s.f)
  expect(offenders).toEqual(['lib/auth.ts'])
})
it('no dangerouslySetInnerHTML', () => {
  expect(src.filter(s => s.text.includes('dangerouslySetInnerHTML')).map(s => s.f)).toEqual([])
})
it('token is never written to localStorage', () => {
  expect(src.filter(s => /localStorage\.setItem\(\s*['"`]harvey-dashboard/.test(s.text)).map(s => s.f)).toEqual([])
})
it('main.tsx imports lib/boot before anything else', () => {
  const imports = fs.readFileSync(path.join(root, 'main.tsx'), 'utf8').match(/^import .*$/gm)!
  expect(imports[0]).toMatch(/^import '@\/lib\/boot'/)
})
it('no href is built from /api/ strings; prospects CSV goes through downloadFile', () => {
  expect(src.filter(s => /href=\{?[^>]*['"`]\/api\//.test(s.text)).map(s => s.f)).toEqual([])
  const pages = src.filter(s => s.f.startsWith('pages/'))
  expect(pages.some(s => s.text.includes('path="/api/export/prospects.csv"'))).toBe(true) // <CsvButton path=...>
  expect(pages.some(s => /downloadFile\(path,/.test(s.text))).toBe(true) // CsvButton -> downloadFile
})
