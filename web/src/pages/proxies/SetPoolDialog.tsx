import { useId, useState } from "react";

import { Button } from "../../components/ui/Button";
import { Field, Input } from "../../components/ui/form";
import { Modal } from "../../components/ui/Overlay";
import { formatNumber } from "../../lib/format";
import { normalizePool, poolError } from "../../lib/validation";

interface SetPoolDialogProps {
  count: number;
  pools: readonly string[];
  loading: boolean;
  onSubmit: (pool: string) => void;
  onClose: () => void;
}

export function SetPoolDialog({ count, pools, loading, onSubmit, onClose }: SetPoolDialogProps) {
  const id = useId();
  const [pool, setPool] = useState("");
  const [submitted, setSubmitted] = useState(false);
  const error = poolError(pool);

  return (
    <Modal
      open
      onClose={onClose}
      title="Chuyển pool"
      description={`Chuyển ${formatNumber(count)} proxy đã chọn sang pool khác. Máy PC thuê proxy theo pool sẽ dùng pool mới từ lượt thuê kế tiếp.`}
      size="sm"
      busy={loading}
      footer={
        <>
          <Button onClick={onClose} disabled={loading}>
            Huỷ
          </Button>
          <Button type="submit" form={`${id}-form`} variant="primary" loading={loading}>
            Chuyển pool
          </Button>
        </>
      }
    >
      <form
        id={`${id}-form`}
        noValidate
        onSubmit={(event) => {
          event.preventDefault();
          setSubmitted(true);
          if (!error) {
            onSubmit(normalizePool(pool));
          }
        }}
      >
        <Field
          label="Pool mới"
          htmlFor={`${id}-pool`}
          error={submitted ? error : null}
          hint="Chọn pool có sẵn hoặc gõ tên mới, ví dụ vn-hcm"
        >
          <Input
            id={`${id}-pool`}
            list={`${id}-pools`}
            value={pool}
            autoComplete="off"
            data-autofocus
            aria-invalid={submitted && error !== null}
            onChange={(event) => {
              setPool(event.target.value);
            }}
          />
          <datalist id={`${id}-pools`}>
            {pools.map((item) => (
              <option key={item} value={item} />
            ))}
          </datalist>
        </Field>
      </form>
    </Modal>
  );
}
