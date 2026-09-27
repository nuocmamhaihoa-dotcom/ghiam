import { Hourglass, Server, Signal } from "lucide-react";

import { Badge, type Tone } from "../../components/ui/Badge";
import { Spinner } from "../../components/ui/Spinner";
import { HEALTH_LABELS, KIND_LABELS } from "../../lib/format";
import type { ProxyHealth, ProxyKind, RotationState } from "../../lib/types";

const HEALTH_TONES: Record<ProxyHealth, Tone> = { alive: "green", dead: "red", unchecked: "gray" };
const HEALTH_DOTS: Record<ProxyHealth, string> = {
  alive: "bg-emerald-500",
  dead: "bg-rose-500",
  unchecked: "bg-slate-400",
};

export function HealthBadge({ health, checking = false }: { health: ProxyHealth; checking?: boolean }) {
  if (checking) {
    return (
      <Badge tone="blue" icon={<Spinner className="size-3" />}>
        Đang kiểm tra
      </Badge>
    );
  }
  return (
    <Badge tone={HEALTH_TONES[health]} icon={<span className={`size-1.5 rounded-full ${HEALTH_DOTS[health]}`} />}>
      {HEALTH_LABELS[health]}
    </Badge>
  );
}

export function KindBadge({ kind }: { kind: ProxyKind }) {
  return kind === "rotating" ? (
    <Badge tone="violet" icon={<Signal className="size-3" aria-hidden />}>
      {KIND_LABELS.rotating}
    </Badge>
  ) : (
    <Badge tone="gray" icon={<Server className="size-3" aria-hidden />}>
      {KIND_LABELS.static}
    </Badge>
  );
}

export function RotationStateBadge({ state }: { state: RotationState }) {
  if (state === "rotating") {
    return (
      <Badge tone="blue" icon={<Spinner className="size-3" />}>
        Đang đổi IP
      </Badge>
    );
  }
  if (state === "pending") {
    return (
      <Badge tone="amber" icon={<Hourglass className="size-3" aria-hidden />}>
        Chờ đổi IP
      </Badge>
    );
  }
  return <Badge tone="green">Sẵn sàng</Badge>;
}
