import { keepPreviousData, skipToken, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ChevronRight,
  CircleAlert,
  ClipboardPaste,
  Eraser,
  FileUp,
  Info,
  Server,
  Signal,
  TriangleAlert,
  Upload,
} from "lucide-react";
import { useDeferredValue, useId, useMemo, useRef, useState, type DragEvent, type ReactNode } from "react";
import { toast } from "sonner";

import { Badge, type Tone } from "../../components/ui/Badge";
import { Button } from "../../components/ui/Button";
import { Checkbox, ChoiceCards, Field, Input, Select, Textarea } from "../../components/ui/form";
import { Drawer } from "../../components/ui/Overlay";
import { Spinner } from "../../components/ui/Spinner";
import { errorMessage } from "../../lib/api";
import { cn } from "../../lib/cn";
import { PREVIEW_STATUS_LABELS, PROTOCOL_LABELS, ROTATION_MODE_LABELS, formatNumber } from "../../lib/format";
import { useDebouncedValue } from "../../lib/hooks";
import {
  MAX_IMPORT_FILE_BYTES,
  MAX_IMPORT_TEXT_LENGTH,
  appendText,
  countProxyLines,
  pickLines,
} from "../../lib/import-text";
import { proxiesApi, useRefreshProxies } from "../../lib/proxies";
import type {
  DuplicatePolicy,
  ImportDefaults,
  ImportPreview,
  ImportPreviewRequest,
  ImportRequest,
  ImportResult,
  PreviewLine,
  PreviewStatus,
  ProxyKind,
  ProxyProtocol,
  RotationMethod,
} from "../../lib/types";
import {
  concurrencyError,
  durationError,
  durationSeconds,
  normalizePool,
  parseWholeNumber,
  poolError,
  type DurationValue,
} from "../../lib/validation";
import { KindBadge } from "./badges";
import { DurationInput } from "./DurationInput";

const PREVIEW_KEY = ["proxy-import-preview"] as const;
const PREVIEW_DELAY_MS = 400;
const RENDER_LIMIT = 500;

const PLACEHOLDER = [
  "1.2.3.4:8080",
  "1.2.3.4:8080:user:pass",
  "socks5://user:pass@5.6.7.8:1080",
  "4g.nhacungcap.vn:10001:user:pass|https://nhacungcap.vn/api/change-ip?key=abc123",
].join("\n");

const FORMAT_EXAMPLES: readonly { code: string; note: string }[] = [
  { code: "1.2.3.4:8080", note: "host:port, proxy không cần mật khẩu" },
  { code: "1.2.3.4:8080:user:pass", note: "host:port:user:pass, dạng phổ biến nhất" },
  { code: "user:pass@1.2.3.4:8080", note: "user:pass@host:port (hoặc user:pass:host:port)" },
  { code: "socks5://user:pass@1.2.3.4:1080", note: "ghi rõ giao thức http://, https:// hoặc socks5://" },
  { code: "1.2.3.4 8080 user pass", note: "các cột cách nhau bằng dấu cách hoặc tab, ví dụ copy từ Excel" },
  { code: "[2001:db8::1]:8080:user:pass", note: "địa chỉ IPv6 đặt trong ngoặc vuông" },
  {
    code: "4g.vn:10001:user:pass|https://4g.vn/api/change-ip?key=abc",
    note: "proxy 4G kèm link đổi IP, cách nhau bằng | hoặc dấu cách",
  },
  {
    code: "gw.vn:7000:user-session-{session}:pass",
    note: "proxy 4G đổi IP bằng session: mỗi lần đổi IP, {session} được thay bằng một mã ngẫu nhiên mới",
  },
];

type LineFilter = "all" | PreviewStatus | "warnings";

const STATUS_TONES: Record<PreviewStatus, Tone> = { new: "green", update: "blue", duplicate: "gray", invalid: "red" };

function matchesFilter(line: PreviewLine, filter: LineFilter): boolean {
  if (filter === "all") {
    return true;
  }
  if (filter === "warnings") {
    return line.status !== "invalid" && line.warnings.length > 0;
  }
  return line.status === filter;
}

function lineNote(line: PreviewLine): string | null {
  if (line.status === "duplicate" && line.duplicate_of_line !== null) {
    return `Trùng với dòng ${line.duplicate_of_line}, sẽ bỏ qua`;
  }
  if (line.status === "duplicate" && line.existing_id !== null) {
    return `Đã có trong kho (proxy #${line.existing_id}), sẽ bỏ qua`;
  }
  if (line.status === "update" && line.existing_id !== null) {
    return `Sẽ ghi đè proxy #${line.existing_id} đang có trong kho`;
  }
  return null;
}

function hasFiles(event: DragEvent): boolean {
  return event.dataTransfer.types.includes("Files");
}

function showImportToast(result: ImportResult): void {
  const done = [
    result.created ? `thêm ${formatNumber(result.created)} proxy mới` : null,
    result.updated ? `cập nhật ${formatNumber(result.updated)} proxy` : null,
  ].filter((part) => part !== null);
  const notes = [
    result.skipped ? `Bỏ qua ${formatNumber(result.skipped)} dòng trùng` : null,
    result.invalid ? `${formatNumber(result.invalid)} dòng lỗi không nhập được` : null,
    result.check_scheduled ? `Đang kiểm tra sống/chết ${formatNumber(result.check_scheduled)} proxy` : null,
  ].filter((part) => part !== null);
  const description = notes.length ? notes.join(" · ") : undefined;
  if (done.length) {
    toast.success(`Đã ${done.join(" và ")}`, { description });
  } else {
    toast.warning("Không có proxy nào được nhập", { description });
  }
}

function Section({ title, description, children }: { title: string; description?: ReactNode; children: ReactNode }) {
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

function FormatHelp() {
  return (
    <details className="group rounded-lg bg-white ring-1 ring-slate-200">
      <summary className="flex cursor-pointer list-none items-center gap-2 px-3 py-2 text-sm font-medium text-slate-700 select-none [&::-webkit-details-marker]:hidden">
        <Info className="size-4 text-slate-400" aria-hidden />
        Các định dạng được hỗ trợ
        <ChevronRight className="ml-auto size-4 text-slate-400 transition-transform group-open:rotate-90" aria-hidden />
      </summary>
      <div className="space-y-3 border-t border-slate-200 px-3 py-3 text-xs text-slate-600">
        <ul className="space-y-2">
          {FORMAT_EXAMPLES.map((example) => (
            <li key={example.code}>
              <code className="block font-mono text-[12px] break-all text-slate-900">{example.code}</code>
              <span className="text-slate-500">{example.note}</span>
            </li>
          ))}
        </ul>
        <div className="space-y-1 rounded-md bg-slate-50 p-2.5">
          <p className="font-medium text-slate-700">Tuỳ chọn riêng cho từng dòng (thêm vào cuối dòng):</p>
          <code className="block font-mono text-[12px] break-all text-slate-900">
            type=4g pool=vn-hcm interval=10m cooldown=90s concurrency=1 method=POST protocol=socks5
          </code>
          <p>
            <b>type</b>: static hoặc 4g · <b>interval</b>: tự đổi IP theo lịch · <b>cooldown</b>: chờ tối thiểu giữa 2
            lần đổi (số giây, hoặc 30s, 10m, 1h) · <b>concurrency</b>: số máy dùng chung · <b>method</b>: GET/POST khi
            gọi link đổi IP · <b>protocol</b>: http, https hoặc socks5.
          </p>
        </div>
        <p>
          Dòng có link đổi IP hoặc <code className="font-mono">{"{session}"}</code> tự được nhận là proxy 4G xoay. Dòng
          trống và dòng bắt đầu bằng <code className="font-mono">#</code> được bỏ qua.
        </p>
      </div>
    </details>
  );
}

function PreviewRow({ line }: { line: PreviewLine }) {
  const note = lineNote(line);
  return (
    <li className="flex gap-3 px-3 py-2.5 text-sm">
      <span className="w-10 shrink-0 pt-0.5 text-right font-mono text-xs text-slate-400 tabular-nums">
        {line.line_no}
      </span>
      <div className="min-w-0 flex-1 space-y-1">
        <div className="flex items-center gap-2">
          <Badge tone={STATUS_TONES[line.status]}>{PREVIEW_STATUS_LABELS[line.status]}</Badge>
          <span
            title={line.display}
            className={cn(
              "min-w-0 truncate font-mono text-[13px]",
              line.status === "invalid" ? "text-slate-500" : "text-slate-900",
            )}
          >
            {line.display}
          </span>
        </div>
        {line.kind ? (
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-slate-500">
            <KindBadge kind={line.kind} />
            {line.protocol ? <span>{PROTOCOL_LABELS[line.protocol]}</span> : null}
            {line.pool ? <span>· pool {line.pool}</span> : null}
            {line.max_concurrency ? <span>· tối đa {line.max_concurrency} máy</span> : null}
            {line.kind === "rotating" && line.rotation_mode ? (
              <span>· {ROTATION_MODE_LABELS[line.rotation_mode]}</span>
            ) : null}
          </div>
        ) : null}
        {line.rotation_url ? (
          <div className="truncate font-mono text-xs text-slate-500" title={line.rotation_url}>
            {line.rotation_url}
          </div>
        ) : null}
        {line.error ? (
          <p className="flex gap-1.5 text-xs text-rose-600">
            <CircleAlert className="mt-px size-3.5 shrink-0" aria-hidden />
            {line.error}
          </p>
        ) : null}
        {note ? <p className="text-xs text-slate-500">{note}</p> : null}
        {line.warnings.map((warning, index) => (
          <p key={`${index}-${warning}`} className="flex gap-1.5 text-xs text-amber-700">
            <TriangleAlert className="mt-px size-3.5 shrink-0" aria-hidden />
            {warning}
          </p>
        ))}
      </div>
    </li>
  );
}

interface PreviewResultProps {
  preview: ImportPreview;
  filter: LineFilter;
  onFilter: (filter: LineFilter) => void;
  dimmed: boolean;
}

function PreviewResult({ preview, filter, onFilter, dimmed }: PreviewResultProps) {
  const { summary } = preview;
  const tabs: readonly { value: LineFilter; label: string; count: number; dot: string }[] = [
    { value: "all", label: "Tất cả", count: summary.total, dot: "bg-slate-400" },
    { value: "new", label: "Mới", count: summary.new, dot: "bg-emerald-500" },
    { value: "update", label: "Cập nhật", count: summary.update, dot: "bg-sky-500" },
    { value: "duplicate", label: "Trùng", count: summary.duplicate, dot: "bg-slate-400" },
    { value: "invalid", label: "Lỗi", count: summary.invalid, dot: "bg-rose-500" },
    { value: "warnings", label: "Có cảnh báo", count: summary.with_warnings, dot: "bg-amber-500" },
  ];
  const lines = preview.lines.filter((line) => matchesFilter(line, filter));
  const importCount = summary.new + summary.update;

  return (
    <div className={cn("space-y-3 transition-opacity", dimmed && "opacity-60")}>
      <div className="flex flex-wrap gap-1.5" role="group" aria-label="Lọc dòng xem trước">
        {tabs.map((tab) => {
          const active = filter === tab.value;
          return (
            <button
              key={tab.value}
              type="button"
              aria-pressed={active}
              disabled={tab.count === 0 && tab.value !== "all" && !active}
              onClick={() => {
                onFilter(tab.value);
              }}
              className={cn(
                "inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium ring-1 transition-colors ring-inset disabled:cursor-not-allowed disabled:opacity-40",
                active
                  ? "bg-slate-900 text-white ring-slate-900"
                  : "bg-white text-slate-600 ring-slate-200 hover:bg-slate-50 disabled:hover:bg-white",
              )}
            >
              <span className={cn("size-1.5 rounded-full", active ? "bg-white" : tab.dot)} />
              {tab.label}
              <span className={cn("tabular-nums", active ? "text-slate-300" : "text-slate-400")}>
                {formatNumber(tab.count)}
              </span>
            </button>
          );
        })}
      </div>

      <p className="text-sm text-slate-700">
        Sẽ nhập <b className="tabular-nums">{formatNumber(importCount)}</b> proxy
        {importCount > 0 ? (
          <span className="text-slate-500">
            {" "}
            ({formatNumber(summary.static)} tĩnh · {formatNumber(summary.rotating)} 4G xoay
            {summary.update > 0 ? ` · ${formatNumber(summary.update)} cập nhật proxy cũ` : ""})
          </span>
        ) : null}
      </p>

      {preview.truncated ? (
        <p className="text-xs text-slate-500">
          Danh sách dài nên chỉ hiện {formatNumber(preview.lines.length)} dòng, ưu tiên dòng lỗi và dòng có cảnh báo. Số
          liệu phía trên tính trên toàn bộ danh sách.
        </p>
      ) : null}

      {lines.length === 0 ? (
        <p className="rounded-lg px-3 py-8 text-center text-sm text-slate-500 ring-1 ring-slate-200">
          Không có dòng nào ở mục này.
        </p>
      ) : (
        <ul className="max-h-[28rem] divide-y divide-slate-100 overflow-y-auto rounded-lg bg-white ring-1 ring-slate-200 lg:max-h-[calc(100vh-22rem)]">
          {lines.slice(0, RENDER_LIMIT).map((line) => (
            <PreviewRow key={line.line_no} line={line} />
          ))}
        </ul>
      )}
      {lines.length > RENDER_LIMIT ? (
        <p className="text-xs text-slate-500">
          Đang hiện {formatNumber(RENDER_LIMIT)}/{formatNumber(lines.length)} dòng, chọn một mục ở trên để xem theo loại.
        </p>
      ) : null}
    </div>
  );
}

function PreviewMessage({ tone, icon, children }: { tone: "slate" | "amber" | "rose"; icon: ReactNode; children: ReactNode }) {
  return (
    <div
      className={cn(
        "flex gap-3 rounded-lg px-4 py-4 text-sm",
        tone === "slate" && "border border-dashed border-slate-300 bg-slate-50/60 text-slate-600",
        tone === "amber" && "bg-amber-50 text-amber-900 ring-1 ring-amber-200",
        tone === "rose" && "bg-rose-50 text-rose-800 ring-1 ring-rose-200",
      )}
    >
      <span className="mt-0.5 shrink-0">{icon}</span>
      <div className="min-w-0 space-y-2">{children}</div>
    </div>
  );
}

interface ImportDrawerProps {
  open: boolean;
  onClose: () => void;
  pools: readonly string[];
}

interface Draft {
  text: string;
  revision: number;
}

/** Luôn được gắn sẵn (chỉ ẩn khi đóng) để lỡ tay đóng thì nội dung đã dán và thiết lập vẫn còn. */
export function ImportDrawer({ open, onClose, pools }: ImportDrawerProps) {
  const id = useId();
  const queryClient = useQueryClient();
  const refresh = useRefreshProxies();
  const fileInput = useRef<HTMLInputElement>(null);

  const [draft, setDraft] = useState<Draft>({ text: "", revision: 0 });
  const [kind, setKind] = useState<ProxyKind>("static");
  const [protocol, setProtocol] = useState<ProxyProtocol>("http");
  const [pool, setPool] = useState("default");
  const [concurrency, setConcurrency] = useState("");
  const [rotationInterval, setRotationInterval] = useState<DurationValue>({ amount: "0", unit: "m" });
  const [cooldown, setCooldown] = useState<DurationValue>({ amount: "60", unit: "s" });
  const [method, setMethod] = useState<RotationMethod>("GET");
  const [rotateOnBlock, setRotateOnBlock] = useState(true);
  const [onDuplicate, setOnDuplicate] = useState<DuplicatePolicy>("skip");
  const [checkAfterImport, setCheckAfterImport] = useState(true);
  const [filter, setFilter] = useState<LineFilter>("all");
  const [notice, setNotice] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);

  const setText = (text: string) => {
    setDraft((current) => ({ text, revision: current.revision + 1 }));
  };

  const poolErr = poolError(pool);
  const concurrencyErr = concurrency.trim() ? concurrencyError(concurrency) : null;
  const intervalErr = durationError(rotationInterval);
  const cooldownErr = durationError(cooldown);

  const defaults = useMemo<ImportDefaults | null>(() => {
    const intervalSec = durationSeconds(rotationInterval);
    const cooldownSec = durationSeconds(cooldown);
    const customConcurrency = concurrency.trim() !== "";
    if (poolError(pool) || intervalSec === null || cooldownSec === null) {
      return null;
    }
    if (customConcurrency && concurrencyError(concurrency)) {
      return null;
    }
    return {
      kind,
      protocol,
      pool: normalizePool(pool),
      max_concurrency: customConcurrency ? parseWholeNumber(concurrency) : null,
      rotation_interval_sec: intervalSec,
      rotation_cooldown_sec: cooldownSec,
      rotation_method: method,
      rotate_on_block: rotateOnBlock,
    };
  }, [kind, protocol, pool, concurrency, rotationInterval, cooldown, method, rotateOnBlock]);

  const hasText = /\S/.test(draft.text);
  const tooLong = draft.text.length > MAX_IMPORT_TEXT_LENGTH;
  const request = useMemo<ImportPreviewRequest | null>(
    () => (defaults && hasText && !tooLong ? { text: draft.text, defaults, on_duplicate: onDuplicate } : null),
    [defaults, hasText, tooLong, draft.text, onDuplicate],
  );
  const snapshot = useMemo(() => (request ? { revision: draft.revision, request } : null), [request, draft.revision]);
  const debounced = useDebouncedValue(snapshot, PREVIEW_DELAY_MS);

  const deferredText = useDeferredValue(draft.text);
  const lineCount = useMemo(() => countProxyLines(deferredText), [deferredText]);

  const previewQuery = useQuery({
    queryKey: [...PREVIEW_KEY, debounced?.revision, debounced?.request.defaults, debounced?.request.on_duplicate],
    queryFn: open && debounced ? ({ signal }) => proxiesApi.preview(debounced.request, signal) : skipToken,
    placeholderData: keepPreviousData,
    retry: false,
    refetchOnWindowFocus: false,
    staleTime: 30_000,
  });

  const importMutation = useMutation({
    mutationFn: (body: ImportRequest) => proxiesApi.import(body),
    onSuccess: (result, body) => {
      showImportToast(result);
      queryClient.removeQueries({ queryKey: PREVIEW_KEY });
      refresh();
      setFilter("all");
      if (result.errors.length === 0) {
        setText("");
        setNotice(null);
        onClose();
        return;
      }
      const kept = result.errors.length;
      setText(pickLines(body.text, result.errors.map((line) => line.line_no)).join("\n"));
      setNotice(
        `Đã nhập xong các dòng hợp lệ. Còn ${formatNumber(kept)} dòng lỗi được giữ lại trong ô nhập để bạn sửa rồi nhập tiếp` +
          (result.invalid > kept ? ` (chỉ giữ ${formatNumber(kept)}/${formatNumber(result.invalid)} dòng lỗi đầu tiên).` : "."),
      );
    },
    onError: (error) => {
      toast.error("Chưa nhập được proxy", { description: errorMessage(error) });
      void queryClient.invalidateQueries({ queryKey: PREVIEW_KEY });
    },
  });

  const preview = request && debounced ? previewQuery.data : undefined;
  const stale = request !== debounced?.request || previewQuery.isFetching || previewQuery.isPlaceholderData;
  const ready = preview !== undefined && !stale;
  const importCount = preview ? preview.summary.new + preview.summary.update : 0;
  const canImport = ready && importCount > 0 && !importMutation.isPending;
  const previewFailed = request !== null && previewQuery.isError && !previewQuery.isFetching;

  const submit = () => {
    if (request && canImport) {
      importMutation.mutate({ ...request, check_after_import: checkAfterImport });
    }
  };

  async function addFiles(files: readonly File[]) {
    for (const file of files) {
      if (file.size > MAX_IMPORT_FILE_BYTES) {
        toast.error(`File ${file.name} quá lớn`, { description: "Mỗi file tối đa 8 MB, hãy chia nhỏ danh sách." });
        continue;
      }
      let content: string;
      try {
        content = await file.text();
      } catch {
        toast.error(`Không đọc được file ${file.name}`);
        continue;
      }
      if (content.includes("\u0000")) {
        toast.error(`File ${file.name} không phải file văn bản`, {
          description: "Hãy lưu danh sách dưới dạng .txt (mỗi dòng một proxy) rồi thử lại.",
        });
        continue;
      }
      setDraft((current) => ({ text: appendText(current.text, content), revision: current.revision + 1 }));
      setNotice(null);
      toast.success(`Đã thêm nội dung file ${file.name}`, {
        description: `${formatNumber(countProxyLines(content))} dòng proxy`,
      });
    }
  }

  let footerStatus: string;
  if (importMutation.isPending) {
    footerStatus = "Đang nhập proxy…";
  } else if (!hasText) {
    footerStatus = "Chưa có proxy nào trong ô nhập";
  } else if (tooLong || !defaults) {
    footerStatus = "Cần sửa lỗi trước khi nhập";
  } else if (previewFailed) {
    footerStatus = "Chưa xem trước được danh sách";
  } else if (!ready) {
    footerStatus = "Đang cập nhật xem trước…";
  } else if (importCount === 0) {
    footerStatus =
      preview.summary.duplicate > 0 && onDuplicate === "skip"
        ? "Mọi proxy đều đã có. Chọn “Cập nhật proxy cũ” nếu muốn ghi đè."
        : "Không có proxy hợp lệ để nhập";
  } else {
    const skipped = [
      preview.summary.duplicate ? `bỏ qua ${formatNumber(preview.summary.duplicate)} dòng trùng` : null,
      preview.summary.invalid ? `${formatNumber(preview.summary.invalid)} dòng lỗi` : null,
    ].filter((part) => part !== null);
    footerStatus = [`${formatNumber(importCount)} proxy sẽ được nhập`, ...skipped].join(" · ");
  }

  let previewContent: ReactNode;
  if (!hasText) {
    previewContent = (
      <PreviewMessage tone="slate" icon={<ClipboardPaste className="size-5 text-slate-400" aria-hidden />}>
        <p className="font-medium text-slate-800">Dán danh sách proxy vào ô bên cạnh</p>
        <p>
          Hệ thống tự nhận dạng định dạng, phát hiện dòng trùng, dòng lỗi và proxy 4G rồi hiện kết quả ở đây trước khi
          nhập.
        </p>
      </PreviewMessage>
    );
  } else if (tooLong) {
    previewContent = (
      <PreviewMessage tone="rose" icon={<CircleAlert className="size-5" aria-hidden />}>
        <p>
          Nội dung quá dài (tối đa {formatNumber(MAX_IMPORT_TEXT_LENGTH)} ký tự). Hãy chia danh sách thành nhiều lần
          nhập.
        </p>
      </PreviewMessage>
    );
  } else if (!defaults) {
    previewContent = (
      <PreviewMessage tone="amber" icon={<TriangleAlert className="size-5 text-amber-600" aria-hidden />}>
        <p>Sửa các thiết lập đang báo lỗi để xem trước.</p>
      </PreviewMessage>
    );
  } else if (previewFailed) {
    previewContent = (
      <PreviewMessage tone="rose" icon={<CircleAlert className="size-5" aria-hidden />}>
        <p>{errorMessage(previewQuery.error)}</p>
        <Button
          size="sm"
          onClick={() => {
            void previewQuery.refetch();
          }}
        >
          Thử lại
        </Button>
      </PreviewMessage>
    );
  } else if (!preview) {
    previewContent = (
      <div className="flex items-center justify-center gap-2 rounded-lg px-4 py-12 text-sm text-slate-500 ring-1 ring-slate-200">
        <Spinner className="size-4 text-indigo-500" />
        Đang phân tích danh sách…
      </div>
    );
  } else {
    previewContent = <PreviewResult preview={preview} filter={filter} onFilter={setFilter} dimmed={stale} />;
  }

  return (
    <Drawer
      open={open}
      onClose={onClose}
      size="xl"
      busy={importMutation.isPending}
      title="Thêm proxy hàng loạt"
      description="Dán danh sách proxy tĩnh hoặc proxy 4G xoay, kiểm tra phần xem trước rồi mới nhập vào kho."
      footer={
        <>
          <p className="mr-auto min-w-0 text-sm text-slate-600">{footerStatus}</p>
          <Button onClick={onClose} disabled={importMutation.isPending}>
            Đóng
          </Button>
          <Button
            variant="primary"
            icon={<Upload className="size-4" aria-hidden />}
            loading={importMutation.isPending}
            disabled={!canImport}
            onClick={submit}
          >
            {importCount > 0 && ready ? `Nhập ${formatNumber(importCount)} proxy` : "Nhập proxy"}
          </Button>
        </>
      }
    >
      <div className="grid gap-x-8 gap-y-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)]">
        <div className="min-w-0 space-y-5">
          <section className="space-y-2">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <label htmlFor={`${id}-text`} className="text-sm font-medium text-slate-700">
                Danh sách proxy
              </label>
              <div className="flex items-center gap-1">
                <span className="mr-1 text-xs text-slate-500 tabular-nums">{formatNumber(lineCount)} dòng proxy</span>
                <Button
                  size="xs"
                  variant="ghost"
                  icon={<FileUp className="size-3.5" aria-hidden />}
                  onClick={() => {
                    fileInput.current?.click();
                  }}
                >
                  Chọn file .txt
                </Button>
                <Button
                  size="xs"
                  variant="ghost"
                  icon={<Eraser className="size-3.5" aria-hidden />}
                  disabled={!draft.text}
                  onClick={() => {
                    setText("");
                    setNotice(null);
                  }}
                >
                  Xoá hết
                </Button>
              </div>
            </div>
            <input
              ref={fileInput}
              type="file"
              accept=".txt,.csv,.list,text/plain"
              multiple
              hidden
              onChange={(event) => {
                const files = [...(event.target.files ?? [])];
                event.target.value = "";
                void addFiles(files);
              }}
            />
            <div
              className="relative"
              onDragOver={(event) => {
                if (hasFiles(event)) {
                  event.preventDefault();
                  setDragging(true);
                }
              }}
              onDragLeave={(event) => {
                if (!(event.relatedTarget instanceof Node && event.currentTarget.contains(event.relatedTarget))) {
                  setDragging(false);
                }
              }}
              onDrop={(event) => {
                if (hasFiles(event)) {
                  event.preventDefault();
                  setDragging(false);
                  void addFiles([...event.dataTransfer.files]);
                }
              }}
            >
              <Textarea
                id={`${id}-text`}
                data-autofocus
                value={draft.text}
                rows={12}
                wrap="off"
                spellCheck={false}
                autoComplete="off"
                placeholder={PLACEHOLDER}
                aria-describedby={`${id}-text-hint`}
                onChange={(event) => {
                  setText(event.target.value);
                }}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
                    event.preventDefault();
                    submit();
                  }
                }}
                className="min-h-64 resize-y font-mono text-[13px] leading-5"
              />
              {dragging ? (
                <div className="pointer-events-none absolute inset-0 flex items-center justify-center gap-2 rounded-lg border-2 border-dashed border-indigo-400 bg-indigo-50/90 text-sm font-medium text-indigo-700">
                  <FileUp className="size-4" aria-hidden />
                  Thả file .txt vào đây
                </div>
              ) : null}
            </div>
            <p id={`${id}-text-hint`} className="text-xs text-slate-500">
              Mỗi dòng một proxy, có thể kéo thả file .txt vào ô trên. Bấm Ctrl + Enter để nhập nhanh.
            </p>
            {notice ? (
              <div
                role="status"
                className="flex gap-2 rounded-lg bg-amber-50 px-3 py-2.5 text-sm text-amber-900 ring-1 ring-amber-200"
              >
                <TriangleAlert className="mt-0.5 size-4 shrink-0 text-amber-600" aria-hidden />
                <p>{notice}</p>
              </div>
            ) : null}
            <FormatHelp />
          </section>

          <Section title="Loại proxy và kết nối" description="Áp dụng cho các dòng không tự ghi tuỳ chọn riêng.">
            <fieldset className="space-y-2">
              <legend className="mb-2 text-sm font-medium text-slate-700">Loại proxy</legend>
              <ChoiceCards<ProxyKind>
                name={`${id}-kind`}
                value={kind}
                onChange={setKind}
                options={[
                  {
                    value: "static",
                    label: "Proxy tĩnh",
                    description: "IP cố định. Mặc định mỗi proxy cho 2 máy dùng chung.",
                    icon: <Server className="size-4" aria-hidden />,
                  },
                  {
                    value: "rotating",
                    label: "Proxy 4G xoay",
                    description: "Đổi IP bằng link hoặc {session}. Mặc định mỗi proxy cho 1 máy.",
                    icon: <Signal className="size-4" aria-hidden />,
                  },
                ]}
              />
              <p className="text-xs text-slate-500">
                Dòng có link đổi IP hoặc <code className="font-mono">{"{session}"}</code> luôn được nhận là 4G xoay, trừ
                khi ghi <code className="font-mono">type=static</code>.
              </p>
            </fieldset>
            <div className="grid gap-4 sm:grid-cols-2">
              <Field
                label="Giao thức"
                htmlFor={`${id}-protocol`}
                hint={
                  protocol === "https"
                    ? "HTTPS là kết nối TLS tới chính proxy. Nhà cung cấp ghi “HTTP/HTTPS” thì chọn HTTP."
                    : "Dùng khi dòng không ghi http:// hay socks5://"
                }
              >
                <Select
                  id={`${id}-protocol`}
                  value={protocol}
                  onChange={(event) => {
                    setProtocol(event.target.value as ProxyProtocol);
                  }}
                >
                  <option value="http">HTTP</option>
                  <option value="socks5">SOCKS5</option>
                  <option value="https">HTTPS (TLS tới proxy)</option>
                </Select>
              </Field>
              <Field
                label="Pool"
                htmlFor={`${id}-pool`}
                error={poolErr}
                hint="Nhóm proxy để máy PC thuê theo nhóm, ví dụ vn-hcm"
              >
                <Input
                  id={`${id}-pool`}
                  list={`${id}-pools`}
                  value={pool}
                  autoComplete="off"
                  aria-invalid={poolErr !== null}
                  onChange={(event) => {
                    setPool(event.target.value);
                  }}
                  className={poolErr ? "ring-rose-400" : undefined}
                />
                <datalist id={`${id}-pools`}>
                  {pools.map((item) => (
                    <option key={item} value={item} />
                  ))}
                </datalist>
              </Field>
              <Field
                label="Số máy dùng chung tối đa"
                htmlFor={`${id}-concurrency`}
                error={concurrencyErr}
                hint="Bỏ trống để dùng mặc định theo loại proxy"
              >
                <Input
                  id={`${id}-concurrency`}
                  inputMode="numeric"
                  autoComplete="off"
                  placeholder="Tự động (tĩnh 2, 4G 1)"
                  value={concurrency}
                  aria-invalid={concurrencyErr !== null}
                  onChange={(event) => {
                    setConcurrency(event.target.value);
                  }}
                  className={concurrencyErr ? "ring-rose-400" : undefined}
                />
              </Field>
            </div>
          </Section>

          <Section
            title="Đổi IP cho proxy 4G"
            description="Áp dụng cho mọi dòng được nhận là proxy 4G xoay, trừ khi dòng ghi interval=, cooldown=, method= riêng."
          >
            <div className="grid gap-4 sm:grid-cols-2">
              <Field
                label="Tự đổi IP theo lịch"
                htmlFor={`${id}-interval`}
                error={intervalErr}
                hint="0 = không tự đổi theo lịch, chỉ đổi khi bấm hoặc khi bị chặn"
              >
                <DurationInput
                  id={`${id}-interval`}
                  value={rotationInterval}
                  onChange={setRotationInterval}
                  invalid={intervalErr !== null}
                />
              </Field>
              <Field
                label="Chờ tối thiểu giữa 2 lần đổi"
                htmlFor={`${id}-cooldown`}
                error={cooldownErr}
                hint="Tránh gọi link đổi IP dồn dập (nhà cung cấp thường giới hạn)"
              >
                <DurationInput
                  id={`${id}-cooldown`}
                  value={cooldown}
                  onChange={setCooldown}
                  invalid={cooldownErr !== null}
                />
              </Field>
              <Field label="Cách gọi link đổi IP" htmlFor={`${id}-method`} hint="Hầu hết nhà cung cấp dùng GET">
                <Select
                  id={`${id}-method`}
                  value={method}
                  onChange={(event) => {
                    setMethod(event.target.value as RotationMethod);
                  }}
                >
                  <option value="GET">GET</option>
                  <option value="POST">POST</option>
                </Select>
              </Field>
            </div>
            <Checkbox
              checked={rotateOnBlock}
              onChange={(event) => {
                setRotateOnBlock(event.target.checked);
              }}
              label="Tự đổi IP khi máy PC báo bị chặn"
              description="Máy PC trả proxy kèm kết quả “bị chặn” thì hệ thống đổi IP trước khi cho thuê tiếp."
            />
          </Section>

          <Section title="Khi proxy đã có trong kho" description="So khớp theo giao thức, host, cổng và tên đăng nhập.">
            <ChoiceCards<DuplicatePolicy>
              name={`${id}-duplicate`}
              value={onDuplicate}
              onChange={setOnDuplicate}
              options={[
                { value: "skip", label: "Bỏ qua, giữ proxy cũ", description: "Không thay đổi gì proxy đang có." },
                {
                  value: "update",
                  label: "Cập nhật proxy cũ",
                  description:
                    "Ghi đè mật khẩu, link đổi IP, pool, số máy dùng chung và thiết lập đổi IP. Đổi mật khẩu thì proxy được kiểm tra lại từ đầu.",
                },
              ]}
            />
            <Checkbox
              checked={checkAfterImport}
              onChange={(event) => {
                setCheckAfterImport(event.target.checked);
              }}
              label="Kiểm tra sống/chết ngay sau khi nhập"
              description="Chạy nền, kết quả hiện dần trong bảng proxy."
            />
          </Section>
        </div>

        <section
          aria-labelledby={`${id}-preview-title`}
          className="min-w-0 space-y-3 lg:sticky lg:top-0 lg:self-start"
        >
          <div className="flex items-center justify-between gap-2">
            <h3 id={`${id}-preview-title`} className="text-sm font-semibold text-slate-900">
              Xem trước
            </h3>
            {previewQuery.isFetching ? (
              <span className="flex items-center gap-1.5 text-xs text-slate-500">
                <Spinner className="size-3.5" />
                Đang phân tích…
              </span>
            ) : null}
          </div>
          {previewContent}
        </section>
      </div>
    </Drawer>
  );
}
