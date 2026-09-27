export const MAX_IMPORT_TEXT_LENGTH = 8_000_000;
export const MAX_IMPORT_FILE_BYTES = 8 * 1024 * 1024;

// Cùng tập ký tự xuống dòng với str.splitlines() của Python để số dòng khớp với máy chủ.
// eslint-disable-next-line no-control-regex
const LINE_BREAK_RE = /\r\n|[\n\r\v\f\x1c-\x1e\x85\u2028\u2029]/;

export function splitLines(text: string): string[] {
  if (!text) {
    return [];
  }
  const lines = text.split(LINE_BREAK_RE);
  if (lines.at(-1) === "") {
    lines.pop();
  }
  return lines;
}

function isProxyLine(raw: string): boolean {
  const line = raw.trim();
  return line !== "" && !line.startsWith("#") && !line.startsWith("//");
}

/** Số dòng sẽ được phân tích: bỏ dòng trống và dòng ghi chú bắt đầu bằng # hoặc //. */
export function countProxyLines(text: string): number {
  return splitLines(text).filter(isProxyLine).length;
}

/** Lấy lại nguyên văn các dòng theo số dòng (đếm từ 1) mà máy chủ báo về. */
export function pickLines(text: string, lineNumbers: readonly number[]): string[] {
  const lines = splitLines(text);
  return lineNumbers.flatMap((lineNo) => {
    const line = lines[lineNo - 1];
    return line === undefined ? [] : [line];
  });
}

/** Ghép thêm nội dung vào cuối ô nhập, luôn bắt đầu ở dòng mới. */
export function appendText(current: string, addition: string): string {
  if (!current.trim()) {
    return addition;
  }
  return /[\r\n]$/.test(current) ? `${current}${addition}` : `${current}\n${addition}`;
}
