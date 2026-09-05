"use client";

import type { RootCause, RootCauseNode } from "@/lib/types";

type Props = {
  rootCause: RootCause;
};

function Node({ node, depth = 0 }: { node: RootCauseNode; depth?: number }) {
  return (
    <div className={depth === 0 ? "" : "ml-4 border-l border-slate-700 pl-3"}>
      <div className="mb-2 flex items-start gap-2 rounded-md border border-slate-800 bg-slate-900/50 px-3 py-2">
        <div
          className="mt-1 h-2 w-2 shrink-0 rounded-full bg-teal-400"
          style={{ opacity: 0.4 + node.weight * 0.6 }}
        />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-mono text-xs text-teal-300/90">{node.code}</span>
            <span className="text-xs text-slate-500">
              trọng số {(node.weight * 100).toFixed(0)}%
            </span>
          </div>
          <div className="text-sm text-slate-200">{node.label}</div>
        </div>
      </div>
      {node.children?.map((c) => (
        <Node key={c.code} node={c} depth={depth + 1} />
      ))}
    </div>
  );
}

export function RootCauseTree({ rootCause }: Props) {
  if (rootCause.status === "Insufficient Evidence") {
    return (
      <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 px-4 py-4 text-sm text-amber-100">
        <div className="font-medium">Insufficient Evidence</div>
        <p className="mt-1 text-amber-200/90">
          {rootCause.reason || "Không đủ bằng chứng để kết luận nguyên nhân gốc."}
        </p>
      </div>
    );
  }

  const tree =
    rootCause.children && rootCause.children.length > 0
      ? rootCause.children
      : [
          {
            code: rootCause.primary_code || "RC-UNKNOWN",
            label: rootCause.label || "Nguyên nhân chưa xác định",
            weight: rootCause.confidence ?? 0.5,
          },
        ];

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-end justify-between gap-2">
        <div>
          <div className="text-sm font-medium text-slate-200">Cây nguyên nhân gốc</div>
          <div className="mt-1 font-mono text-xs text-teal-300">
            {rootCause.primary_code}
          </div>
        </div>
        {rootCause.confidence != null ? (
          <div className="text-xs text-slate-400">
            Độ tin cậy {(rootCause.confidence * 100).toFixed(0)}%
          </div>
        ) : null}
      </div>
      <p className="text-sm text-slate-300">{rootCause.label}</p>
      {rootCause.contributing_factors.length > 0 ? (
        <div className="flex flex-wrap gap-1.5">
          {rootCause.contributing_factors.map((f) => (
            <span
              key={f}
              className="rounded border border-slate-700 bg-slate-900 px-2 py-0.5 text-xs text-slate-300"
            >
              {f}
            </span>
          ))}
        </div>
      ) : null}
      <div className="space-y-1 pt-1">
        {tree.map((n) => (
          <Node key={n.code} node={n} />
        ))}
      </div>
    </div>
  );
}
