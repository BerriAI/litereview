import { useEffect, useId, useRef, useState } from "react";
import { ChevronDown } from "lucide-react";
import { Toggle } from "./ui";

export function MCPToolSelector({
  name,
  tools = [],
  selected,
  onChange,
}: {
  name: string;
  tools?: { name: string; description?: string }[];
  selected: string[];
  onChange: (tools: string[]) => void;
}) {
  const id = useId();
  const checkbox = useRef<HTMLInputElement>(null);
  const [expanded, setExpanded] = useState(false);
  const count = tools.filter((tool) => selected.includes(tool.name)).length;
  const all = tools.length > 0 && count === tools.length;
  const partial = count > 0 && !all;

  useEffect(() => {
    if (checkbox.current) checkbox.current.indeterminate = partial;
  }, [partial]);

  return (
    <div className="mcp-selector">
      <div className="mcp-selector-head">
        <label className="check mcp-selector-all">
          <input
            ref={checkbox}
            type="checkbox"
            aria-label={`All tools from ${name}`}
            aria-describedby={`${id}-count`}
            checked={all}
            disabled={!tools.length}
            onChange={(event) =>
              onChange(
                event.target.checked
                  ? Array.from(
                      new Set([...selected, ...tools.map((t) => t.name)]),
                    )
                  : [],
              )
            }
          />
          <span>
            <strong>{name}</strong>
            <small id={`${id}-count`}>
              {tools.length
                ? all
                  ? `All ${tools.length} tools selected`
                  : `${count} of ${tools.length} tools selected`
                : "Test this connection in MCP servers to discover its tools."}
            </small>
          </span>
        </label>
        {!!tools.length && (
          <button
            type="button"
            className="icon mcp-selector-expand"
            aria-label={`${expanded ? "Hide" : "Show"} tools from ${name}`}
            aria-expanded={expanded}
            aria-controls={`${id}-tools`}
            title={expanded ? "Hide tools" : "Choose individual tools"}
            onClick={() => setExpanded(!expanded)}
          >
            <ChevronDown size={17} aria-hidden="true" />
          </button>
        )}
      </div>
      <div id={`${id}-tools`} className="mcp-selector-tools" hidden={!expanded}>
        {tools.map((tool) => (
          <Toggle
            key={tool.name}
            checked={selected.includes(tool.name)}
            onChange={(on) =>
              onChange(
                on
                  ? Array.from(new Set([...selected, tool.name]))
                  : selected.filter((name) => name !== tool.name),
              )
            }
          >
            <span>
              {tool.name}
              <small>{tool.description}</small>
            </span>
          </Toggle>
        ))}
      </div>
    </div>
  );
}
