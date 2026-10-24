import { api } from '../api.js'
import { h, field, busy, notice } from '../util.js'

export function render(root, route, app) {
  app.setTitle('Sign in')
  const err = h('div', { 'aria-live': 'assertive' })
  const pw = h('input', { class: 'input', type: 'password', name: 'password', autocomplete: 'current-password', required: true, autofocus: true })
  const btn = h('button', { class: 'btn primary lg', type: 'submit', style: 'width:100%' }, 'Sign in')
  const form = h('form', { onsubmit: async (e) => {
    e.preventDefault()
    err.replaceChildren()
    busy(btn, true, 'Signing in')
    try {
      await api.post('/auth/login', { password: pw.value }, { quiet401: true })
      let back = '#/'
      try { back = sessionStorage.getItem('quill.returnTo') || '#/'; sessionStorage.removeItem('quill.returnTo') } catch { /* private mode */ }
      location.hash = back.startsWith('#/signin') ? '#/' : back
    } catch (ex) {
      err.replaceChildren(notice('error', ex.status === 401 ? 'That password is not correct.' : ex.message))
      pw.select()
    } finally { busy(btn, false) }
  } },
  field('Password', pw, 'The admin password you set with the "Set Admin Password" action in StartOS.'),
  err, btn)

  root.append(h('div', { class: 'signin' }, h('div', { class: 'card' },
    h('div', { class: 'brand' }, h('img', { src: 'favicon.svg', alt: '', width: 30, height: 30 }), 'Quill'),
    h('p', { class: 'muted', style: 'margin-bottom:18px' }, 'Self-hosted meeting notes. Sign in to continue.'),
    form)))

  // Already signed in (valid cookie)? skip the form.
  let alive = true
  api.get('/meetings', { query: { limit: 1 }, quiet401: true }).then(() => { if (alive) location.hash = '#/' }).catch(() => {})
  return () => { alive = false }
}
