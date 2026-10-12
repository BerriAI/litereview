import { useState } from "react";
import { MCPConnect } from "./mcp-connect";
import { AgentGenerationSettings } from "./agent-generation";
import { GitHubSettings } from "./github-setup";
import "./settings.css";

const sections = [
  { id: "assistant", name: "AI assistants", description: "Connect Claude and other MCP clients" },
  { id: "generation", name: "Agent creation", description: "Choose the model for prompt drafts" },
  { id: "github", name: "GitHub App", description: "Manage credentials and recovery" },
];
export function WorkspaceSettings(props: {
  setup: any; connections: any[]; onModels: () => void; onCreate: () => void;
  onExisting: () => void; onReset: (undo: boolean) => Promise<void>; onRepositories: () => void;
}) {
  const [section, setSection] = useState("assistant");
  const [visited, setVisited] = useState(["assistant"]);
  return <div className="settings-layout">
    <nav className="settings-navigation" aria-label="Settings sections">
      {sections.map(item => <button key={item.id} aria-pressed={section === item.id}
        className={section === item.id ? "selected" : ""} onClick={() => { setSection(item.id); setVisited(v => v.includes(item.id) ? v : [...v, item.id]); }}>
        <span>{item.name}</span><small>{item.description}</small>
      </button>)}
    </nav>
    <div className="settings-content">
      <div hidden={section !== "assistant"}><MCPConnect /></div>
      {visited.includes("generation") && <div hidden={section !== "generation"}><AgentGenerationSettings connections={props.connections} onModels={props.onModels} /></div>}
      {visited.includes("github") && <div hidden={section !== "github"}><GitHubSettings {...props} /></div>}
    </div>
  </div>;
}
