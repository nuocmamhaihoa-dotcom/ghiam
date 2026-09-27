import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  CircleAlert,
  Copy,
  Eye,
  EyeOff,
  Hourglass,
  Info,
  Pencil,
  Power,
  PowerOff,
  RefreshCw,
  RotateCw,
  SearchX,
  Server,
  ShieldAlert,
  Signal,
  Trash2,
  TriangleAlert,
} from "lucide-react";
import { useId, useState, type ReactNode, type SubmitEvent } from "react";
import { toast } from "sonner";

import { Badge } from "../../components/ui/Badge";
import { Button } from "../../components/ui/Button";
import { ConfirmDialog } from "../../components/ui/ConfirmDialog";
import { EmptyState } from "../../components/ui/EmptyState";
import { Checkbox, ChoiceCards, Field, Input, Select, Textarea } from "../../components/ui/form";
import { Drawer } from "../../components/ui/Overlay";
import { Spinner } from "../../components/ui/Spinner";
import { ApiError, errorMessage } from "../../lib/api";
import { copyText } from "../../lib/clipboard";
import { cn } from "../../lib/cn";
import {
  PROTOCOL_LABELS,
  ROTATION_MODE_LABELS,
  cooldownLeft,
  formatDateTime,
  formatDuration,
  formatLatency,
  formatNumber,
  formatRelative,
  formatRemaining,
  leaseAvailability,
} from "../../lib/format";
import { useNow } from "../../lib/hooks";
import {
  CHECK_MUTATION_KEY,
  ROTATE_MUTATION_KEY,
  isRotatable,
  proxiesApi,
  proxyKeys,
  usePendingIds,
  useProxyDetail,
  useRefreshProxies,
  type RotateVars,
} from "../../lib/proxies";
import {
  buildEditPatch,
  credentialsChanged,
  editValuesOf,
  noteLength,
  validateEdit,
  type ProxyEditValues,
} from "../../lib/proxy-edit";
import type { ProxyDetail, ProxyKind, ProxyUpdate, RotationMethod } from "../../lib/types";
import { MAX_CONCURRENCY, MAX_NOTE_LENGTH } from "../../lib/validation";
import { HealthBadge, KindBadge, RotationStateBadge } from "./badges";
import { DurationInput } from "./DurationInput";
import { notifyCheck, notifyRotate } from "./notify";

function addressOf(proxy: ProxyDetail): string {
  const host = proxy.host.includes(":") ? `[${proxy.host}]` : proxy.host;
  return `${host}:${proxy.port}`;
}

function rotationModeNote(proxy: ProxyDetail): string {
  switch (proxy.rotation_mode) {
    case "url":
      return "Hệ thống gọi link đổi IP của nhà cung cấp rồi kiểm tra lại IP ra.";
    case "session":
      return "Mỗi lần đổi IP, {session} trong tên đăng nhập hoặc mật khẩu được thay bằng một mã mới.";
    case "provider":
      return `Nhà cung cấp tự đổi IP mỗi ${formatDuration(proxy.rotation_interval_sec)}; hệ thống kiểm tra lại proxy sau mỗi chu kỳ để cập nhật IP ra. Không bấm đổi IP thủ công được.`;
    case "none":
      return "Chưa có link đổi IP hoặc {session}. Bấm “Sửa” để thêm link đổi IP, hoặc nhập chu kỳ nếu nhà cung cấp tự đổi IP.";
  }
}

function pendingNote(proxy: ProxyDetail, waitSec: number): string {
  if (proxy.active_leases > 0) {
    return `Proxy sẽ đổi IP khi ${proxy.active_leases} máy đang dùng trả proxy. Trong lúc chờ, proxy không được cho thuê thêm.`;
  }
  if (!proxy.enabled) {
    return "Proxy đang tắt nên lượt đổi IP này chờ tới khi bật lại.";
  }
  if (waitSec > 0) {
    return `Proxy sẽ đổi IP khi hết thời gian chờ giữa 2 lần đổi (còn ${formatDuration(waitSec)}).`;
  }
  return "Proxy sắp đổi IP, hệ thống xử lý trong vài giây.";
}

type NoteTone = "info" | "warning";

const NOTE_STYLES: Record<NoteTone, string> = {
  info: "bg-sky-50 text-sky-900 ring-sky-200",
  warning: "bg-amber-50 text-amber-900 ring-amber-200",
};

function Note({ tone, icon, children }: { tone: NoteTone; icon: ReactNode; children: ReactNode }) {
  return (
    <div className={cn("flex gap-2.5 rounded-lg px-3 py-2.5 text-sm ring-1", NOTE_STYLES[tone])}>
      <span className="mt-0.5 shrink-0">{icon}</span>
      <div className="min-w-0 flex-1 space-y-2">{children}</div>
    </div>
  );
}

function DetailSection({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="space-y-2">
      <h3 className="text-sm font-semibold text-slate-900">{title}</h3>
      <dl className="divide-y divide-slate-100 rounded-lg ring-1 ring-slate-200">{children}</dl>
    </section>
  );
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="grid grid-cols-[8.5rem_minmax(0,1fr)] gap-3 px-3 py-2.5 text-sm sm:grid-cols-[11rem_minmax(0,1fr)]">
      <dt className="text-slate-500">{label}</dt>
      <dd className="min-w-0 text-slate-900">{children}</dd>
    </div>
  );
}

function Muted({ children }: { children: ReactNode }) {
  return <span className="text-slate-400">{children}</span>;
}

function TimeValue({ iso, now, empty }: { iso: string | null; now: number; empty: string }) {
  if (!iso) {
    return <Muted>{empty}</Muted>;
  }
  return (
    <time dateTime={iso}>
      {formatRelative(iso, now)}
      <span className="text-slate-400"> · {formatDateTime(iso)}</span>
    </time>
  );
}

function DetailSkeleton() {
  return (
    <div className="animate-pulse space-y-6" aria-busy="true" aria-label="Đang tải thông tin proxy">
      <div className="space-y-3">
        <div className="flex gap-1.5">
          <div className="h-5 w-16 rounded-md bg-slate-200" />
          <div className="h-5 w-20 rounded-md bg-slate-200" />
        </div>
        <div className="flex gap-2">
          <div className="h-8 w-28 rounded-md bg-slate-100" />
          <div className="h-8 w-24 rounded-md bg-slate-100" />
          <div className="h-8 w-16 rounded-md bg-slate-100" />
        </div>
      </div>
      {[5, 4, 4].map((rows, index) => (
        <div key={index} className="space-y-2">
          <div className="h-4 w-28 rounded bg-slate-200" />
          <div className="divide-y divide-slate-100 rounded-lg ring-1 ring-slate-200">
            {Array.from({ length: rows }, (_, row) => (
              <div key={row} className="flex gap-6 px-3 py-3">
                <div className="h-3.5 w-24 rounded bg-slate-100" />
                <div className="h-3.5 w-40 rounded bg-slate-200" />
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

interface ProxyViewProps {
  proxy: ProxyDetail;
  checking: boolean;
  rotating: boolean;
  toggling: boolean;
  onCheck: () => void;
  onRotate: (force: boolean) => void;
  onToggle: () => void;
  onEdit: () => void;
  onDelete: () => void;
}

function ProxyView({ proxy, checking, rotating, toggling, onCheck, onRotate, onToggle, onEdit, onDelete }: ProxyViewProps) {
  const now = useNow();
  const [revealUrl, setRevealUrl] = useState(false);
  const quarantineLeft = formatRemaining(proxy.quarantined_until, now);
  const waitSec = cooldownLeft(proxy, now);
  const lease = leaseAvailability(proxy, now);
  const location = [proxy.country, proxy.isp].filter(Boolean).join(" · ");
  const fullUrl = proxy.rotation_url_full;
  const manual = isRotatable(proxy);
  const rotating4g = proxy.kind === "rotating";

  async function copyRotationUrl(url: string) {
    if (await copyText(url)) {
      toast.success("Đã sao chép link đổi IP");
      return;
    }
    setRevealUrl(true);
    toast.error("Trình duyệt không cho sao chép tự động", {
      description: "Link đã được hiện đầy đủ, hãy bôi đen rồi sao chép thủ công.",
    });
  }

  return (
    <div className="space-y-6">
      <div className="space-y-3">
        <div className="flex flex-wrap items-center gap-1.5">
          <KindBadge kind={proxy.kind} />
          <HealthBadge health={proxy.health} checking={checking} />
          {rotating4g && proxy.rotation_state !== "idle" ? <RotationStateBadge state={proxy.rotation_state} /> : null}
          {proxy.enabled ? null : (
            <Badge tone="gray" icon={<PowerOff className="size-3" aria-hidden />}>
              Đang tắt
            </Badge>
          )}
          {quarantineLeft ? (
            <Badge tone="amber" icon={<ShieldAlert className="size-3" aria-hidden />}>
              Cách ly {quarantineLeft}
            </Badge>
          ) : null}
        </div>
        <div className="flex flex-wrap gap-2">
          <Button size="sm" icon={<RefreshCw className="size-4" aria-hidden />} loading={checking} onClick={onCheck}>
            Kiểm tra ngay
          </Button>
          {manual ? (
            <Button
              size="sm"
              icon={<RotateCw className="size-4" aria-hidden />}
              loading={rotating}
              onClick={() => {
                onRotate(false);
              }}
            >
              Đổi IP ngay
            </Button>
          ) : null}
          <Button
            size="sm"
            icon={
              proxy.enabled ? <PowerOff className="size-4" aria-hidden /> : <Power className="size-4" aria-hidden />
            }
            loading={toggling}
            onClick={onToggle}
          >
            {proxy.enabled ? "Tắt proxy" : "Bật proxy"}
          </Button>
          <Button size="sm" icon={<Pencil className="size-4" aria-hidden />} onClick={onEdit}>
            Sửa
          </Button>
          <Button
            size="sm"
            variant="ghost"
            icon={<Trash2 className="size-4" aria-hidden />}
            onClick={onDelete}
            className="text-rose-600 hover:bg-rose-50 hover:text-rose-700 sm:ml-auto"
          >
            Xoá
          </Button>
        </div>
      </div>

      {proxy.rotation_state === "rotating" ? (
        <Note tone="info" icon={<Spinner className="size-4 text-sky-600" />}>
          <p>Đang đổi IP, thường mất 10–60 giây. Trong lúc này máy PC không thuê được proxy; trạng thái sẽ tự cập nhật.</p>
        </Note>
      ) : null}
      {proxy.rotation_state === "pending" ? (
        <Note tone="info" icon={<Hourglass className="size-4 text-sky-600" aria-hidden />}>
          <p>{pendingNote(proxy, waitSec)}</p>
          {manual ? (
            <Button
              size="xs"
              loading={rotating}
              onClick={() => {
                onRotate(true);
              }}
            >
              {proxy.active_leases > 0 ? "Đổi ngay (ngắt máy đang dùng)" : "Đổi ngay"}
            </Button>
          ) : null}
        </Note>
      ) : null}
      {proxy.enabled ? null : (
        <Note tone="warning" icon={<PowerOff className="size-4 text-amber-600" aria-hidden />}>
          <p>
            Proxy đang tắt: máy PC không thuê được, hệ thống cũng không tự kiểm tra hay tự đổi IP proxy này. Bạn vẫn bấm
            kiểm tra hoặc đổi IP thủ công được.
          </p>
        </Note>
      )}

      <DetailSection title="Tình trạng">
        <Row label="Độ trễ">{proxy.latency_ms === null ? <Muted>Chưa đo</Muted> : formatLatency(proxy.latency_ms)}</Row>
        <Row label={proxy.health === "dead" ? "IP ra gần nhất" : "IP ra"}>
          {proxy.exit_ip ? (
            <span className="font-mono text-[13px] break-all">{proxy.exit_ip}</span>
          ) : (
            <Muted>Chưa biết</Muted>
          )}
          {location ? <div className="mt-0.5 text-xs text-slate-500">{location}</div> : null}
        </Row>
        <Row label="Kiểm tra lần cuối">
          <TimeValue iso={proxy.last_checked_at} now={now} empty="Chưa kiểm tra lần nào" />
        </Row>
        {proxy.last_check_error ? (
          <Row label="Lỗi kiểm tra">
            <span className="break-words text-rose-600">{proxy.last_check_error}</span>
          </Row>
        ) : null}
        <Row label="Máy PC báo lỗi">
          {proxy.consecutive_failures > 0 ? (
            <span className="text-amber-700">
              {formatNumber(proxy.consecutive_failures)} lần liên tiếp
              {quarantineLeft ? ` · tạm ngừng cho thuê thêm ${quarantineLeft}` : ""}
            </span>
          ) : (
            <Muted>Không có</Muted>
          )}
        </Row>
      </DetailSection>

      <DetailSection title="Cho máy PC thuê">
        <Row label="Cho thuê lúc này">
          <span className="flex items-start gap-2">
            <span
              className={cn("mt-1.5 size-1.5 shrink-0 rounded-full", lease.available ? "bg-emerald-500" : "bg-amber-500")}
            />
            {lease.text}
          </span>
        </Row>
        <Row label="Đang dùng">
          {formatNumber(proxy.active_leases)} máy
          <span className="text-slate-500"> · tối đa {formatNumber(proxy.max_concurrency)} máy cùng lúc</span>
        </Row>
        <Row label="Pool">
          <Badge tone="indigo">{proxy.pool}</Badge>
        </Row>
        <Row label="Dùng lần cuối">
          <TimeValue iso={proxy.last_used_at} now={now} empty="Chưa cho thuê lần nào" />
        </Row>
        <Row label="Kết quả sử dụng">
          {proxy.success_count + proxy.failure_count === 0 ? (
            <Muted>Chưa có</Muted>
          ) : (
            `Tốt ${formatNumber(proxy.success_count)} lần · Lỗi ${formatNumber(proxy.failure_count)} lần`
          )}
        </Row>
        <Row label="Ghi chú">
          {proxy.note ? (
            <span className="break-words whitespace-pre-line">{proxy.note}</span>
          ) : (
            <Muted>Không có</Muted>
          )}
        </Row>
      </DetailSection>

      <DetailSection title="Đổi IP">
        {rotating4g ? (
          <>
            <Row label="Cách đổi IP">
              {ROTATION_MODE_LABELS[proxy.rotation_mode]}
              <p className={cn("mt-0.5 text-xs", proxy.rotation_mode === "none" ? "text-amber-700" : "text-slate-500")}>
                {rotationModeNote(proxy)}
              </p>
            </Row>
            {proxy.rotation_url ? (
              <Row label="Link đổi IP">
                <code className="block font-mono text-[12px] break-all text-slate-800">
                  {revealUrl && fullUrl ? fullUrl : proxy.rotation_url}
                </code>
                {fullUrl ? (
                  <div className="mt-1.5 flex flex-wrap gap-1">
                    <Button
                      size="xs"
                      variant="ghost"
                      icon={
                        revealUrl ? (
                          <EyeOff className="size-3.5" aria-hidden />
                        ) : (
                          <Eye className="size-3.5" aria-hidden />
                        )
                      }
                      onClick={() => {
                        setRevealUrl((value) => !value);
                      }}
                    >
                      {revealUrl ? "Che khoá" : "Hiện đầy đủ"}
                    </Button>
                    <Button
                      size="xs"
                      variant="ghost"
                      icon={<Copy className="size-3.5" aria-hidden />}
                      onClick={() => {
                        void copyRotationUrl(fullUrl);
                      }}
                    >
                      Sao chép
                    </Button>
                  </div>
                ) : null}
              </Row>
            ) : null}
            {proxy.rotation_mode === "url" ? <Row label="Cách gọi link">{proxy.rotation_method}</Row> : null}
            {manual ? (
              <>
                <Row label="Tự đổi theo lịch">
                  {proxy.rotation_interval_sec > 0 ? (
                    <>
                      Mỗi {formatDuration(proxy.rotation_interval_sec)}
                      <p className="mt-0.5 text-xs text-slate-500">
                        Đến giờ mà máy PC đang dùng thì chờ máy trả proxy rồi mới đổi.
                      </p>
                    </>
                  ) : (
                    <Muted>
                      Không, chỉ đổi khi bấm “Đổi IP ngay”{proxy.rotate_on_block ? " hoặc khi máy PC báo bị chặn" : ""}
                    </Muted>
                  )}
                </Row>
                <Row label="Chờ giữa 2 lần đổi">
                  {proxy.rotation_cooldown_sec > 0 ? formatDuration(proxy.rotation_cooldown_sec) : "Không chờ"}
                </Row>
                <Row label="Khi máy PC báo bị chặn">
                  {proxy.rotate_on_block ? "Tự đổi IP trước khi cho thuê tiếp" : <Muted>Không tự đổi IP</Muted>}
                </Row>
                <Row label="Trạng thái">
                  <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
                    <RotationStateBadge state={proxy.rotation_state} />
                    {waitSec > 0 && proxy.rotation_state === "idle" ? (
                      <span className="text-xs text-slate-500">còn chờ {formatDuration(waitSec)} mới đổi tiếp được</span>
                    ) : null}
                  </span>
                </Row>
              </>
            ) : null}
            <Row label="Lần đổi gần nhất">
              {proxy.last_rotated_at ? (
                <>
                  <TimeValue iso={proxy.last_rotated_at} now={now} empty="" />
                  <div className="mt-0.5 text-xs text-slate-500">
                    Tổng cộng {formatNumber(proxy.rotation_count)} lần đổi IP
                  </div>
                </>
              ) : (
                <Muted>Chưa đổi IP lần nào</Muted>
              )}
            </Row>
            {proxy.last_rotation_attempt_at ? (
              <Row label="Lần thử gần nhất">
                <TimeValue iso={proxy.last_rotation_attempt_at} now={now} empty="" />
                {proxy.last_rotation_message ? (
                  <p
                    className={cn(
                      "mt-0.5 text-xs break-words",
                      proxy.last_rotation_ok === false && "text-rose-600",
                      proxy.last_rotation_ok === true && "text-emerald-700",
                      proxy.last_rotation_ok === null && "text-slate-500",
                    )}
                  >
                    {proxy.last_rotation_message}
                  </p>
                ) : null}
              </Row>
            ) : null}
          </>
        ) : (
          <Row label="Cách đổi IP">IP cố định, không đổi IP</Row>
        )}
      </DetailSection>

      <DetailSection title="Kết nối">
        <Row label="Giao thức">{PROTOCOL_LABELS[proxy.protocol]}</Row>
        <Row label="Địa chỉ">
          <span className="font-mono text-[13px] break-all">{addressOf(proxy)}</span>
        </Row>
        <Row label="Tên đăng nhập">
          {proxy.username ? (
            <span className="font-mono text-[13px] break-all">{proxy.username}</span>
          ) : (
            <Muted>Không có</Muted>
          )}
        </Row>
        <Row label="Mật khẩu">{proxy.has_password ? "Đã lưu (mã hoá trên máy chủ)" : <Muted>Không có</Muted>}</Row>
        <Row label="Thêm vào kho">
          <TimeValue iso={proxy.created_at} now={now} empty="" />
        </Row>
      </DetailSection>
    </div>
  );
}

function FormSection({ title, description, children }: { title: string; description?: ReactNode; children: ReactNode }) {
  return (
    <section className="space-y-4 border-t border-slate-200 pt-5">
      <div>
        <h3 className="text-sm font-semibold text-slate-900">{title}</h3>
        {description ? <p className="mt-0.5 text-xs text-slate-500">{description}</p> : null}
      </div>
      {children}
    </section>
  );
}

interface EditFormProps {
  proxy: ProxyDetail;
  pools: readonly string[];
  formId: string;
  onSubmit: (patch: ProxyUpdate) => void;
}

function EditForm({ proxy, pools, formId, onSubmit }: EditFormProps) {
  // So với lúc mở form, không phải dữ liệu mới nhất: dữ liệu tự làm mới nền không được ghi đè chỗ đang sửa.
  const [snapshot] = useState(() => ({ values: editValuesOf(proxy), hadPassword: proxy.has_password }));
  const [values, setValues] = useState(snapshot.values);
  const [showPassword, setShowPassword] = useState(false);
  const errors = validateEdit(values, snapshot.values, snapshot.hadPassword);
  const patch = buildEditPatch(values, snapshot.values, snapshot.hadPassword);
  const field = (name: string) => `${formId}-${name}`;

  function set<K extends keyof ProxyEditValues>(key: K, value: ProxyEditValues[K]) {
    setValues((current) => ({ ...current, [key]: value }));
  }

  function submit(event: SubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    if (Object.keys(errors).length > 0) {
      event.currentTarget.querySelector<HTMLElement>("[aria-invalid='true']")?.focus();
      return;
    }
    onSubmit(patch);
  }

  let passwordHint: string;
  if (values.clearPassword) {
    passwordHint = "Mật khẩu đang lưu sẽ bị xoá khi lưu thay đổi";
  } else if (snapshot.hadPassword) {
    passwordHint = "Để trống nếu giữ mật khẩu đang lưu";
  } else {
    passwordHint = "Để trống nếu proxy không cần mật khẩu";
  }
  const noteChars = noteLength(values.note);

  return (
    <form id={formId} noValidate onSubmit={submit} className="space-y-6">
      <fieldset className="space-y-2">
        <legend className="mb-2 text-sm font-medium text-slate-700">Loại proxy</legend>
        <ChoiceCards<ProxyKind>
          name={field("kind")}
          value={values.kind}
          onChange={(kind) => {
            set("kind", kind);
          }}
          options={[
            {
              value: "static",
              label: "Proxy tĩnh",
              description: "IP cố định, không đổi IP.",
              icon: <Server className="size-4" aria-hidden />,
            },
            {
              value: "rotating",
              label: "Proxy 4G xoay",
              description: "Đổi IP bằng link, {session} hoặc nhà cung cấp tự đổi.",
              icon: <Signal className="size-4" aria-hidden />,
            },
          ]}
        />
        {values.kind === "static" && proxy.kind === "rotating" ? (
          <p className="text-xs text-slate-500">
            Proxy tĩnh không được đổi IP. Link đổi IP và thiết lập đổi IP cũ vẫn được giữ, chuyển lại 4G xoay là dùng
            tiếp.
          </p>
        ) : null}
      </fieldset>

      <FormSection
        title="Kết nối"
        description={`${PROTOCOL_LABELS[proxy.protocol]} · ${addressOf(proxy)}. Không sửa được giao thức, host và cổng: muốn đổi thì xoá proxy rồi nhập lại.`}
      >
        <div className="grid gap-4 sm:grid-cols-2">
          <Field
            label="Tên đăng nhập"
            htmlFor={field("username")}
            error={errors.username}
            hint="Để trống nếu proxy không cần đăng nhập"
          >
            <Input
              id={field("username")}
              name="proxy-username"
              autoComplete="off"
              spellCheck={false}
              autoFocus
              data-1p-ignore
              data-lpignore="true"
              value={values.username}
              aria-invalid={errors.username !== undefined}
              onChange={(event) => {
                set("username", event.target.value);
              }}
              className={cn("font-mono text-[13px]", errors.username && "ring-rose-400")}
            />
          </Field>
          <Field
            label={snapshot.hadPassword ? "Mật khẩu mới" : "Mật khẩu"}
            htmlFor={field("password")}
            error={errors.password}
            hint={passwordHint}
          >
            <div className="relative">
              <Input
                id={field("password")}
                name="proxy-password"
                type={showPassword ? "text" : "password"}
                autoComplete="new-password"
                spellCheck={false}
                data-1p-ignore
                data-lpignore="true"
                value={values.password}
                disabled={values.clearPassword}
                placeholder={snapshot.hadPassword && !values.clearPassword ? "••••••••" : undefined}
                aria-invalid={errors.password !== undefined}
                onChange={(event) => {
                  set("password", event.target.value);
                }}
                className={cn("pr-10 font-mono text-[13px]", errors.password && "ring-rose-400")}
              />
              <button
                type="button"
                disabled={values.clearPassword}
                onClick={() => {
                  setShowPassword((value) => !value);
                }}
                aria-label={showPassword ? "Ẩn mật khẩu" : "Hiện mật khẩu"}
                className="absolute inset-y-0 right-0 flex items-center px-3 text-slate-400 hover:text-slate-600 disabled:opacity-40"
              >
                {showPassword ? <EyeOff className="size-4" aria-hidden /> : <Eye className="size-4" aria-hidden />}
              </button>
            </div>
          </Field>
        </div>
        {snapshot.hadPassword ? (
          <Checkbox
            checked={values.clearPassword}
            onChange={(event) => {
              const clear = event.target.checked;
              setValues((current) => ({ ...current, clearPassword: clear, password: clear ? "" : current.password }));
            }}
            label="Xoá mật khẩu đang lưu"
            description="Dùng khi proxy chuyển sang xác thực theo IP, không cần mật khẩu nữa."
          />
        ) : null}
        {credentialsChanged(patch) ? (
          <Note tone="warning" icon={<TriangleAlert className="size-4 text-amber-600" aria-hidden />}>
            <p>
              Sau khi lưu, proxy về trạng thái “Chưa kiểm tra” và được kiểm tra lại ngay với thông tin đăng nhập mới; máy
              PC chỉ thuê được khi proxy sống.
            </p>
          </Note>
        ) : null}
      </FormSection>

      <FormSection title="Cho máy PC thuê">
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Pool" htmlFor={field("pool")} error={errors.pool} hint="Máy PC thuê proxy theo pool, ví dụ vn-hcm">
            <Input
              id={field("pool")}
              list={field("pools")}
              autoComplete="off"
              value={values.pool}
              aria-invalid={errors.pool !== undefined}
              onChange={(event) => {
                set("pool", event.target.value);
              }}
              className={errors.pool ? "ring-rose-400" : undefined}
            />
            <datalist id={field("pools")}>
              {pools.map((pool) => (
                <option key={pool} value={pool} />
              ))}
            </datalist>
          </Field>
          <Field
            label="Số máy dùng cùng lúc tối đa"
            htmlFor={field("concurrency")}
            error={errors.maxConcurrency}
            hint={`Từ 1 đến ${MAX_CONCURRENCY}. Máy đang dùng vẫn giữ proxy khi giảm số này.`}
          >
            <Input
              id={field("concurrency")}
              inputMode="numeric"
              autoComplete="off"
              value={values.maxConcurrency}
              aria-invalid={errors.maxConcurrency !== undefined}
              onChange={(event) => {
                set("maxConcurrency", event.target.value);
              }}
              className={errors.maxConcurrency ? "ring-rose-400" : undefined}
            />
          </Field>
        </div>
        <Field
          label="Ghi chú"
          htmlFor={field("note")}
          error={errors.note ? `${errors.note} (đang có ${formatNumber(noteChars)})` : undefined}
          hint={`${formatNumber(noteChars)}/${formatNumber(MAX_NOTE_LENGTH)} ký tự`}
        >
          <Textarea
            id={field("note")}
            rows={3}
            value={values.note}
            aria-invalid={errors.note !== undefined}
            onChange={(event) => {
              set("note", event.target.value);
            }}
            className={errors.note ? "ring-rose-400" : undefined}
          />
        </Field>
      </FormSection>

      {values.kind === "rotating" ? (
        <FormSection title="Đổi IP">
          <Field
            label="Link đổi IP"
            htmlFor={field("rotation-url")}
            error={errors.rotationUrl}
            hint="Bỏ trống nếu tên đăng nhập hoặc mật khẩu có {session}, hoặc nhà cung cấp tự đổi IP theo chu kỳ."
          >
            <Input
              id={field("rotation-url")}
              inputMode="url"
              autoComplete="off"
              spellCheck={false}
              placeholder="https://nhacungcap.vn/api/change-ip?key=…"
              value={values.rotationUrl}
              aria-invalid={errors.rotationUrl !== undefined}
              onChange={(event) => {
                set("rotationUrl", event.target.value);
              }}
              className={cn("font-mono text-[13px]", errors.rotationUrl && "ring-rose-400")}
            />
          </Field>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field
              label="Chu kỳ đổi IP"
              htmlFor={field("interval")}
              error={errors.rotationInterval}
              hint="0 = không tự đổi. Không có link đổi IP hay {session} thì nhập chu kỳ nhà cung cấp tự đổi IP."
            >
              <DurationInput
                id={field("interval")}
                value={values.rotationInterval}
                onChange={(value) => {
                  set("rotationInterval", value);
                }}
                invalid={errors.rotationInterval !== undefined}
              />
            </Field>
            <Field
              label="Chờ tối thiểu giữa 2 lần đổi"
              htmlFor={field("cooldown")}
              error={errors.rotationCooldown}
              hint="Tránh gọi đổi IP dồn dập (nhà cung cấp thường giới hạn)"
            >
              <DurationInput
                id={field("cooldown")}
                value={values.rotationCooldown}
                onChange={(value) => {
                  set("rotationCooldown", value);
                }}
                invalid={errors.rotationCooldown !== undefined}
              />
            </Field>
            <Field label="Cách gọi link đổi IP" htmlFor={field("method")} hint="Hầu hết nhà cung cấp dùng GET">
              <Select
                id={field("method")}
                value={values.rotationMethod}
                onChange={(event) => {
                  set("rotationMethod", event.target.value as RotationMethod);
                }}
              >
                <option value="GET">GET</option>
                <option value="POST">POST</option>
              </Select>
            </Field>
          </div>
          <Checkbox
            checked={values.rotateOnBlock}
            onChange={(event) => {
              set("rotateOnBlock", event.target.checked);
            }}
            label="Tự đổi IP khi máy PC báo bị chặn"
            description="Máy PC trả proxy kèm kết quả “bị chặn” thì hệ thống đổi IP trước khi cho thuê tiếp."
          />
        </FormSection>
      ) : null}
    </form>
  );
}

interface DetailDrawerProps {
  id: number;
  pools: readonly string[];
  onClose: () => void;
}

function DetailDrawer({ id, pools, onClose }: DetailDrawerProps) {
  const formId = useId();
  const queryClient = useQueryClient();
  const refresh = useRefreshProxies();
  const detail = useProxyDetail(id);
  const checkingIds = usePendingIds(CHECK_MUTATION_KEY);
  const rotatingIds = usePendingIds(ROTATE_MUTATION_KEY);
  const [editing, setEditing] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);

  const checkMutation = useMutation({
    mutationKey: CHECK_MUTATION_KEY,
    mutationFn: (proxyId: number) => proxiesApi.check(proxyId),
    onSuccess: (result, proxyId) => {
      notifyCheck(proxyId, result);
    },
    onError: (error, proxyId) => {
      toast.error(`Không kiểm tra được proxy #${proxyId}`, { description: errorMessage(error) });
    },
    onSettled: refresh,
  });

  const rotateMutation = useMutation({
    mutationKey: ROTATE_MUTATION_KEY,
    mutationFn: ({ id: proxyId, force }: RotateVars) => proxiesApi.rotate(proxyId, { force, wait: true }),
    onMutate: () => {
      // Chờ kết quả đổi IP có thể mất cả phút: làm mới sớm để thấy ngay trạng thái "Đang đổi IP".
      window.setTimeout(refresh, 400);
    },
    onSuccess: (result, { id: proxyId }) => {
      notifyRotate(proxyId, result, refresh);
    },
    onError: (error, { id: proxyId }) => {
      toast.error(`Không đổi IP được proxy #${proxyId}`, { description: errorMessage(error) });
    },
    onSettled: refresh,
  });

  const toggleMutation = useMutation({
    mutationFn: (enabled: boolean) => proxiesApi.update(id, { enabled }),
    onSuccess: (proxy) => {
      queryClient.setQueryData(proxyKeys.detail(id), proxy);
      if (proxy.enabled) {
        toast.success(`Đã bật proxy #${id}`, { description: "Máy PC thuê được proxy này trở lại khi proxy đang sống." });
      } else {
        toast.success(`Đã tắt proxy #${id}`, {
          description:
            "Máy PC không thuê được proxy này nữa, hệ thống cũng ngừng tự kiểm tra và tự đổi IP. Máy đang thuê vẫn dùng tiếp tới khi trả proxy.",
        });
      }
    },
    onError: (error, enabled) => {
      toast.error(`Chưa ${enabled ? "bật" : "tắt"} được proxy #${id}`, { description: errorMessage(error) });
    },
    onSettled: refresh,
  });

  const updateMutation = useMutation({
    mutationFn: (patch: ProxyUpdate) => proxiesApi.update(id, patch),
    onSuccess: (proxy, patch) => {
      queryClient.setQueryData(proxyKeys.detail(id), proxy);
      setEditing(false);
      if (credentialsChanged(patch)) {
        toast.success("Đã lưu thay đổi", { description: "Đang kiểm tra lại proxy với thông tin đăng nhập mới." });
        checkMutation.mutate(id);
      } else {
        toast.success("Đã lưu thay đổi");
      }
    },
    onSettled: refresh,
  });

  const deleteMutation = useMutation({
    mutationFn: () => proxiesApi.remove(id),
    onSuccess: () => {
      toast.success(`Đã xoá proxy #${id}`);
      setConfirmDelete(false);
      onClose();
      queryClient.removeQueries({ queryKey: proxyKeys.detail(id) });
    },
    onError: (error) => {
      toast.error(`Chưa xoá được proxy #${id}`, { description: errorMessage(error) });
    },
    onSettled: refresh,
  });

  const proxy = detail.data;
  const gone = detail.error instanceof ApiError && detail.error.status === 404;
  const busy = updateMutation.isPending || deleteMutation.isPending;

  function submitEdit(patch: ProxyUpdate) {
    if (Object.keys(patch).length === 0) {
      setEditing(false);
      toast.info("Không có thay đổi nào");
      return;
    }
    updateMutation.mutate(patch);
  }

  let body: ReactNode;
  if (gone) {
    body = (
      <EmptyState
        icon={<SearchX className="size-6" aria-hidden />}
        title="Không tìm thấy proxy"
        description={`Proxy #${id} không còn trong kho, có thể vừa bị xoá.`}
        action={<Button onClick={onClose}>Đóng</Button>}
      />
    );
  } else if (!proxy && detail.isError) {
    body = (
      <EmptyState
        icon={<CircleAlert className="size-6" aria-hidden />}
        title="Chưa tải được thông tin proxy"
        description={errorMessage(detail.error)}
        action={
          <Button
            onClick={() => {
              void detail.refetch();
            }}
          >
            Thử lại
          </Button>
        }
      />
    );
  } else if (!proxy) {
    body = <DetailSkeleton />;
  } else if (editing) {
    body = <EditForm proxy={proxy} pools={pools} formId={formId} onSubmit={submitEdit} />;
  } else {
    body = (
      <ProxyView
        proxy={proxy}
        checking={proxy.check_in_progress || checkingIds.has(id)}
        rotating={proxy.rotation_state === "rotating" || rotatingIds.has(id)}
        toggling={toggleMutation.isPending}
        onCheck={() => {
          checkMutation.mutate(id);
        }}
        onRotate={(force) => {
          rotateMutation.mutate({ id, force });
        }}
        onToggle={() => {
          toggleMutation.mutate(!proxy.enabled);
        }}
        onEdit={() => {
          updateMutation.reset();
          setEditing(true);
        }}
        onDelete={() => {
          deleteMutation.reset();
          setConfirmDelete(true);
        }}
      />
    );
  }

  const cancelEdit = () => {
    setEditing(false);
  };

  return (
    <>
      <Drawer
        open
        onClose={editing ? cancelEdit : onClose}
        size="md"
        busy={busy}
        title={editing ? `Sửa proxy #${id}` : `Proxy #${id}`}
        description={proxy ? <span className="font-mono text-[13px] break-all">{proxy.display}</span> : undefined}
        footer={
          editing && proxy && !gone ? (
            <>
              {updateMutation.isError ? (
                <p role="alert" className="mr-auto min-w-0 text-sm text-rose-600">
                  {errorMessage(updateMutation.error)}
                </p>
              ) : null}
              <Button onClick={cancelEdit} disabled={updateMutation.isPending}>
                Huỷ
              </Button>
              <Button type="submit" form={formId} variant="primary" loading={updateMutation.isPending}>
                Lưu thay đổi
              </Button>
            </>
          ) : undefined
        }
      >
        {body}
        {editing && proxy && !gone ? (
          <p className="mt-6 flex gap-2 text-xs text-slate-500">
            <Info className="mt-px size-3.5 shrink-0" aria-hidden />
            Bấm Esc hoặc “Huỷ” để quay lại xem thông tin mà không lưu.
          </p>
        ) : null}
      </Drawer>
      <ConfirmDialog
        open={confirmDelete}
        title={`Xoá proxy #${id}?`}
        confirmLabel="Xoá proxy"
        danger
        loading={deleteMutation.isPending}
        onConfirm={() => {
          deleteMutation.mutate();
        }}
        onClose={() => {
          setConfirmDelete(false);
        }}
      >
        <p>
          Proxy <span className="font-mono break-all text-slate-900">{proxy?.display ?? `#${id}`}</span> sẽ bị xoá vĩnh
          viễn khỏi kho, không thể hoàn tác.
        </p>
        {proxy && proxy.active_leases > 0 ? (
          <p className="mt-2">
            Đang có {formatNumber(proxy.active_leases)} máy PC thuê proxy này: các máy đó sẽ không gia hạn được và phải
            thuê proxy khác.
          </p>
        ) : null}
      </ConfirmDialog>
    </>
  );
}

interface ProxyDetailDrawerProps {
  proxyId: number | null;
  pools: readonly string[];
  onClose: () => void;
}

export function ProxyDetailDrawer({ proxyId, pools, onClose }: ProxyDetailDrawerProps) {
  return proxyId === null ? null : <DetailDrawer key={proxyId} id={proxyId} pools={pools} onClose={onClose} />;
}
