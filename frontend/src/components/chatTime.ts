import type { ChatMessage } from "../api/chat";

const TIME_DIVIDER_MS = 5 * 60 * 1000;

export function shouldShowTimeDivider(
  messages: ChatMessage[],
  index: number,
): boolean {
  if (index === 0) return false;
  const previous = new Date(messages[index - 1].created_at).getTime();
  const current = new Date(messages[index].created_at).getTime();
  if (Number.isNaN(previous) || Number.isNaN(current)) return false;
  return current - previous >= TIME_DIVIDER_MS;
}

export function formatTimeDivider(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  const now = new Date();
  const clock = date.toLocaleTimeString("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
  });
  if (date.toDateString() === now.toDateString()) {
    return clock;
  }
  return `${date.getMonth() + 1}月${date.getDate()}日 ${clock}`;
}
