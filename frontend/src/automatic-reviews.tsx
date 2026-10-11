import { Plus, Trash2 } from "lucide-react";
import { Field, FormSection, Toggle } from "./ui";
import "./automatic-reviews.css";

export const reviewEvents: Record<string, string> = {
  opened: "PR opened",
  ready_for_review: "Ready for review",
  synchronize: "New commits pushed",
  reopened: "PR reopened",
  labeled: "Label added",
};
type Rule = {
  id: string;
  target_id: string;
  enabled: boolean;
  events: string[];
  authors: string[];
  label: string;
};
type Target = { id: string; name: string; members?: unknown[] };

export function AutomaticReviews({ rules, targets, allowedTargetIds, eventsEnabled, onChange }: {
  rules: Rule[]; targets: Target[]; allowedTargetIds: string[]; eventsEnabled?: boolean; onChange: (rules: Rule[]) => void;
}) {
  const update = (index: number, changes: Partial<Rule>) =>
    onChange(rules.map((rule, i) => i === index ? { ...rule, ...changes } : rule));
  return <FormSection title="Automatic reviews" description="Choose who reviews a PR and when. Rules apply to this repository.">
    <p className="meta">Enabled rules start paid reviews and post results on GitHub. They can review any PR author, including bots and external contributors, without requiring that author to sign in to LiteReview. Repository review permissions still apply.</p>
    {eventsEnabled === false && <div className="info" role="status">Pull request events are not enabled on your GitHub App. Enable them in GitHub → App settings → Permissions &amp; events, then return to Repositories to check again.</div>}
    {!rules.length && <p className="meta">No automatic reviews. You can still call reviewers with <code>@litereview</code>.</p>}
    {rules.map((rule, index) => <fieldset className="review-rule" key={rule.id}>
      <legend>Rule {index + 1}</legend>
      <div className="review-rule-heading">
        <Toggle checked={rule.enabled} onChange={enabled => update(index, { enabled })}>Enabled</Toggle>
        <button type="button" className="icon" aria-label={`Remove rule ${index + 1}`} onClick={() => onChange(rules.filter((_, i) => i !== index))}><Trash2 size={15} /></button>
      </div>
      <Field label="Reviewer"><select required value={rule.target_id} onChange={e => update(index, { target_id: e.target.value })}>
        <option value="">Choose an agent or group</option>
        {targets.map(t => <option key={t.id} value={t.id} disabled={!allowedTargetIds.includes(t.id)}>{t.name}{t.members ? " (group)" : ""}{!allowedTargetIds.includes(t.id) ? " — not permitted" : ""}</option>)}
      </select></Field>
      {rule.target_id && !allowedTargetIds.includes(rule.target_id) && <p className="meta">This reviewer is blocked by this repository’s Review permissions. Allow it there before this rule can run.</p>}
      <Field label="PR authors" hint="Leave blank for everyone. Enter one or more GitHub usernames, separated by commas. Use the author’s account, such as dependabot[bot], rather than the person pushing commits.">
        <input defaultValue={rule.authors.join(", ")} placeholder="Everyone" onChange={e => update(index, { authors: e.target.value.split(/[\s,]+/).filter(Boolean) })} />
      </Field>
      <fieldset className="review-rule-events"><legend>Run when</legend>
        {Object.entries(reviewEvents).map(([event, label]) => <Toggle key={event} checked={rule.events.includes(event)} onChange={on => update(index, { events: on ? [...rule.events, event] : rule.events.filter(v => v !== event) })}>{label}</Toggle>)}
      </fieldset>
      {rule.events.includes("labeled") && <Field label="Trigger label"><input required value={rule.label} placeholder="review-requested" onChange={e => update(index, { label: e.target.value })} /></Field>}
    </fieldset>)}
    <button type="button" className="quiet-action" onClick={() => onChange([...rules, { id: crypto.randomUUID(), target_id: "", enabled: true, events: ["opened", "ready_for_review"], authors: [], label: "" }])}><Plus size={14} /> Add rule</button>
    <p className="meta">Draft and closed PRs are skipped. Each agent instance runs automatically once per commit, even when rules overlap. Use <code>@litereview</code> to request another review of the same commit. Changes take effect after saving.</p>
    <p className="meta">Your GitHub App must subscribe to <strong>Pull request</strong> events. Existing Apps can enable this in GitHub → App settings → Permissions &amp; events. Keep <strong>Issue comment</strong> enabled for mentions.</p>
  </FormSection>;
}
