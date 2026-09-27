export type ProxyKind = "static" | "rotating";
export type ProxyProtocol = "http" | "https" | "socks5";
export type ProxyHealth = "unchecked" | "alive" | "dead";
export type RotationState = "idle" | "pending" | "rotating";
export type RotationMode = "none" | "url" | "session" | "provider";
export type RotationMethod = "GET" | "POST";
export type DuplicatePolicy = "skip" | "update";

export interface LoginResult {
  access_token: string;
  token_type: "bearer";
  expires_at: string;
  username: string;
}

export interface ProxyItem {
  id: number;
  kind: ProxyKind;
  protocol: ProxyProtocol;
  host: string;
  port: number;
  username: string;
  has_password: boolean;
  display: string;
  pool: string;
  note: string | null;
  enabled: boolean;
  max_concurrency: number;
  active_leases: number;

  rotation_mode: RotationMode;
  rotation_url: string | null;
  rotation_method: RotationMethod;
  rotation_interval_sec: number;
  rotation_cooldown_sec: number;
  rotate_on_block: boolean;
  rotation_state: RotationState;
  cooldown_remaining_sec: number;
  last_rotated_at: string | null;
  last_rotation_attempt_at: string | null;
  last_rotation_ok: boolean | null;
  last_rotation_message: string | null;
  rotation_count: number;

  health: ProxyHealth;
  check_in_progress: boolean;
  last_checked_at: string | null;
  last_check_error: string | null;
  latency_ms: number | null;
  exit_ip: string | null;
  country: string | null;
  isp: string | null;

  success_count: number;
  failure_count: number;
  consecutive_failures: number;
  quarantined_until: string | null;
  last_used_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface ProxyDetail extends ProxyItem {
  rotation_url_full: string | null;
}

export interface ProxyList {
  items: ProxyItem[];
  total: number;
  page: number;
  page_size: number;
}

export interface ProxyStats {
  total: number;
  enabled: number;
  alive: number;
  dead: number;
  unchecked: number;
  static: number;
  rotating: number;
  leased_proxies: number;
  active_leases: number;
  rotating_now: number;
  rotation_pending: number;
  quarantined: number;
  pools: { pool: string; total: number }[];
}

export interface ProxyFilters {
  kind?: ProxyKind;
  health?: ProxyHealth;
  pool?: string;
  enabled?: boolean;
  rotation_state?: RotationState;
  leased?: boolean;
  quarantined?: boolean;
  q?: string;
}

export type ProxySort = "-id" | "id" | "latency" | "-latency" | "last_checked" | "-last_checked" | "last_used" | "host";

export interface ProxyListParams extends ProxyFilters {
  page: number;
  page_size: number;
  sort: ProxySort;
}

export interface ImportDefaults {
  kind: ProxyKind;
  protocol: ProxyProtocol;
  pool: string;
  max_concurrency: number | null;
  rotation_interval_sec: number;
  rotation_cooldown_sec: number;
  rotation_method: RotationMethod;
  rotate_on_block: boolean;
}

export interface ImportPreviewRequest {
  text: string;
  defaults: ImportDefaults;
  on_duplicate: DuplicatePolicy;
}

export interface ImportRequest extends ImportPreviewRequest {
  check_after_import: boolean;
}

export type PreviewStatus = "new" | "update" | "duplicate" | "invalid";

export interface PreviewLine {
  line_no: number;
  status: PreviewStatus;
  display: string;
  kind: ProxyKind | null;
  protocol: ProxyProtocol | null;
  pool: string | null;
  rotation_mode: RotationMode | null;
  rotation_url: string | null;
  max_concurrency: number | null;
  error: string | null;
  warnings: string[];
  duplicate_of_line: number | null;
  existing_id: number | null;
}

export interface PreviewSummary {
  total: number;
  new: number;
  update: number;
  duplicate: number;
  invalid: number;
  with_warnings: number;
  static: number;
  rotating: number;
}

export interface ImportPreview {
  summary: PreviewSummary;
  lines: PreviewLine[];
  truncated: boolean;
}

export interface ImportResult {
  created: number;
  updated: number;
  skipped: number;
  invalid: number;
  total: number;
  check_scheduled: number;
  errors: PreviewLine[];
}

export type BulkAction = "enable" | "disable" | "delete" | "check" | "rotate" | "set_pool" | "reset_stats";

export interface BulkRequest {
  action: BulkAction;
  ids?: number[];
  filter?: ProxyFilters;
  pool?: string;
}

export interface BulkResult {
  action: BulkAction;
  matched: number;
  affected: number;
  message: string;
}

export interface CheckResult {
  ok: boolean;
  latency_ms: number | null;
  exit_ip: string | null;
  country: string | null;
  isp: string | null;
  error: string | null;
  proxy: ProxyItem | null;
}

export type RotateStatus = "rotated" | "failed" | "started" | "pending" | "skipped";

export interface RotateResult {
  status: RotateStatus;
  message: string;
  proxy: ProxyItem | null;
}

export interface ProxyUpdate {
  kind?: ProxyKind;
  pool?: string;
  note?: string;
  enabled?: boolean;
  max_concurrency?: number;
  username?: string;
  password?: string;
  rotation_url?: string;
  rotation_method?: RotationMethod;
  rotation_interval_sec?: number;
  rotation_cooldown_sec?: number;
  rotate_on_block?: boolean;
}

export type ExportFormat = "url" | "colon";
