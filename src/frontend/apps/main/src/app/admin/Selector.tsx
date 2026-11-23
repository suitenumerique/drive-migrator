'use client';

import { useEffect, useId, useRef, useState } from 'react';

import './Selector.scss';

export type SelectorOption = {
  value: string;
  label: string;
};

type Props = {
  id: string;
  title: string;
  icon?: string;
  value: string | null;
  options: SelectorOption[];
  onChange: (value: string | null) => void;
  allowEmpty?: boolean;
};

export const Selector = ({
  id,
  title,
  icon,
  value,
  options,
  onChange,
  allowEmpty = false,
}: Props) => {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const listId = useId();
  const current = options.find((option) => option.value === value);

  useEffect(() => {
    if (!open) {
      return;
    }
    const onPointer = (event: MouseEvent) => {
      if (!root.current?.contains(event.target as Node)) {
        setOpen(false);
      }
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setOpen(false);
      }
    };
    document.addEventListener('mousedown', onPointer);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onPointer);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  const choose = (next: string) => {
    onChange(allowEmpty && next === value ? null : next);
    setOpen(false);
  };

  return (
    <div className="adm-selector" ref={root}>
      <button
        id={id}
        type="button"
        className="adm-selector__button"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={listId}
        onClick={() => setOpen((currentOpen) => !currentOpen)}
      >
        {icon && (
          <span className="material-icons" aria-hidden>
            {icon}
          </span>
        )}
        <span className="adm-selector__title">{title}</span>
        {current && (
          <strong className="adm-selector__value">{current.label}</strong>
        )}
        <span className="material-icons" aria-hidden>
          expand_more
        </span>
      </button>
      {open && (
        <ul
          id={listId}
          role="listbox"
          aria-labelledby={id}
          className="adm-selector__menu"
        >
          {options.map((option) => (
            <li key={option.value} role="presentation">
              <button
                type="button"
                role="option"
                aria-selected={option.value === value}
                className="adm-selector__option"
                onClick={() => choose(option.value)}
              >
                <span className="material-icons" aria-hidden>
                  {option.value === value ? 'check' : ''}
                </span>
                {option.label}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
};
