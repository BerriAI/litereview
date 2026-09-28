import React, { useEffect, useId, useRef } from "react";
import {
  ArrowLeft,
  X,
  Terminal,
  Globe,
  Monitor,
  FileCode2,
  Plug,
  Network,
} from "lucide-react";

export function Brand() {
  return (
    <span className="brand-lockup">
      <img src="/brand/litellm-train.webp" alt="" />
      <span>
        <strong>LiteLLM</strong>
        <small>LiteReview</small>
      </span>
    </span>
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
  const modes = values.length ? values : ["repository"];
  const labels: Record<string, string> = {
    repository: "Code only",
    terminal: "Terminal",
    browser: "Browser",
    computer: "Computer",
  };
  const icons = {
    repository: FileCode2,
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
}: {
  title: string;
  close: () => void;
  children: React.ReactNode;
  page?: boolean;
  description?: string;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  useEffect(() => {
    const dialog = ref.current;
    dialog?.showModal();
    return () => dialog?.close();
  }, []);
  return (
    <dialog
      className={page ? "page-editor" : "compact-editor"}
      ref={ref}
      onCancel={(e) => {
        e.preventDefault();
        close();
      }}
      aria-labelledby={titleId}
    >
      <div className="dialog-head">
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
