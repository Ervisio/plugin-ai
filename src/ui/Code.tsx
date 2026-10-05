import { useState } from 'react';
import { t } from '../i18n';
import { copyText } from './copy';
import { Button, toast } from '../kit';

/** A block of code or configuration with a Copy button. `secret` blurs it until it is hovered. */
export function Code({ text, title, secret, wrap }: { text: string; title?: string; secret?: boolean; wrap?: boolean }) {
  const [done, setDone] = useState(false);
  const copy = async () => {
    if (await copyText(text)) {
      setDone(true);
      setTimeout(() => setDone(false), 1600);
    } else toast.err(t('common.copyFailed'));
  };
  return (
    <div>
      <div className="ai-code-h">
        <b>{title}</b>
        <Button size="sm" variant="ghost" icon={done ? 'check' : 'copy'} onClick={copy}>{done ? t('common.copied') : t('common.copy')}</Button>
      </div>
      <div className={`ai-code${wrap ? ' ai-code--wrap' : ''}${secret ? ' ai-secret' : ''}`}>{text}</div>
    </div>
  );
}
