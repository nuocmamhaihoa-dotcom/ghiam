import clsx, { type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

/**
 * Ghép class và để class truyền vào sau thắng class mặc định cùng thuộc tính
 * (Tailwind v4 xếp các utility cùng thuộc tính theo bảng chữ cái, không theo thứ tự trong chuỗi class).
 */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}
