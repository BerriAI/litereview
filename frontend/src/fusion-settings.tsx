import { Field, Toggle } from "./ui";
import { MCPToolSelector } from "./mcp-tool-selector";
import { ReviewRules } from "./review-settings";

export type Model = { connection_id: string; model: string; reasoning_effort?: string | null; inference_speed?: "standard" | "fast" };
export type RoleSettings = {
  instructions?: string | null; skill_ids?: string[] | null; mcps?: { connection_id: string; tools: string[] }[] | null;
  capabilities?: string[] | null; rules?: any[] | null;
};
export type Branch = Model & RoleSettings & {
  name?: string; paths?: string[] | null; condition?: { connection_id: string; model: string; instructions: string; threshold: number } | null;
};
export type Selection = Model & RoleSettings & { fusion?: { branches: Branch[] } | null };
const roleKeys = ["instructions", "skill_ids", "rules", "capabilities", "mcps"] as const;

export function BranchSettings({ branch, defaults, change, skills, mcps }: {
  branch: Branch; defaults: RoleSettings; change: (patch: Partial<Branch>) => void;
  skills: { id: string; name: string; description?: string }[];
  mcps: { id: string; name: string; tools?: { name: string; description?: string }[] }[];
}) {
  const custom = roleKeys.some(key => branch[key] != null);
  const selectedSkills = branch.skill_ids ?? defaults.skill_ids ?? [];
  const selectedMCPs = branch.mcps ?? defaults.mcps ?? [];
  const capabilities = branch.capabilities ?? defaults.capabilities ?? ["terminal"];
  return <details className="fusion-role-settings">
    <summary>Role, skills & tools · {custom ? "Custom settings" : "Use agent settings"}</summary>
    <Field label="Role name"><input value={branch.name || ""} placeholder="e.g. Drift and reuse" onChange={e => change({ name: e.target.value })} /></Field>
    <label className="check"><input type="checkbox" checked={custom} onChange={e => change(e.target.checked ? {
      instructions: defaults.instructions || "", skill_ids: defaults.skill_ids || [], rules: defaults.rules || [],
      capabilities: defaults.capabilities || ["terminal"], mcps: defaults.mcps || [],
    } : { instructions: null, skill_ids: null, rules: null, capabilities: null, mcps: null })} />Use separate settings for this reviewer</label>
    {!custom && <p className="meta">Inherits the agent instructions, skills, rules, and tools below. Its model and run condition are independent.</p>}
    {custom && <>
      <Field label="Reviewer instructions"><textarea required rows={5} value={branch.instructions ?? defaults.instructions ?? ""} onChange={e => change({ instructions: e.target.value })} /></Field>
      <p className="meta">These instructions replace the agent instructions for this reviewer. The final reviewer keeps the agent settings.</p>
      <div className="fusion-role-section"><h4>Skills</h4>
        {!skills.length && <p className="meta">Add reusable skills in Resources → Skills.</p>}
        {skills.map(s => <Toggle key={s.id} checked={selectedSkills.includes(s.id)} onChange={on => change({ skill_ids: on ? [...selectedSkills, s.id] : selectedSkills.filter(id => id !== s.id) })}><span>{s.name}<small>{s.description}</small></span></Toggle>)}
      </div>
      <div className="fusion-role-section"><h4>Rules</h4><ReviewRules rules={branch.rules ?? defaults.rules ?? []} onChange={rules => change({ rules })} /></div>
      <div className="fusion-role-section"><h4>Tools</h4><p className="meta">Code & terminal is always included.</p>
        {[["browser", "Browser"], ["computer", "Computer use"]].map(([key, label]) => <Toggle key={key} checked={capabilities.includes(key)} onChange={on => change({ capabilities: on ? [...capabilities, key] : capabilities.filter(c => c !== key) })}>{label}</Toggle>)}
        <small>Visual tools need a model that supports images.</small>
      </div>
      <div className="fusion-role-section"><h4>MCP tools</h4>
        {!mcps.length && <p className="meta">Connect a server in Resources → MCP servers.</p>}
        {mcps.map(mcp => <MCPToolSelector key={mcp.id} name={mcp.name} tools={mcp.tools}
          selected={selectedMCPs.find(m => m.connection_id === mcp.id)?.tools || []}
          onChange={tools => change({ mcps: [...selectedMCPs.filter(m => m.connection_id !== mcp.id), ...(tools.length ? [{ connection_id: mcp.id, tools }] : [])] })} />)}
      </div>
    </>}
  </details>;
}
