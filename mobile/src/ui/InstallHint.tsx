// iOS ne propose jamais l'installation d'une web app : on explique le geste « Sur l'écran d'accueil ».
import { useState } from 'react';
import { Blueprint, Icon, ICONS } from './kit';

const KEY = 'projectflow.installHint.dismissed';

export function isIos(): boolean {
  const ua = navigator.userAgent;
  return /iPad|iPhone|iPod/.test(ua) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
}

export function isStandalone(): boolean {
  return (navigator as Navigator & { standalone?: boolean }).standalone === true || window.matchMedia('(display-mode: standalone)').matches;
}

function ShareIcon() {
  return (
    <Icon size={16} style={{ display: 'inline', verticalAlign: '-3px' }}>
      <path d="M4 12v8a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-8M16 6l-4-4-4 4M12 2v13" />
    </Icon>
  );
}

export function InstallHint() {
  const [dismissed, setDismissed] = useState(() => {
    try {
      return localStorage.getItem(KEY) === '1';
    } catch {
      return false;
    }
  });
  if (dismissed || !isIos() || isStandalone()) return null;
  const inSafari = !/CriOS|FxiOS|EdgiOS/.test(navigator.userAgent);
  return (
    <Blueprint className="pf-card" style={{ marginBottom: 0, background: 'var(--color-accent-100)' }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, marginBottom: 6 }}>
        <span className="pf-title" style={{ flex: 1 }}>Installer sur l’iPhone</span>
        <button
          type="button"
          className="btn btn-ghost btn-icon"
          style={{ width: 36, height: 36 }}
          aria-label="Masquer"
          onClick={() => {
            setDismissed(true);
            try {
              localStorage.setItem(KEY, '1');
            } catch {
              // préférence non mémorisée
            }
          }}
        >
          <Icon d={ICONS.close} size={18} />
        </button>
      </div>
      <ol style={{ margin: 0, paddingLeft: 18, fontSize: 14, lineHeight: 1.6 }}>
        {!inSafari && <li>Ouvrez cette page dans <b>Safari</b>.</li>}
        <li>Touchez <b>Partager</b> <ShareIcon /> en bas de l’écran.</li>
        <li>Choisissez <b>Sur l’écran d’accueil</b>, puis <b>Ajouter</b>.</li>
        <li>Ouvrez ProjectFlow depuis son icône et connectez-vous.</li>
      </ol>
    </Blueprint>
  );
}
