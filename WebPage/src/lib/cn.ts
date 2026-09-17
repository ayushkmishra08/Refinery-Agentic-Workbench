import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

/** Tailwind-aware class joiner: later classes win over earlier ones in the same group. */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}
