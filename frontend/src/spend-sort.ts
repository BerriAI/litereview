export type SpendSortKey = "name" | "runs" | "pull_requests" | "average_runs_per_pr" | "cost_usd" | "average_cost_usd" | "average_pr_cost_usd";
export type SpendSort = { key: SpendSortKey; direction: "ascending" | "descending" };
type SortableSpend = { name: string } & Partial<Record<Exclude<SpendSortKey, "name">, number | null>>;

export function sortSpendRows<T extends SortableSpend>(rows: readonly T[], sort: SpendSort): T[] {
  const direction = sort.direction === "ascending" ? 1 : -1;
  return [...rows].sort((a, b) => {
    const left = a[sort.key], right = b[sort.key];
    // Missing prices stay last in either direction; zero is a recorded cost.
    if (left == null || right == null) return left == null ? (right == null ? 0 : 1) : -1;
    const difference = typeof left === "number" && typeof right === "number" ? left - right :
      String(left).localeCompare(String(right), undefined, { numeric: true, sensitivity: "base" });
    return direction * difference;
  });
}
