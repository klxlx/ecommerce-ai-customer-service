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
import { MessageBubble } from "../../components/MessageBubble";
import {
  formatTimeDivider,
  shouldShowTimeDivider,
} from "../../components/chatTime";
import { useAuth } from "../../auth/AuthContext";

function waitedMinutes(createdAt: string): number {
  const date = new Date(createdAt);
  if (Number.isNaN(date.getTime())) return 0;
  return Math.max(0, Math.floor((Date.now() - date.getTime()) / 60000));
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
    onSuccess: (ticket) => {
      queryClient.setQueryData<HandoffLiveSession[]>(
        ["admin", "live"],
        (current = []) =>
          current.map((session) =>
            session.id === ticket.id
              ? {
                  ...session,
                  status: "in_progress",
                  assignee_id: ticket.assignee_id ?? user?.id ?? null,
                  assignee_name: user?.username ?? "",
                }
              : session,
          ),
      );
      setNotice("已接入会话，可以开始回复顾客。");
      queryClient.invalidateQueries({ queryKey: ["admin", "live"] });
      queryClient.invalidateQueries({ queryKey: ["admin", "handoffs"] });
    },
  });

  const replyMutation = useMutation({
    mutationFn: (content: string) =>
      sendHandoffReply(selectedId as number, content),
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
      setNotice("已退出会话，已交还智能体。");
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
    <div className="cs-console">
      <div className="cs-heading">
        <h2 className="admin-title">客服回复</h2>
        <div className="conversation-time">
          待接入 {waiting.length} · 接待中 {serving.length}
        </div>
      </div>

      {notice && (
        <div className="success-text cs-notice" role="status">
          {notice}
        </div>
      )}

      <div className="cs-grid">
        <aside className="cs-sidebar" aria-label="客服会话列表">
          <SessionGroup
            title="待接入"
            empty="暂无等待接入的顾客"
            sessions={waiting}
            selectedId={selectedId}
            onSelect={openSession}
            currentUserId={user?.id}
          />
          <SessionGroup
            title="接待中"
            empty="暂无正在接待的会话"
            sessions={serving}
            selectedId={selectedId}
            onSelect={openSession}
            currentUserId={user?.id}
          />
        </aside>

        <section className="cs-main" aria-label="顾客会话窗口">
          {isLoading && (
            <div className="state-box">
              <div className="spinner" />
            </div>
          )}
          {!isLoading && !selected && (
            <div className="cs-empty">
              <Headset size={36} />
              <div>从左侧选择一个会话接入</div>
              <div className="conversation-time">
                顾客转人工后会出现在「待接入」列表
              </div>
            </div>
          )}

          {selected && (
            <>
              <header className="cs-chat-header">
                <div>
                  <div className="cs-customer">
                    <UserRound size={16} />
                    <strong>{selected.username || `顾客 #${selected.user_id}`}</strong>
                    <span className={`pill pill-${selected.status}`}>
                      {selected.status === "open" ? "待接入" : "接待中"}
                    </span>
                  </div>
                  <div className="conversation-time">
                    等待 {waitedMinutes(selected.created_at)} 分钟 · 工单 #{selected.id}
                  </div>
                </div>
                <div className="cs-head-actions">
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
                      退出会话
                    </button>
                  )}
                </div>
              </header>

              {claimMutation.error && (
                <div className="error-text cs-alert" role="alert">
                  {claimMutation.error instanceof Error
                    ? claimMutation.error.message
                    : "接入失败，请重试"}
                </div>
              )}
              {claimedByOther && (
                <div className="cs-alert cs-alert-muted">
                  该会话由 {selected.assignee_name || "其他客服"} 接待中，你当前为只读。
                </div>
              )}

              <div
                className="chat-messages"
                aria-live="polite"
                data-testid="chat-messages"
              >
                {messagesLoading && messages.length === 0 && (
                  <div className="state-box">
                    <div className="spinner" />
                  </div>
                )}
                {messages.map((message, index) => (
                  <Fragment key={message.id}>
                    {shouldShowTimeDivider(messages, index) && (
                      <div className="chat-time-divider">
                        {formatTimeDivider(message.created_at)}
                      </div>
                    )}
                    <MessageBubble message={message} />
                  </Fragment>
                ))}
                {!messagesLoading && messages.length === 0 && (
                  <div className="conversation-time">（暂无会话记录）</div>
                )}
                <div ref={bottomRef} />
              </div>

              {mine ? (
                <div className="chat-composer cs-composer">
                  <textarea
                    className="input"
                    rows={2}
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
                  <button
                    className="btn btn-primary send-btn"
                    aria-label="发送"
                    disabled={!draft.trim() || replyMutation.isPending}
                    onClick={() => replyMutation.mutate(draft.trim())}
                  >
                    <Send size={16} />
                  </button>
                </div>
              ) : (
                <div className="chat-composer cs-composer cs-composer-locked">
                  {selected.status === "open"
                    ? "接入会话后即可回复顾客。"
                    : `会话由 ${selected.assignee_name || "其他客服"} 接待中，暂不可回复。`}
                </div>
              )}
              {mine && (
                <div className="cs-composer-hint">
                  回复不会结束会话；确认问题解决后，点右上角「退出会话」交还智能体。
                </div>
              )}
            </>
          )}
        </section>
      </div>

      {releaseOpen && (
        <div className="modal-backdrop" role="presentation">
          <div
            className="modal cs-release-modal"
            role="dialog"
            aria-modal="true"
            aria-label="退出会话"
          >
            <h3>退出会话</h3>
            <p className="conversation-time">
              确认顾客问题已解决。填写处理结论后，会话将交还智能体继续服务。
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
                {releaseMutation.isPending ? "交还中…" : "确认解决并交还"}
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
    <section className="cs-group">
      <h3>
        {title}
        <span className="cs-count">{sessions.length}</span>
      </h3>
      {sessions.length === 0 && (
        <div className="conversation-time cs-group-empty">{empty}</div>
      )}
      {sessions.map((session) => {
        const mine =
          session.status === "in_progress" &&
          session.assignee_id === currentUserId;
        return (
          <button
            key={session.id}
            className={
              session.id === selectedId ? "cs-session active" : "cs-session"
            }
            onClick={() => onSelect(session)}
          >
            <div className="cs-session-top">
              <strong>{session.username || `顾客 #${session.user_id}`}</strong>
              <span className="conversation-time">
                {waitedMinutes(session.created_at)} 分钟
              </span>
            </div>
            <div className="cs-session-preview">
              {session.last_message || session.summary || "（暂无消息）"}
            </div>
            <div className="cs-session-meta">
              <span className={`pill pill-${session.status}`}>
                {mine
                  ? "我接待中"
                  : session.status === "in_progress"
                    ? session.assignee_name || "接待中"
                    : "待接入"}
              </span>
            </div>
          </button>
        );
      })}
    </section>
  );
}
