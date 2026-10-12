import { useId, useRef, useState } from "react";
import { Check, ChevronDown, Plus, Trash2 } from "lucide-react";
import { Field } from "./ui";
import "./review-settings.css";

const scopes = [
  { value: "full", name: "Full PR", description: "Review every change in the PR each time.", detail: "Best for a complete assessment before merging. This is the default." },
  { value: "incremental", name: "New changes", description: "Review changes since this agent’s last successful review.", detail: "The first review is full. Later reviews focus on new commits and their effects, with earlier findings as context. Falls back to a full review when the base or agent configuration changes, history is rewritten, or no successful baseline exists." },
  { value: "compare", name: "Compare both", description: "Run New changes and a separate Full PR review.", detail: "Posts the incremental result to GitHub. The full result stays in Runs for comparison. Both runs use model and sandbox credits. The first review runs once, in full." },
];

export function ReviewScope({ value, onChange }: { value: string; onChange: (value: string) => void }) {
  const id = useId();
  const button = useRef<HTMLButtonElement>(null);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const selected = scopes.find(s => s.value === value) || scopes[0];
  const show = () => { setActive(scopes.indexOf(selected)); setOpen(true); };
  const choose = (index: number) => { onChange(scopes[index].value); setOpen(false); button.current?.focus(); };
  return <div className="field review-scope" onBlur={event => { if (!event.currentTarget.contains(event.relatedTarget)) setOpen(false); }}>
    <label id={id + "-label"}>Review scope</label>
    <button type="button" ref={button} role="combobox" aria-labelledby={id + "-label"} aria-expanded={open}
      aria-controls={open ? id + "-options" : undefined} aria-describedby={id + "-detail"}
      aria-activedescendant={open ? `${id}-${active}` : undefined} className="scope-trigger"
      onClick={() => open ? setOpen(false) : show()}
      onKeyDown={event => {
        if (["ArrowDown", "ArrowUp", "Home", "End", "Escape", "Enter", " "].includes(event.key)) {
          event.preventDefault(); event.stopPropagation();
          if (event.key === "Escape") setOpen(false);
          else if (!open) show();
          else if (event.key === "Enter" || event.key === " ") choose(active);
          else setActive(event.key === "Home" ? 0 : event.key === "End" ? 2 : (active + (event.key === "ArrowDown" ? 1 : 2)) % 3);
        }
      }}>
      <span><strong>{selected.name}</strong><small>{selected.description}</small></span><ChevronDown size={16} />
    </button>
    {open && <div role="listbox" id={id + "-options"} aria-labelledby={id + "-label"} className="scope-options">
      {scopes.map((scope, index) => <button type="button" role="option" id={`${id}-${index}`} key={scope.value}
        aria-selected={scope.value === selected.value} tabIndex={-1} className={index === active ? "active" : ""}
        onMouseDown={event => event.preventDefault()} onMouseMove={() => setActive(index)} onClick={() => choose(index)}>
        <span><strong>{scope.name}</strong><small>{scope.description}</small></span>{scope.value === selected.value && <Check size={16} />}
      </button>)}
    </div>}
    <small id={id + "-detail"}>{selected.detail}</small>
  </div>;
}

type Rule = { id: string; name: string; instructions: string; enabled: boolean; required: boolean };
export function ReviewRules({ rules, onChange }: { rules: Rule[]; onChange: (rules: Rule[]) => void }) {
  const change = (index: number, patch: Partial<Rule>) => onChange(rules.map((r, i) => i === index ? { ...r, ...patch } : r));
  return <div className="review-rules">
    {!rules.length && <p className="meta">Add requirements such as real end-to-end proof or breaking-change guidance. Keep the base skill attached below.</p>}
    {rules.map((rule, index) => <div className="review-rule" key={rule.id}>
      <div className="rule-toolbar"><label className="check"><input type="checkbox" checked={rule.enabled} onChange={e => change(index, { enabled: e.target.checked })} />Enabled</label>
        <button type="button" className="icon-button" aria-label={`Delete rule ${rule.name || index + 1}`} onClick={() => onChange(rules.filter((_, i) => i !== index))}><Trash2 size={15} /></button></div>
      <Field label="Rule name"><input required value={rule.name} onChange={e => change(index, { name: e.target.value })} placeholder="Require end-to-end proof" /></Field>
      <Field label="Rule instructions"><textarea required rows={3} value={rule.instructions} onChange={e => change(index, { instructions: e.target.value })} placeholder="Describe what this reviewer must verify and what counts as evidence." /></Field>
      <label className="check"><input type="checkbox" checked={rule.required} onChange={e => change(index, { required: e.target.checked })} />Required verification</label>
      <small>A skipped required rule makes the review incomplete. Every enabled rule appears as a check in the result.</small>
    </div>)}
    <button type="button" className="secondary" onClick={() => onChange([...rules, { id: crypto.randomUUID(), name: "", instructions: "", enabled: true, required: true }])}><Plus size={15} /> Add rule</button>
    <small>Save the agent to apply rule changes to future reviews.</small>
  </div>;
}
