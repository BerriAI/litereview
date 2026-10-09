import { Field } from "./ui";

export function MinimumReviewTime({
  value,
  max,
  onChange,
  disabled = false,
}: {
  value: number;
  max: number;
  onChange: (value: number) => void;
  disabled?: boolean;
}) {
  return (
    <Field
      label="Minimum review time (minutes)"
      hint={`0 means no minimum. Up to ${max} whole minutes, starting after setup. Applies whenever this reviewer runs, including in groups. Longer reviews can use more credits.`}
    >
      <input
        type="number"
        min={0}
        max={max}
        step={1}
        required
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        disabled={disabled}
      />
    </Field>
  );
}
