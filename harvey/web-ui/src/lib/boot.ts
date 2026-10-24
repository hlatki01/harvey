// Imported first by main.tsx: ES modules evaluate in import order, so the
// token is out of the address bar before any other module runs.
import { takeFragmentToken } from './auth'
takeFragmentToken()

// Pasting a fresh link into a tab that already has the dashboard open only
// changes the #fragment: no page load, so nothing above would run again and the
// new token would never be taken. Take it, then reload so the app starts clean.
window.addEventListener('hashchange', () => {
  if (!window.location.hash) return
  takeFragmentToken()
  window.location.reload()
})
