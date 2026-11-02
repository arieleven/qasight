import type { TestLabelItem } from "../../lib/api-client";

/** `feature:Checkout`, the value `?label=` and the API's `label` filter take. */
export function labelFilter(label: TestLabelItem): string {
  return `${label.name}:${label.value}`;
}

/** How a label reads on a chip: a tag is just its value, anything else is "name: value". */
export function labelText(label: TestLabelItem): string {
  return label.name === "tag" ? label.value : `${label.name}: ${label.value}`;
}

/**
 * Whether `labels` holds `wanted`: `name:value` exactly, or a bare value under any name.
 * Case-insensitive; the same rule as `has_label` in testo_core/services/test_history.py.
 */
export function hasLabel(labels: TestLabelItem[] | undefined, wanted: string): boolean {
  const needle = wanted.trim().toLowerCase();
  if (!needle) return true;
  const colon = needle.indexOf(":");
  const name = colon >= 0 ? needle.slice(0, colon).trim() : "";
  const value = colon >= 0 ? needle.slice(colon + 1).trim() : "";
  return (labels ?? []).some((label) => {
    const labelName = label.name.toLowerCase();
    const labelValue = label.value.toLowerCase();
    return (colon >= 0 && labelName === name && labelValue === value) || labelValue === needle;
  });
}
