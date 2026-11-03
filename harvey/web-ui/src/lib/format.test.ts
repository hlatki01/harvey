import { markdownToWhatsapp, safeHref } from './format'

it('safeHref keeps only http(s) links', () => {
  expect(safeHref('https://ok.example/a?b=1&c=2')).toBe('https://ok.example/a?b=1&c=2')
  for (const bad of ['javascript:alert(1)', ' JaVaScRiPt:x', 'data:text/html,x', '//evil.example', 'vbscript:x'])
    expect(safeHref(bad)).toBe('')
})

it('markdownToWhatsapp converts pasted markdown', () => {
  expect(markdownToWhatsapp('**Oficina:** https://x.me')).toBe('*Oficina:* https://x.me')
  expect(markdownToWhatsapp('## Exemplos\n~~velho~~ __novo__ *já*')).toBe('*Exemplos*\n~velho~ *novo* *já*')
  expect(markdownToWhatsapp('[Peças](https://x.me/pecas)')).toBe('Peças: https://x.me/pecas')
})
