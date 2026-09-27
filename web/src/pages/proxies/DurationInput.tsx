import { Input, Select } from "../../components/ui/form";
import { DURATION_UNITS, DURATION_UNIT_LABELS, type DurationUnit, type DurationValue } from "../../lib/validation";

interface DurationInputProps {
  id: string;
  value: DurationValue;
  onChange: (value: DurationValue) => void;
  invalid?: boolean;
  disabled?: boolean;
}

export function DurationInput({ id, value, onChange, invalid = false, disabled = false }: DurationInputProps) {
  return (
    <div className="flex">
      <Input
        id={id}
        inputMode="numeric"
        autoComplete="off"
        value={value.amount}
        disabled={disabled}
        aria-invalid={invalid}
        onChange={(event) => {
          onChange({ ...value, amount: event.target.value });
        }}
        className={invalid ? "rounded-r-none ring-rose-400" : "rounded-r-none"}
      />
      <Select
        aria-label="Đơn vị thời gian"
        value={value.unit}
        disabled={disabled}
        onChange={(event) => {
          onChange({ ...value, unit: event.target.value as DurationUnit });
        }}
        className="-ml-px w-24 shrink-0 rounded-l-none"
      >
        {DURATION_UNITS.map((unit) => (
          <option key={unit} value={unit}>
            {DURATION_UNIT_LABELS[unit]}
          </option>
        ))}
      </Select>
    </div>
  );
}
