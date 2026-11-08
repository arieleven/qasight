import type { ButtonHTMLAttributes } from "react";

type Variant = "primary" | "secondary" | "ghost" | "danger";
type Size = "sm" | "md";

const VARIANT_CLASSES: Record<Variant, string> = {
  primary:
    // Dark text on the bright green: white on green fails WCAG contrast.
    "bg-gradient-to-r from-brand-400 to-brand-300 text-ink-950 shadow-lg shadow-brand-500/30 hover:from-brand-300 hover:to-brand-300 hover:shadow-brand-500/50 active:from-brand-500 active:to-brand-400 disabled:bg-none disabled:bg-ink-700 disabled:text-ink-400 disabled:shadow-none",
  secondary:
    "border border-white/10 bg-ink-800/60 text-ink-100 hover:border-brand-500/60 hover:bg-ink-800 disabled:text-ink-500",
  ghost: "text-ink-200 hover:bg-ink-800 hover:text-ink-100 disabled:text-ink-500",
  danger:
    "bg-danger-500 text-white hover:bg-danger-400 active:bg-danger-600 disabled:bg-ink-700 disabled:text-ink-400"
};

const SIZE_CLASSES: Record<Size, string> = {
  sm: "px-2.5 py-1 text-xs",
  md: "px-3.5 py-2 text-sm"
};

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
}

/**
 * The button look, for a link that navigates: wrapping a <Button> in a <Link>
 * puts two tab stops (and a button inside a link) on one control.
 */
export function buttonClasses(variant: Variant = "primary", size: Size = "md"): string {
  return `inline-flex items-center justify-center gap-1.5 rounded-xl font-semibold transition-[color,background-color,border-color,box-shadow] duration-200 disabled:cursor-not-allowed ${VARIANT_CLASSES[variant]} ${SIZE_CLASSES[size]}`;
}

export function Button({ variant = "primary", size = "md", className, type = "button", ...props }: ButtonProps) {
  return (
    <button
      type={type}
      className={`${buttonClasses(variant, size)} ${className ?? ""}`}
      {...props}
    />
  );
}
