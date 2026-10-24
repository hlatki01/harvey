import { safeHref } from './format'

it('safeHref keeps only http(s) links', () => {
  expect(safeHref('https://ok.example/a?b=1&c=2')).toBe('https://ok.example/a?b=1&c=2')
  for (const bad of ['javascript:alert(1)', ' JaVaScRiPt:x', 'data:text/html,x', '//evil.example', 'vbscript:x'])
    expect(safeHref(bad)).toBe('')
})
