import type { ComponentProps, ReactNode } from "react";

import { cn } from "../../lib/cn";

const CONTROL =
  "block w-full rounded-lg border-0 bg-white text-sm text-slate-900 shadow-sm ring-1 ring-inset ring-slate-300 placeholder:text-slate-400 focus:ring-2 focus:ring-inset focus:ring-indigo-600 focus:outline-none disabled:cursor-not-allowed disabled:bg-slate-50 disabled:text-slate-500";

interface FieldProps {
  label: ReactNode;
  htmlFor?: string;
  hint?: ReactNode;
  error?: ReactNode;
  className?: string;
  children: ReactNode;
}

export function Field({ label, htmlFor, hint, error, className, children }: FieldProps) {
  return (
    <div className={cn("space-y-1.5", className)}>
      <label htmlFor={htmlFor} className="block text-sm font-medium text-slate-700">
        {label}
      </label>
      {children}
      {error ? (
        <p className="text-xs text-rose-600">{error}</p>
      ) : hint ? (
        <p className="text-xs text-slate-500">{hint}</p>
      ) : null}
    </div>
  );
}

export function Input({ className, ...rest }: ComponentProps<"input">) {
  return <input className={cn(CONTROL, "h-9 px-3", className)} {...rest} />;
}

export function Select({ className, children, ...rest }: ComponentProps<"select">) {
  return (
    <select className={cn(CONTROL, "h-9 pr-8 pl-3", className)} {...rest}>
      {children}
    </select>
  );
}

export function Textarea({ className, ...rest }: ComponentProps<"textarea">) {
  return <textarea className={cn(CONTROL, "px-3 py-2", className)} {...rest} />;
}

interface CheckboxProps extends Omit<ComponentProps<"input">, "type"> {
  label?: ReactNode;
  description?: ReactNode;
}

export function Checkbox({ label, description, className, ...rest }: CheckboxProps) {
  const box = (
    <input
      type="checkbox"
      className={cn(
        "size-4 shrink-0 rounded border-slate-300 accent-indigo-600 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-600",
        !label && className,
      )}
      {...rest}
    />
  );
  if (!label) {
    return box;
  }
  return (
    <label className={cn("flex items-start gap-2.5 text-sm", className)}>
      <span className="flex h-5 items-center">{box}</span>
      <span>
        <span className="font-medium text-slate-800">{label}</span>
        {description ? <span className="block text-xs text-slate-500">{description}</span> : null}
      </span>
    </label>
  );
}

interface ChoiceOption<T extends string> {
  value: T;
  label: ReactNode;
  description?: ReactNode;
  icon?: ReactNode;
}

interface ChoiceCardsProps<T extends string> {
  name: string;
  value: T;
  options: readonly ChoiceOption<T>[];
  onChange: (value: T) => void;
  columns?: 2 | 3;
}

/** Nhóm radio dạng thẻ, dùng cho các lựa chọn quan trọng (loại proxy, xử lý trùng). */
export function ChoiceCards<T extends string>({ name, value, options, onChange, columns = 2 }: ChoiceCardsProps<T>) {
  return (
    <div className={cn("grid gap-2", columns === 3 ? "sm:grid-cols-3" : "sm:grid-cols-2")}>
      {options.map((option) => {
        const checked = option.value === value;
        return (
          <label
            key={option.value}
            className={cn(
              "relative flex cursor-pointer gap-2.5 rounded-lg p-3 text-sm ring-1 transition-colors ring-inset has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-offset-2 has-[:focus-visible]:outline-indigo-600",
              checked ? "bg-indigo-50/70 ring-2 ring-indigo-600" : "bg-white ring-slate-300 hover:bg-slate-50",
            )}
          >
            <input
              type="radio"
              name={name}
              value={option.value}
              checked={checked}
              onChange={() => {
                onChange(option.value);
              }}
              className="sr-only"
            />
            {option.icon ? (
              <span className={cn("mt-0.5 shrink-0", checked ? "text-indigo-600" : "text-slate-400")}>
                {option.icon}
              </span>
            ) : null}
            <span>
              <span className={cn("block font-medium", checked ? "text-indigo-900" : "text-slate-800")}>
                {option.label}
              </span>
              {option.description ? (
                <span className="mt-0.5 block text-xs text-slate-500">{option.description}</span>
              ) : null}
            </span>
          </label>
        );
      })}
    </div>
  );
}
