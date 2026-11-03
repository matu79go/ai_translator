import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import ExperimentalApp from './ExperimentalApp.tsx'
import MeetingApp from './MeetingApp.tsx'

// Previous standard App is disabled - kept for reference
// import App from './App.tsx'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    {/* #meeting: online meeting assist on GX10 (docs/designs/meeting-assist-local.md) */}
    {window.location.hash === '#meeting' ? <MeetingApp /> : <ExperimentalApp />}
  </StrictMode>,
)
