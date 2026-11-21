import { Bot, Headset, UserRound } from "lucide-react";

type ChatRole = "user" | "assistant" | "admin";

const AVATAR_META: Record<
  ChatRole,
  { label: string; className: string; Icon: typeof Bot }
> = {
  user: { label: "顾客", className: "chat-avatar-customer", Icon: UserRound },
  assistant: { label: "AI 客服", className: "chat-avatar-assistant", Icon: Bot },
  admin: { label: "人工客服", className: "chat-avatar-admin", Icon: Headset },
};

export function ChatAvatar({ role }: { role: ChatRole }) {
  const { label, className, Icon } = AVATAR_META[role];
  return (
    <span
      className={`chat-avatar ${className}`}
      role="img"
      aria-label={label}
      title={label}
    >
      <Icon size={17} strokeWidth={1.9} aria-hidden="true" />
    </span>
  );
}
