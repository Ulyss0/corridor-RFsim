import '@mantine/core/styles.css'
import { MantineProvider } from '@mantine/core'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { initialCallsign, signIn } from './api/client'
import App from './App'
import './index.css'

// Reconnect as the last callsign (URL `?as=`, else this browser); a refused one falls back to
// the sign-in screen with the reason.
const callsign = initialCallsign()
if (callsign) signIn(callsign)

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <MantineProvider defaultColorScheme="dark">
      <App />
    </MantineProvider>
  </StrictMode>,
)
