export interface ShareInput {
  url: string
  title: string
  text?: string
}

export interface ShareTarget {
  id: 'whatsapp' | 'telegram' | 'x' | 'facebook' | 'email'
  href: string
}

/** Plain share links: no scripts, no trackers, only the page address and the title. */
export function shareTargets({ url, title, text = '' }: ShareInput): ShareTarget[] {
  const enc = encodeURIComponent
  const headline = [title, text].filter(Boolean).join(' ')
  return [
    { id: 'whatsapp', href: `https://wa.me/?text=${enc([headline, url].filter(Boolean).join(' '))}` },
    { id: 'telegram', href: `https://t.me/share/url?url=${enc(url)}&text=${enc(headline)}` },
    { id: 'x', href: `https://x.com/intent/post?url=${enc(url)}&text=${enc(headline)}` },
    { id: 'facebook', href: `https://www.facebook.com/sharer/sharer.php?u=${enc(url)}` },
    { id: 'email', href: `mailto:?subject=${enc(title)}&body=${enc([text, url].filter(Boolean).join(' '))}` },
  ]
}
