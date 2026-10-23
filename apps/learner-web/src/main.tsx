import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';

import App from './PlayerApp';
import AppErrorBoundary from './AppErrorBoundary';
import 'mana-font/css/mana.css';
import './styles.css';
import './player-app.css';

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <AppErrorBoundary><App /></AppErrorBoundary>
  </StrictMode>,
);
