import MarkdownIt from 'markdown-it'

// Model output is untrusted. Raw HTML stays escaped; unsafe link protocols
// are rejected by markdown-it's default validator.
const markdown = new MarkdownIt({ html: false, breaks: true, linkify: false })
const originalLink = markdown.renderer.rules.link_open
markdown.renderer.rules.link_open = (tokens, index, options, env, self) => {
  tokens[index].attrSet('target', '_blank')
  tokens[index].attrSet('rel', 'noopener noreferrer')
  return originalLink ? originalLink(tokens, index, options, env, self) : self.renderToken(tokens, index, options)
}
const cache = new Map<string, string>()
export function renderMarkdown(source: string): string {
  const cached = cache.get(source)
  if (cached !== undefined) return cached
  const html = markdown.render(source)
  if (cache.size >= 128) cache.delete(cache.keys().next().value!)
  cache.set(source, html)
  return html
}
