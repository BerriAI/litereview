import React, { useEffect, useId, useRef, useState } from "react";
import {
  ArrowLeft,
  X,
  Terminal,
  Globe,
  Monitor,
  FileCode2,
  Plug,
  Network,
  ChevronDown,
  Check,
  Menu,
  Moon,
  Sun,
  SunMoon,
} from "lucide-react";

export function Brand() {
  return (
    <span className="brand-lockup">
      <img src="/brand/litereview.png" alt="" />
      <span>
        <strong>LiteReview</strong>
        <small>by LiteLLM</small>
      </span>
    </span>
  );
}

const THEMES = ["system", "light", "dark"] as const;
type Theme = (typeof THEMES)[number];

function savedTheme(): Theme {
  try {
    const value = localStorage.getItem("litereview-theme");
    return value === "light" || value === "dark" ? value : "system";
  } catch {
    return "system";
  }
}

export function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>(savedTheme);
  useEffect(() => {
    if (theme === "system") delete document.documentElement.dataset.theme;
    else document.documentElement.dataset.theme = theme;
    try {
      if (theme === "system") localStorage.removeItem("litereview-theme");
      else localStorage.setItem("litereview-theme", theme);
    } catch {}
  }, [theme]);
  const Icon = { system: SunMoon, light: Sun, dark: Moon }[theme];
  const label = { system: "System theme", light: "Light theme", dark: "Dark theme" }[theme];
  return (
    <button
      type="button"
      className="theme-toggle"
      title="Change theme"
      aria-label={`${label}. Change theme`}
      onClick={() => setTheme(THEMES[(THEMES.indexOf(theme) + 1) % THEMES.length])}
    >
      <Icon size={15} aria-hidden="true" /> {label}
    </button>
  );
}

export function ProviderMark({ type }: { type: string }) {
  if (["anthropic", "openai", "github"].includes(type))
    return <img className="provider-mark" src={`/brand/${type}.svg`} alt="" />;
  return type === "gateway" ? (
    <Network size={19} aria-hidden="true" />
  ) : (
    <Plug size={19} aria-hidden="true" />
  );
}

export function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  const id = useId();
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      {React.Children.map(children, (child) =>
        React.isValidElement(child) &&
        ["input", "textarea", "select"].includes(String(child.type))
          ? React.cloneElement(child as React.ReactElement<any>, {
              id,
              "aria-describedby": hint ? id + "-hint" : undefined,
            })
          : child,
      )}
      {hint && <small id={id + "-hint"}>{hint}</small>}
    </div>
  );
}

export function SearchPicker({
  label,
  value,
  options,
  onChange,
  allowCustom = false,
  loading = false,
  disabled = false,
}: {
  label: string;
  value: string;
  options: { value: string; label: string }[];
  onChange: (value: string) => void;
  allowCustom?: boolean;
  loading?: boolean;
  disabled?: boolean;
}) {
  const id = useId();
  const search = useRef<HTMLInputElement>(null);
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const term = query.trim();
  const matches = options.filter((option) =>
    option.label.toLowerCase().includes(term.toLowerCase()),
  );
  const choices =
    allowCustom && term && !options.some((option) => option.value === term)
      ? [...matches, { value: term, label: `Use “${term}”` }]
      : matches;
  const index = Math.min(active, Math.max(0, choices.length - 1));
  const selected = options.find((option) => option.value === value);
  useEffect(() => {
    if (open)
      document
        .getElementById(`${id}-option-${index}`)
        ?.scrollIntoView({ block: "nearest" });
  }, [open, index, id]);
  const openPicker = () => {
    setQuery("");
    setActive(
      Math.max(
        0,
        options.findIndex((option) => option.value === value),
      ),
    );
    setOpen(true);
  };
  const choose = (next: string) => {
    onChange(next);
    setOpen(false);
    search.current?.focus();
  };
  return (
    <div
      className="field search-picker"
      onBlur={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget)) setOpen(false);
      }}
    >
      <label htmlFor={id}>{label}</label>
      <div className="picker-control">
        <input
          id={id}
          ref={search}
          className="picker-trigger"
          disabled={disabled}
          role="combobox"
          aria-expanded={open}
          aria-autocomplete="list"
          aria-controls={open ? id + "-options" : undefined}
          aria-activedescendant={
            open && choices.length ? `${id}-option-${index}` : undefined
          }
          value={open ? query : selected?.label || value}
          placeholder={
            open
              ? allowCustom
                ? "Search models or enter an ID…"
                : `Search ${label.toLowerCase()}…`
              : `Choose ${label.toLowerCase()}`
          }
          autoComplete="off"
          spellCheck={false}
          onFocus={openPicker}
          onClick={() => {
            if (!open) openPicker();
          }}
          onChange={(event) => {
            setQuery(event.target.value);
            setActive(0);
            setOpen(true);
          }}
          onKeyDown={(event) => {
            if (event.key === "Escape" && open) {
              event.preventDefault();
              event.stopPropagation();
              setOpen(false);
            } else if (event.key === "Enter") {
              event.preventDefault();
              if (open) {
                if (choices[index]) choose(choices[index].value);
              } else openPicker();
            } else if (event.key === "ArrowDown" || event.key === "ArrowUp") {
              event.preventDefault();
              if (!open) openPicker();
              else
                setActive(
                  Math.max(
                    0,
                    Math.min(
                      choices.length - 1,
                      index + (event.key === "ArrowDown" ? 1 : -1),
                    ),
                  ),
                );
            }
          }}
        />
        <ChevronDown size={15} aria-hidden="true" />
      </div>
      {open && (
        <div className="picker-popover">
          <div
            id={id + "-options"}
            role="listbox"
            aria-label={label}
            className="picker-options"
          >
            {choices.map((option, i) => (
              <button
                type="button"
                role="option"
                id={`${id}-option-${i}`}
                key={option.value}
                tabIndex={-1}
                aria-selected={option.value === value}
                className={
                  i === index ? "picker-option active" : "picker-option"
                }
                onMouseDown={(event) => event.preventDefault()}
                onMouseMove={() => setActive(i)}
                onClick={() => choose(option.value)}
              >
                <span>{option.label}</span>
                {option.value === value && (
                  <Check size={14} aria-hidden="true" />
                )}
              </button>
            ))}
          </div>
          {!choices.length && (
            <p className="picker-empty" role="status">
              {loading
                ? "Loading models…"
                : term
                  ? "No matches."
                  : allowCustom
                    ? "Enter a model ID to use it."
                    : "No connections available."}
            </p>
          )}
        </div>
      )}
    </div>
  );
}

export function Badge({
  children,
  tone = "",
}: {
  children: React.ReactNode;
  tone?: string;
}) {
  return (
    <span className={`badge ${tone}`}>
      {tone && <span className="status-dot" />}
      {children}
    </span>
  );
}

export function Toggle({
  checked,
  onChange,
  children,
}: {
  checked: boolean;
  onChange: (value: boolean) => void;
  children: React.ReactNode;
}) {
  return (
    <label className="check">
      <input
        type="checkbox"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
      />
      {children}
    </label>
  );
}

export function FormSection({
  title,
  description,
  children,
}: {
  title: string;
  description: string;
  children: React.ReactNode;
}) {
  return (
    <section className="form-section">
      <div className="section-label">
        <h3>{title}</h3>
        <p>{description}</p>
      </div>
      <div className="section-fields">{children}</div>
    </section>
  );
}

export function Capabilities({ values }: { values: string[] }) {
  const modes = Array.from(new Set(["terminal", ...values]));
  const labels: Record<string, string> = {
    terminal: "Terminal",
    browser: "Browser",
    computer: "Computer",
  };
  const icons = {
    terminal: Terminal,
    browser: Globe,
    computer: Monitor,
  };
  return (
    <div className="capability-list">
      {modes.map((value) => {
        const Icon = icons[value as keyof typeof icons];
        return (
          <span key={value} title={labels[value]}>
            {Icon && <Icon size={14} />}
            {labels[value]}
          </span>
        );
      })}
    </div>
  );
}

export function Modal({
  title,
  close,
  children,
  page = false,
  description,
  openNavigation,
  navigationOpen = false,
}: {
  title: string;
  close: () => void;
  children: React.ReactNode;
  page?: boolean;
  description?: string;
  openNavigation?: () => void;
  navigationOpen?: boolean;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  const navigable = page && !!openNavigation;
  useEffect(() => {
    const dialog = ref.current;
    if (navigable) dialog?.show();
    else dialog?.showModal();
    return () => dialog?.close();
  }, [navigable]);
  return (
    <dialog
      className={page ? "page-editor" : "compact-editor"}
      ref={ref}
      inert={page && navigationOpen}
      onKeyDown={(e) => {
        if (navigable && e.key === "Escape" && !e.defaultPrevented) {
          e.preventDefault();
          close();
        }
      }}
      onCancel={(e) => {
        e.preventDefault();
        close();
      }}
      aria-labelledby={titleId}
    >
      <div className="dialog-head">
        {page && openNavigation && (
          <button
            type="button"
            className="icon mobile-menu"
            aria-label="Open navigation"
            onClick={openNavigation}
          >
            <Menu size={18} />
          </button>
        )}
        {page && (
          <button
            type="button"
            className="icon"
            aria-label="Close editor"
            onClick={close}
          >
            <ArrowLeft size={19} />
          </button>
        )}
        <div>
          <h2 id={titleId}>{title}</h2>
          {description && <p>{description}</p>}
        </div>
        {!page && (
          <button
            type="button"
            className="icon"
            aria-label="Close editor"
            onClick={close}
          >
            <X size={19} />
          </button>
        )}
      </div>
      {children}
    </dialog>
  );
}
