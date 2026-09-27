/** Trả về false khi trình duyệt chặn clipboard, ví dụ trang mở bằng http:// thay vì https://. */
export async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}
