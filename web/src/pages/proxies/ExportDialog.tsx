import { useMutation } from "@tanstack/react-query";
import { Download, TriangleAlert } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { Button } from "../../components/ui/Button";
import { Checkbox, ChoiceCards } from "../../components/ui/form";
import { Modal } from "../../components/ui/Overlay";
import { errorMessage } from "../../lib/api";
import { saveBlob } from "../../lib/download";
import { formatNumber } from "../../lib/format";
import { proxiesApi, type ExportQuery } from "../../lib/proxies";
import { countFilters } from "../../lib/proxy-filters";
import type { ExportFormat, ProxyFilters } from "../../lib/types";

type Scope = "filtered" | "all";

interface ExportDialogProps {
  filters: ProxyFilters;
  filteredCount: number | undefined;
  totalCount: number | undefined;
  onClose: () => void;
}

function countLabel(count: number | undefined): string {
  return count === undefined ? "đang đếm…" : `${formatNumber(count)} proxy`;
}

export function ExportDialog({ filters, filteredCount, totalCount, onClose }: ExportDialogProps) {
  const hasFilters = countFilters(filters) > 0;
  const [scope, setScope] = useState<Scope>(hasFilters ? "filtered" : "all");
  const [format, setFormat] = useState<ExportFormat>("url");
  const [withOptions, setWithOptions] = useState(true);

  const exportMutation = useMutation({
    mutationFn: (query: ExportQuery) => proxiesApi.export(query),
    onSuccess: ({ blob, filename }) => {
      const name = filename ?? "proxies.txt";
      saveBlob(blob, name);
      toast.success("Đã tải file danh sách proxy", { description: name });
      onClose();
    },
  });

  const count = scope === "filtered" ? filteredCount : totalCount;
  const empty = count === 0;

  return (
    <Modal
      open
      onClose={onClose}
      title="Xuất danh sách proxy"
      description="Tải file .txt, mỗi dòng một proxy, dùng để sao lưu hoặc nhập lại."
      size="md"
      busy={exportMutation.isPending}
      footer={
        <>
          <Button onClick={onClose} disabled={exportMutation.isPending}>
            Huỷ
          </Button>
          <Button
            variant="primary"
            icon={<Download className="size-4" aria-hidden />}
            loading={exportMutation.isPending}
            disabled={empty}
            onClick={() => {
              exportMutation.mutate({
                ...(scope === "filtered" ? filters : {}),
                format,
                with_options: withOptions,
              });
            }}
          >
            Tải file{count === undefined || empty ? "" : ` (${formatNumber(count)} proxy)`}
          </Button>
        </>
      }
    >
      <div className="space-y-5">
        {hasFilters ? (
          <fieldset className="space-y-2">
            <legend className="text-sm font-medium text-slate-700">Xuất những proxy nào</legend>
            <ChoiceCards<Scope>
              name="export-scope"
              value={scope}
              onChange={setScope}
              options={[
                {
                  value: "filtered",
                  label: "Theo bộ lọc đang dùng",
                  description: countLabel(filteredCount),
                },
                { value: "all", label: "Toàn bộ kho proxy", description: countLabel(totalCount) },
              ]}
            />
          </fieldset>
        ) : null}

        <fieldset className="space-y-2">
          <legend className="text-sm font-medium text-slate-700">Định dạng mỗi dòng</legend>
          <ChoiceCards<ExportFormat>
            name="export-format"
            value={format}
            onChange={setFormat}
            options={[
              {
                value: "url",
                label: "Dạng URL",
                description: (
                  <>
                    <code className="font-mono">http://user:pass@1.2.3.4:8080</code>
                    <br />
                    Ghi rõ giao thức, hầu hết phần mềm đều đọc được.
                  </>
                ),
              },
              {
                value: "colon",
                label: "Dạng host:port:user:pass",
                description: (
                  <>
                    <code className="font-mono">1.2.3.4:8080:user:pass</code>
                    <br />
                    Không ghi giao thức; dòng có ký tự đặc biệt sẽ tự dùng dạng URL.
                  </>
                ),
              },
            ]}
          />
        </fieldset>

        <Checkbox
          checked={withOptions}
          onChange={(event) => {
            setWithOptions(event.target.checked);
          }}
          label="Kèm link đổi IP và tuỳ chọn"
          description="Thêm |link đổi IP|type=4g|pool=…|concurrency=… vào cuối dòng để nhập lại vào CommentScope vẫn giữ nguyên thiết lập (và giao thức SOCKS5 khi dùng dạng host:port:user:pass)."
        />

        <div className="flex gap-2.5 rounded-lg bg-amber-50 px-3 py-2.5 text-sm text-amber-900 ring-1 ring-amber-200">
          <TriangleAlert className="mt-0.5 size-4 shrink-0 text-amber-600" aria-hidden />
          <p>File chứa mật khẩu proxy và link đổi IP ở dạng chữ thường. Chỉ lưu ở nơi an toàn, đừng gửi công khai.</p>
        </div>

        {empty ? <p className="text-sm text-slate-500">Không có proxy nào để xuất.</p> : null}
        {exportMutation.isError ? (
          <p role="alert" className="rounded-lg bg-rose-50 px-3 py-2 text-sm text-rose-700 ring-1 ring-rose-200">
            {errorMessage(exportMutation.error)}
          </p>
        ) : null}
      </div>
    </Modal>
  );
}
