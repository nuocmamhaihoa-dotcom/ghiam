import clsx from "clsx";
import { X } from "lucide-react";
import { useEffect, useId, useRef, type ReactNode } from "react";
import { createPortal } from "react-dom";

const openStack: string[] = [];

const FOCUSABLE = [
  "a[href]",
  "button:not([disabled])",
  "input:not([disabled]):not([type='hidden'])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  "[tabindex]:not([tabindex='-1'])",
].join(",");

type Size = "sm" | "md" | "lg" | "xl";

const MODAL_SIZES: Record<Size, string> = { sm: "max-w-md", md: "max-w-lg", lg: "max-w-2xl", xl: "max-w-4xl" };
const DRAWER_SIZES: Record<Size, string> = { sm: "max-w-md", md: "max-w-xl", lg: "max-w-3xl", xl: "max-w-6xl" };

interface OverlayProps {
  open: boolean;
  onClose: () => void;
  title: ReactNode;
  description?: ReactNode;
  footer?: ReactNode;
  size?: Size;
  /** Đang gửi dữ liệu: không cho đóng bằng Esc, nút X hay bấm ra ngoài. */
  busy?: boolean;
  children: ReactNode;
}

interface FrameProps extends Omit<OverlayProps, "open"> {
  variant: "modal" | "drawer";
}

function OverlayFrame({ variant, onClose, title, description, footer, size = "md", busy = false, children }: FrameProps) {
  const id = useId();
  const titleId = `${id}-title`;
  const panelRef = useRef<HTMLDivElement>(null);
  const requestClose = useRef<(() => void) | null>(null);

  useEffect(() => {
    requestClose.current = () => {
      if (!busy) {
        onClose();
      }
    };
  }, [busy, onClose]);

  useEffect(() => {
    const panel = panelRef.current;
    if (!panel) {
      return;
    }
    openStack.push(id);
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    (panel.querySelector<HTMLElement>("[data-autofocus]") ?? panel).focus();
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";

    function onKeyDown(event: KeyboardEvent) {
      if (openStack.at(-1) !== id || !panel) {
        return;
      }
      if (event.key === "Escape") {
        event.preventDefault();
        requestClose.current?.();
        return;
      }
      if (event.key !== "Tab") {
        return;
      }
      const items = [...panel.querySelectorAll<HTMLElement>(FOCUSABLE)].filter((item) => item.offsetParent !== null);
      const first = items[0];
      const last = items.at(-1);
      if (!first || !last) {
        event.preventDefault();
        return;
      }
      const active = document.activeElement;
      if (!panel.contains(active)) {
        event.preventDefault();
        (event.shiftKey ? last : first).focus();
        return;
      }
      if (event.shiftKey && (active === first || active === panel)) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && active === last) {
        event.preventDefault();
        first.focus();
      }
    }

    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      openStack.splice(openStack.indexOf(id), 1);
      document.body.style.overflow = previousOverflow;
      previousFocus?.focus();
    };
  }, [id]);

  const header = (
    <div className="flex items-start justify-between gap-4 border-b border-slate-200 px-5 py-4">
      <div className="min-w-0">
        <h2 id={titleId} className="text-base font-semibold text-slate-900">
          {title}
        </h2>
        {description ? <div className="mt-1 text-sm text-slate-500">{description}</div> : null}
      </div>
      <button
        type="button"
        onClick={() => {
          requestClose.current?.();
        }}
        disabled={busy}
        aria-label="Đóng"
        className="-m-1.5 rounded-md p-1.5 text-slate-400 hover:bg-slate-100 hover:text-slate-600 disabled:opacity-40"
      >
        <X className="size-5" aria-hidden />
      </button>
    </div>
  );

  const footerBar = footer ? (
    <div className="flex flex-wrap items-center justify-end gap-2 border-t border-slate-200 bg-slate-50 px-5 py-3">
      {footer}
    </div>
  ) : null;

  const content =
    variant === "drawer" ? (
      <div className="fixed inset-0 z-40">
        <div
          aria-hidden
          className="fixed inset-0 bg-slate-900/40 motion-safe:animate-fade-in"
          onMouseDown={() => {
            requestClose.current?.();
          }}
        />
        <div
          ref={panelRef}
          role="dialog"
          aria-modal="true"
          aria-labelledby={titleId}
          tabIndex={-1}
          className={clsx(
            "fixed inset-y-0 right-0 flex w-full flex-col bg-white shadow-2xl outline-none motion-safe:animate-slide-in",
            DRAWER_SIZES[size],
          )}
        >
          {header}
          <div className="flex-1 overflow-y-auto px-5 py-5">{children}</div>
          {footerBar}
        </div>
      </div>
    ) : (
      <div className="fixed inset-0 z-40">
        <div aria-hidden className="fixed inset-0 bg-slate-900/40 motion-safe:animate-fade-in" />
        <div
          className="fixed inset-0 flex items-start justify-center overflow-y-auto p-4 sm:items-center sm:p-6"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) {
              requestClose.current?.();
            }
          }}
        >
          <div
            ref={panelRef}
            role="dialog"
            aria-modal="true"
            aria-labelledby={titleId}
            tabIndex={-1}
            className={clsx(
              "w-full overflow-hidden rounded-xl bg-white shadow-2xl outline-none motion-safe:animate-pop-in",
              MODAL_SIZES[size],
            )}
          >
            {header}
            <div className="px-5 py-4">{children}</div>
            {footerBar}
          </div>
        </div>
      </div>
    );

  return createPortal(content, document.body);
}

export function Modal({ open, ...props }: OverlayProps) {
  return open ? <OverlayFrame variant="modal" {...props} /> : null;
}

export function Drawer({ open, ...props }: OverlayProps) {
  return open ? <OverlayFrame variant="drawer" {...props} /> : null;
}
