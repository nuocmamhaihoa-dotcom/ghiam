"use client";

import { useEffect, useState } from "react";
import { AppShell } from "@/components/AppShell";
import { Badge, PageHeader, Panel, SourcePill } from "@/components/ui";
import { api } from "@/lib/api";
import { formatDate } from "@/lib/format";
import type { CoachingPlan } from "@/lib/types";

const STATUS: Record<CoachingPlan["status"], string> = {
  open: "Mới",
  in_progress: "Đang làm",
  completed: "Hoàn tất",
};

const ITEM_STATUS: Record<string, string> = {
  open: "Chưa làm",
  done: "Đã luyện",
  dismissed: "Bỏ qua",
};

export default function CoachingPage() {
  const [plans, setPlans] = useState<CoachingPlan[]>([]);
  const [source, setSource] = useState<"api" | "demo">("demo");
  const [activeId, setActiveId] = useState<string | null>(null);
  const [roleplayId, setRoleplayId] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [feedback, setFeedback] = useState<string | null>(null);

  useEffect(() => {
    void (async () => {
      const res = await api.listCoachingPlans();
      setPlans(res.data);
      setSource(res.source);
      setActiveId(res.data[0]?.id ?? null);
    })();
  }, []);

  const plan = plans.find((p) => p.id === activeId) || plans[0];
  const scenario =
    plan?.roleplay_scenarios.find((s) => s.id === roleplayId) ||
    plan?.roleplay_scenarios[0];

  function submitRoleplay() {
    if (!scenario || !draft.trim()) {
      setFeedback("Hãy nhập câu trả lời roleplay trước khi chấm.");
      return;
    }
    const hasReframe =
      /giá trị|mỗi lần|freeship|chốt|giao/i.test(draft) ||
      draft.length > 40;
    setFeedback(
      hasReframe
        ? `Đạt khung cơ bản cho “${scenario.title}”. Tiếp tục siết câu chốt rõ số lượng + thời gian giao.`
        : `Chưa đủ: cần tái khung giá trị hoặc hỏi chốt. Kỳ vọng: ${scenario.expected_script}`
    );
  }

  return (
    <AppShell>
      <PageHeader
        title="Coaching & Roleplay"
        description="Kế hoạch coaching theo agent, tip luyện tập và kịch bản roleplay objection."
        actions={<SourcePill source={source} />}
      />

      <div className="mb-4 flex flex-wrap gap-2">
        {plans.map((p) => (
          <button
            key={p.id}
            type="button"
            onClick={() => {
              setActiveId(p.id);
              setRoleplayId(null);
              setDraft("");
              setFeedback(null);
            }}
            className={`rounded-lg border px-3 py-2 text-sm ${
              plan?.id === p.id
                ? "border-teal-500/50 bg-teal-500/10 text-teal-100"
                : "border-slate-800 text-slate-300 hover:border-slate-600"
            }`}
          >
            {p.agent_name}
          </button>
        ))}
      </div>

      {!plan ? (
        <Panel>
          <p className="text-sm text-slate-500">Chưa có coaching plan.</p>
        </Panel>
      ) : (
        <div className="grid gap-4 xl:grid-cols-2">
          <Panel
            title={`Plan · ${plan.agent_name}`}
            action={
              <Badge
                tone={
                  plan.status === "completed"
                    ? "emerald"
                    : plan.status === "in_progress"
                      ? "sky"
                      : "amber"
                }
              >
                {STATUS[plan.status]}
              </Badge>
            }
          >
            <div className="mb-3 text-xs text-slate-500">
              {formatDate(plan.period_start)} → {formatDate(plan.period_end)}
            </div>
            <div className="mb-4 flex flex-wrap gap-1.5">
              {plan.focus_areas.map((f) => (
                <Badge key={f} tone="teal">
                  {f}
                </Badge>
              ))}
            </div>
            <ul className="space-y-3">
              {plan.items.map((item) => (
                <li
                  key={item.id}
                  className="rounded-lg border border-slate-800 bg-slate-950/40 p-3"
                >
                  <div className="flex items-center justify-between gap-2">
                    <div className="text-sm font-medium text-slate-100">
                      {item.title}
                    </div>
                    <Badge
                      tone={
                        item.status === "done"
                          ? "emerald"
                          : item.status === "dismissed"
                            ? "slate"
                            : "amber"
                      }
                    >
                      {ITEM_STATUS[item.status]}
                    </Badge>
                  </div>
                  <blockquote className="mt-2 border-l-2 border-teal-500/50 pl-3 text-sm italic text-slate-300">
                    “{item.script}”
                  </blockquote>
                  {item.drill_id ? (
                    <div className="mt-2 font-mono text-xs text-slate-500">
                      {item.drill_id}
                    </div>
                  ) : null}
                </li>
              ))}
            </ul>
          </Panel>

          <Panel title="Roleplay objection">
            <div className="mb-3 flex flex-wrap gap-2">
              {plan.roleplay_scenarios.map((s) => (
                <button
                  key={s.id}
                  type="button"
                  onClick={() => {
                    setRoleplayId(s.id);
                    setDraft("");
                    setFeedback(null);
                  }}
                  className={`rounded-md border px-2.5 py-1 text-xs ${
                    scenario?.id === s.id
                      ? "border-teal-500/50 text-teal-200"
                      : "border-slate-700 text-slate-400"
                  }`}
                >
                  {s.title}
                </button>
              ))}
            </div>

            {scenario ? (
              <div className="space-y-3">
                <div className="rounded-lg border border-slate-800 bg-slate-950/50 p-3 text-sm">
                  <div className="text-xs text-slate-500">Khách nói</div>
                  <p className="mt-1 text-slate-200">“{scenario.objection}”</p>
                  <div className="mt-2 flex gap-2">
                    <Badge>
                      {scenario.difficulty === "easy"
                        ? "Dễ"
                        : scenario.difficulty === "medium"
                          ? "TB"
                          : "Khó"}
                    </Badge>
                  </div>
                </div>
                <label className="block text-sm">
                  <span className="mb-1.5 block text-slate-400">
                    Câu trả lời của bạn
                  </span>
                  <textarea
                    value={draft}
                    onChange={(e) => setDraft(e.target.value)}
                    rows={4}
                    className="w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-slate-100 outline-none ring-teal-500/30 focus:ring-2"
                    placeholder="Nhập script xử lý…"
                  />
                </label>
                <button
                  type="button"
                  onClick={submitRoleplay}
                  className="rounded-lg bg-teal-500 px-4 py-2 text-sm font-semibold text-slate-950 hover:bg-teal-400"
                >
                  Chấm nhanh roleplay
                </button>
                {feedback ? (
                  <div className="rounded-lg border border-slate-700 bg-slate-900/70 px-3 py-2 text-sm text-slate-300">
                    {feedback}
                  </div>
                ) : null}
                <p className="text-xs text-slate-500">
                  Kỳ vọng: {scenario.expected_script}
                </p>
              </div>
            ) : (
              <p className="text-sm text-slate-500">Chưa có kịch bản roleplay.</p>
            )}
          </Panel>
        </div>
      )}
    </AppShell>
  );
}
