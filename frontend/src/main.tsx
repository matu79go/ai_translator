import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import ExperimentalApp from './ExperimentalApp.tsx'

// Previous standard App is disabled - kept for reference
// import App from './App.tsx'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ExperimentalApp />
  </StrictMode>,
)
