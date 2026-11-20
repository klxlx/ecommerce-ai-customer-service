import { Fragment, useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Headset, LogOut, Send, UserRound } from "lucide-react";
import {
  claimHandoff,
  listHandoffMessages,
  listLiveSessions,
  releaseHandoff,
  sendHandoffReply,
  type HandoffLiveSession,
} from "../../api/admin";
import type { ChatMessage } from "../../api/chat";
import { useAuth } from "../../auth/AuthContext";

const ROLE_LABEL: Record<string, string> = {
  user: "顾客",
  assistant: "智能客服",
  admin: "人工客服",
  system: "系统提示",
};

function formatClock(value: string | null): string {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleTimeString("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
  });
}

function waitedMinutes(createdAt: string): number {
  const date = new Date(createdAt);
  if (Number.isNaN(date.getTime())) return 0;
  return Math.max(0, Math.floor((Date.now() - date.getTime()) / 60000));
}

const TIME_DIVIDER_MS = 5 * 60 * 1000;

function shouldShowDivider(messages: ChatMessage[], index: number): boolean {
  if (index === 0) return false;
  const previous = new Date(messages[index - 1].created_at).getTime();
  const current = new Date(messages[index].created_at).getTime();
  if (Number.isNaN(previous) || Number.isNaN(current)) return false;
  return current - previous >= TIME_DIVIDER_MS;
}

function formatDivider(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return `${date.getMonth() + 1}月${date.getDate()}日 ${formatClock(value)}`;
}

export function LiveConsolePage() {
  const { user } = useAuth();
  const queryClient = useQueryClient();
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [draft, setDraft] = useState("");
  const [notice, setNotice] = useState("");
  const [releaseOpen, setReleaseOpen] = useState(false);
  const [releaseNote, setReleaseNote] = useState("");
  const bottomRef = useRef<HTMLDivElement | null>(null);

  const { data: sessions = [], isLoading } = useQuery({
    queryKey: ["admin", "live"],
    queryFn: listLiveSessions,
    refetchInterval: 3000,
  });

  const selected =
    sessions.find((session) => session.id === selectedId) ?? null;

  const { data: messages = [], isLoading: messagesLoading } = useQuery({
    queryKey: ["admin", "live", selectedId, "messages"],
    queryFn: () => listHandoffMessages(selectedId as number),
    enabled: selectedId !== null,
    refetchInterval: 2000,
  });

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: "end" });
  }, [messages.length, selectedId]);

  const claimMutation = useMutation({
    mutationFn: (id: number) => claimHandoff(id),
    onSuccess: () => {
      setNotice("已接入会话，可以开始回复顾客。");
      queryClient.invalidateQueries({ queryKey: ["admin", "live"] });
      queryClient.invalidateQueries({ queryKey: ["admin", "handoffs"] });
    },
  });

  const replyMutation = useMutation({
    mutationFn: (content: string) => sendHandoffReply(selectedId as number, content),
    onSuccess: () => {
      setDraft("");
      queryClient.invalidateQueries({
        queryKey: ["admin", "live", selectedId, "messages"],
      });
      queryClient.invalidateQueries({ queryKey: ["admin", "live"] });
    },
  });

  const releaseMutation = useMutation({
    mutationFn: (note: string) => releaseHandoff(selectedId as number, note),
    onSuccess: () => {
      setReleaseOpen(false);
      setReleaseNote("");
      setSelectedId(null);
      setNotice("已退出人工回复，会话已交还智能体。");
      queryClient.invalidateQueries({ queryKey: ["admin", "live"] });
      queryClient.invalidateQueries({ queryKey: ["admin", "handoffs"] });
    },
  });

  const mine =
    selected !== null &&
    selected.status === "in_progress" &&
    selected.assignee_id === user?.id;
  const claimedByOther =
    selected !== null &&
    selected.status === "in_progress" &&
    selected.assignee_id !== user?.id;

  const waiting = sessions.filter((session) => session.status === "open");
  const serving = sessions.filter((session) => session.status === "in_progress");

  const openSession = (session: HandoffLiveSession) => {
    setSelectedId(session.id);
    setDraft("");
    setNotice("");
    claimMutation.reset();
    replyMutation.reset();
  };

  return (
    <div className="live-console">
      <div className="live-heading">
        <div>
          <h2 className="admin-title">人工回复</h2>
          <div className="conversation-time">
            待接入 {waiting.length} · 进行中 {serving.length}
          </div>
        </div>
      </div>

      {notice && (
        <div className="success-text live-notice" role="status">
          {notice}
        </div>
      )}

      <div className="live-grid">
        <aside className="live-list" aria-label="人工会话列表">
          <SessionGroup
            title="待接入"
            empty="暂无等待接入的顾客"
            sessions={waiting}
            selectedId={selectedId}
            onSelect={openSession}
            currentUserId={user?.id}
          />
          <SessionGroup
            title="进行中"
            empty="暂无正在接待的会话"
            sessions={serving}
            selectedId={selectedId}
            onSelect={openSession}
            currentUserId={user?.id}
          />
        </aside>

        <section className="live-detail" aria-label="会话窗口">
          {isLoading && (
            <div className="state-box">
              <div className="spinner" />
            </div>
          )}
          {!isLoading && !selected && (
            <div className="live-empty">
              <Headset size={36} />
              <div>从左侧选择一个会话接入</div>
              <div className="conversation-time">
                顾客转人工后会出现在「待接入」列表
              </div>
            </div>
          )}

          {selected && (
            <>
              <header className="live-detail-head">
                <div>
                  <div className="live-customer">
                    <UserRound size={16} />
                    <strong>{selected.username || `顾客 #${selected.user_id}`}</strong>
                    <span className={`pill pill-${selected.status}`}>
                      {selected.status === "open" ? "待接入" : "进行中"}
                    </span>
                  </div>
                  <div className="conversation-time">
                    工单 #{selected.id} · 会话 #{selected.conversation_id} · 等待
                    {" "}{waitedMinutes(selected.created_at)} 分钟
                  </div>
                  {selected.summary && (
                    <div className="live-summary">{selected.summary}</div>
                  )}
                </div>
                <div className="live-head-actions">
                  {selected.status === "open" && (
                    <button
                      className="btn btn-primary"
                      disabled={claimMutation.isPending}
                      onClick={() => claimMutation.mutate(selected.id)}
                    >
                      <Headset size={16} />
                      {claimMutation.isPending ? "接入中…" : "接入会话"}
                    </button>
                  )}
                  {mine && (
                    <button
                      className="btn btn-secondary"
                      onClick={() => {
                        setReleaseNote("");
                        releaseMutation.reset();
                        setReleaseOpen(true);
                      }}
                    >
                      <LogOut size={16} />
                      退出人工回复
                    </button>
                  )}
                </div>
              </header>

              {claimMutation.error && (
                <div className="error-text live-alert" role="alert">
                  {claimMutation.error instanceof Error
                    ? claimMutation.error.message
                    : "接入失败，请重试"}
                </div>
              )}
              {claimedByOther && (
                <div className="live-alert live-alert-muted">
                  该会话由 {selected.assignee_name || "其他客服"} 接入，你当前为只读。
                </div>
              )}

              <div className="live-messages" aria-live="polite">
                {messagesLoading && messages.length === 0 && (
                  <div className="state-box">
                    <div className="spinner" />
                  </div>
                )}
                {messages.map((message, index) => (
                  <Fragment key={message.id}>
                    {shouldShowDivider(messages, index) && (
                      <div className="chat-time-divider">
                        {formatDivider(message.created_at)}
                      </div>
                    )}
                    <MessageRow message={message} />
                  </Fragment>
                ))}
                {!messagesLoading && messages.length === 0 && (
                  <div className="conversation-time">（暂无会话记录）</div>
                )}
                <div ref={bottomRef} />
              </div>

              {mine ? (
                <div className="live-composer">
                  <textarea
                    className="input"
                    rows={3}
                    maxLength={4000}
                    placeholder="以人工客服身份回复顾客…"
                    value={draft}
                    onChange={(event) => setDraft(event.target.value)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter" && !event.shiftKey) {
                        event.preventDefault();
                        if (draft.trim() && !replyMutation.isPending) {
                          replyMutation.mutate(draft.trim());
                        }
                      }
                    }}
                  />
                  <div className="live-composer-actions">
                    {replyMutation.error && (
                      <span className="error-text" role="alert">
                        发送失败，请重试
                      </span>
                    )}
                    <button
                      className="btn btn-primary"
                      disabled={!draft.trim() || replyMutation.isPending}
                      onClick={() => replyMutation.mutate(draft.trim())}
                    >
                      <Send size={16} />
                      {replyMutation.isPending ? "发送中…" : "发送"}
                    </button>
                  </div>
                </div>
              ) : (
                <div className="live-composer live-composer-locked">
                  {selected.status === "open"
                    ? "接入会话后即可回复顾客。"
                    : `会话由 ${selected.assignee_name || "其他客服"} 接待中，暂不可回复。`}
                </div>
              )}
            </>
          )}
        </section>
      </div>

      {releaseOpen && (
        <div className="modal-backdrop" role="presentation">
          <div
            className="modal live-release-modal"
            role="dialog"
            aria-modal="true"
            aria-label="退出人工回复"
          >
            <h3>退出人工回复</h3>
            <p className="conversation-time">
              填写处理结论后，会话将交还智能体，顾客可继续提问。
            </p>
            <textarea
              className="input"
              rows={4}
              maxLength={500}
              placeholder="例如：已核实订单破损，已登记补发，顾客同意。"
              value={releaseNote}
              onChange={(event) => setReleaseNote(event.target.value)}
            />
            {releaseMutation.error && (
              <div className="error-text" role="alert">
                {releaseMutation.error instanceof Error
                  ? releaseMutation.error.message
                  : "交还失败，请重试"}
              </div>
            )}
            <div className="modal-actions">
              <button
                className="btn btn-ghost"
                onClick={() => setReleaseOpen(false)}
              >
                取消
              </button>
              <button
                className="btn btn-primary"
                disabled={!releaseNote.trim() || releaseMutation.isPending}
                onClick={() => releaseMutation.mutate(releaseNote.trim())}
              >
                {releaseMutation.isPending ? "交还中…" : "确认交还智能体"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function SessionGroup({
  title,
  empty,
  sessions,
  selectedId,
  onSelect,
  currentUserId,
}: {
  title: string;
  empty: string;
  sessions: HandoffLiveSession[];
  selectedId: number | null;
  onSelect: (session: HandoffLiveSession) => void;
  currentUserId?: number;
}) {
  return (
    <section className="live-group">
      <h3>
        {title}
        <span className="live-count">{sessions.length}</span>
      </h3>
      {sessions.length === 0 && (
        <div className="conversation-time">{empty}</div>
      )}
      {sessions.map((session) => {
        const mine =
          session.status === "in_progress" &&
          session.assignee_id === currentUserId;
        return (
          <button
            key={session.id}
            className={
              session.id === selectedId
                ? "live-item active"
                : "live-item"
            }
            onClick={() => onSelect(session)}
          >
            <div className="live-item-top">
              <strong>{session.username || `顾客 #${session.user_id}`}</strong>
              <span className="conversation-time">
                {waitedMinutes(session.created_at)} 分钟
              </span>
            </div>
            <div className="live-item-preview">
              {session.last_message || session.summary || "（暂无消息）"}
            </div>
            <div className="live-item-meta">
              <span className={`pill pill-${session.status}`}>
                {mine
                  ? "我接待中"
                  : session.status === "in_progress"
                    ? session.assignee_name || "处理中"
                    : "待接入"}
              </span>
              <span className="conversation-time">
                {formatClock(session.last_message_at)}
              </span>
            </div>
          </button>
        );
      })}
    </section>
  );
}

function MessageRow({ message }: { message: ChatMessage }) {
  if (message.role === "system") {
    return <div className="live-system">{message.content}</div>;
  }
  return (
    <div className={`message ${message.role}`}>
      <div className="message-meta">{ROLE_LABEL[message.role] || message.role}</div>
      <div className="message-bubble">{message.content}</div>
      <div className="message-time">{formatClock(message.created_at)}</div>
    </div>
  );
}
