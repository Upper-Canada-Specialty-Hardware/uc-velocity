import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { ClerkProvider } from '@clerk/react'
import { BrowserRouter } from 'react-router-dom'
import './index.css'
import App from './App.tsx'
import { ThemeProvider } from './components/theme-provider'

const clerkPubKey = import.meta.env.VITE_CLERK_PUBLISHABLE_KEY as string

// Self-heal stale tabs after a deploy: hashed chunk filenames change between
// builds, so a tab opened before a deploy references chunk names that 404 on
// the next lazy import ("Failed to fetch dynamically imported module"). Reload
// once to pull the fresh index.html + current chunks. The timestamp guard
// avoids a reload loop if the import keeps failing for a real reason (offline).
const PRELOAD_RELOAD_KEY = 'vite-preload-reloaded-at'
window.addEventListener('vite:preloadError', (event) => {
  const lastReload = Number(sessionStorage.getItem(PRELOAD_RELOAD_KEY) || 0)
  if (Date.now() - lastReload < 10_000) return // already reloaded just now — let the error surface
  event.preventDefault()
  sessionStorage.setItem(PRELOAD_RELOAD_KEY, String(Date.now()))
  window.location.reload()
})

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ClerkProvider publishableKey={clerkPubKey} afterSignOutUrl="/">
      <BrowserRouter>
        <ThemeProvider defaultTheme="system" storageKey="uc-velocity-theme">
          <App />
        </ThemeProvider>
      </BrowserRouter>
    </ClerkProvider>
  </StrictMode>,
)
