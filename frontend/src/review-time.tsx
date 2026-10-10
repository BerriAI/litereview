import { Field } from "./ui";
import { useState } from "react";

export function OptionalReviewLimits({
  maximumMinutes,
  maxModelCalls,
  minimumMinutes,
  onChange,
}: {
  maximumMinutes?: number | null;
  maxModelCalls?: number | null;
  minimumMinutes: number;
  onChange: (field: string, value: number | null) => void;
}) {
  const [open, setOpen] = useState(!!(maximumMinutes || maxModelCalls));
  const selected = [
    maximumMinutes && `${maximumMinutes} min maximum`,
    maxModelCalls && `${maxModelCalls} model calls`,
  ].filter(Boolean);
  return (
    <details
      className="review-limits"
      open={open}
      onToggle={(e) => setOpen(e.currentTarget.open)}
    >
      <summary>
        Optional limits · {selected.length ? selected.join(" · ") : "None set"}
      </summary>
      <p className="review-limits-note">
        Leave blank to let the agent work until it finishes. Minimum time does
        not set a maximum. Modal allows each sandbox to run for up to 24 hours.
      </p>
      <div className="form-grid">
        <Field
          label="Maximum run time (minutes)"
          hint="Includes setup. Reaching this limit marks the review incomplete."
        >
          <input
            type="number"
            min={Math.max(1, minimumMinutes + 1)}
            step={1}
            value={maximumMinutes ?? ""}
            placeholder="No limit set"
            onChange={(e) =>
              onChange(
                "maximum_minutes",
                e.target.value === "" ? null : Number(e.target.value),
              )
            }
          />
        </Field>
        <Field
          label="Maximum model calls"
          hint="Counts requests across the entire run, including session continuations."
        >
          <input
            type="number"
            min={1}
            step={1}
            value={maxModelCalls ?? ""}
            placeholder="No limit set"
            onChange={(e) =>
              onChange(
                "max_model_calls",
                e.target.value === "" ? null : Number(e.target.value),
              )
            }
          />
        </Field>
      </div>
    </details>
  );
}

export function MinimumReviewTime({
  value,
  onChange,
  disabled = false,
}: {
  value: number;
  onChange: (value: number) => void;
  disabled?: boolean;
}) {
  return (
    <Field
      label="Minimum review time (minutes)"
      hint="0 means no minimum. Enter whole minutes, starting after setup. Applies whenever this reviewer runs, including in groups."
    >
      <input
        type="number"
        min={0}
        step={1}
        required
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        disabled={disabled}
      />
    </Field>
  );
}
