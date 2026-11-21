import './StatusBadge.scss';
import { statusView } from './query';

type Props = {
  status: string;
  queue: string | null;
  label: string;
};

export const StatusBadge = ({ status, queue, label }: Props) => {
  const view = statusView(status, queue);
  return (
    <span className={`adm-tag adm-tag--${view.tone}`}>
      <span className="material-icons" aria-hidden>
        {view.icon}
      </span>
      {label}
    </span>
  );
};
