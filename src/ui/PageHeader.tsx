import type { ReactNode } from 'react';
import { Icon, type HueId } from '../kit';

/** Title row of a view: icon tile, title, subtitle, actions on the right. */
export function PageHeader({ icon, hue = 'ov', title, subtitle, actions }: { icon: string; hue?: HueId; title: ReactNode; subtitle?: ReactNode; actions?: ReactNode }) {
  return (
    <header className={`ai-ph hue-${hue}`}>
      <span className="ai-ph-ic"><Icon name={icon} /></span>
      <div className="ai-ph-tx">
        <h1>{title}</h1>
        {subtitle && <p>{subtitle}</p>}
      </div>
      {actions && <div className="ai-ph-act">{actions}</div>}
    </header>
  );
}
