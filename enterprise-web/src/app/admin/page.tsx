"use client";

import { useEffect, useState } from "react";
import { AppShell } from "@/components/AppShell";
import { DataTable } from "@/components/DataTable";
import { Badge, PageHeader, Panel, SourcePill } from "@/components/ui";
import { api } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import type { AdminUser, Role } from "@/lib/types";

const ROLE_LABEL: Record<Role, string> = {
  agent: "Sale",
  team_lead: "Team Lead",
  qa_specialist: "QA",
  qa_manager: "QA Manager",
  admin: "Admin",
  executive: "Executive",
};

const PERMISSION_MATRIX: { role: Role; permissions: string[] }[] = [
  {
    role: "agent",
    permissions: ["calls:read (own)", "scorecard:read (own)", "coaching:read"],
  },
  {
    role: "team_lead",
    permissions: [
      "calls:read (team)",
      "scorecard:read",
      "coaching:manage",
      "revenue:read (team)",
    ],
  },
  {
    role: "qa_specialist",
    permissions: ["calls:read", "scorecard:read", "scorecard:override", "appeals"],
  },
  {
    role: "qa_manager",
    permissions: ["* QA", "rulebook:read", "calibration", "audit:read"],
  },
  {
    role: "admin",
    permissions: ["admin:users", "rulebook:write/publish", "ai-config", "audit:read"],
  },
  {
    role: "executive",
    permissions: ["dashboard", "revenue:read (org)", "calls:read (agg)"],
  },
];

export default function AdminPage() {
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [source, setSource] = useState<"api" | "demo">("demo");

  useEffect(() => {
    void (async () => {
      const res = await api.listUsers();
      setUsers(res.data);
      setSource(res.source);
    })();
  }, []);

  return (
    <AppShell>
      <PageHeader
        title="Admin · Users & RBAC"
        description="Quản lý người dùng tenant, vai trò và ma trận quyền."
        actions={<SourcePill source={source} />}
      />

      <Panel title="Người dùng" className="mb-6">
        <DataTable
          rows={users}
          rowKey={(r) => r.id}
          columns={[
            {
              key: "user",
              header: "User",
              render: (r) => (
                <div>
                  <div className="font-medium">{r.full_name}</div>
                  <div className="text-xs text-slate-500">{r.email}</div>
                </div>
              ),
            },
            {
              key: "team",
              header: "Team",
              render: (r) => r.team_name,
            },
            {
              key: "roles",
              header: "Roles",
              render: (r) => (
                <div className="flex flex-wrap gap-1">
                  {r.roles.map((role) => (
                    <Badge key={role} tone="teal">
                      {ROLE_LABEL[role]}
                    </Badge>
                  ))}
                </div>
              ),
            },
            {
              key: "active",
              header: "Trạng thái",
              render: (r) =>
                r.active ? (
                  <Badge tone="emerald">Active</Badge>
                ) : (
                  <Badge tone="rose">Inactive</Badge>
                ),
            },
            {
              key: "login",
              header: "Đăng nhập gần nhất",
              render: (r) =>
                r.last_login_at ? formatDateTime(r.last_login_at) : "—",
            },
          ]}
        />
      </Panel>

      <Panel title="Ma trận RBAC (tham chiếu)">
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {PERMISSION_MATRIX.map((row) => (
            <div
              key={row.role}
              className="rounded-lg border border-slate-800 bg-slate-950/40 p-3"
            >
              <div className="mb-2 text-sm font-medium text-slate-100">
                {ROLE_LABEL[row.role]}
              </div>
              <ul className="space-y-1 text-xs text-slate-400">
                {row.permissions.map((p) => (
                  <li key={p} className="font-mono">
                    {p}
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      </Panel>
    </AppShell>
  );
}
