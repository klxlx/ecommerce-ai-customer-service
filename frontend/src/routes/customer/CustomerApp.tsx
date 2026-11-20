import { Fragment, useEffect, useRef, useState } from "react";
import {
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import {
  Link,
  useNavigate,
  useParams,
  useSearchParams,
} from "react-router-dom";
import {
  LayoutGrid,
  LogOut,
  Menu,
  MessageSquarePlus,
  Send,
  Trash2,
  X,
} from "lucide-react";
import { useAuth } from "../../auth/AuthContext";
import { MessageBubble } from "../../components/MessageBubble";
import { ReturnRequestModal } from "./ReturnRequestModal";
import {
  formatTimeDivider,
  shouldShowTimeDivider,
} from "../../components/chatTime";
import {
  createConversation,
  deleteConversation,
  listConversations,
  listMessages,
  sendMessageStream,
  type ChatMessage,
} from "../../api/chat";

const WELCOME_MESSAGE =
  "你好呀！我是你的智能客服小天，请问有什么能够帮到你的？";

const SUGGESTIONS = [
  "有哪些蓝牙耳机？",
  "退换货政策是什么？",
  "查询我的订单物流",
];

export function CustomerApp() {
  const params = useParams();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const queryClient = useQueryClient();
  const autoStartRequested = searchParams.get("start") === "1";
  const autoStartConsumed = useRef(false);
  const activeId = params["*"] ? Number(params["*"].split("/")[0]) : undefined;
  const [railOpen, setRailOpen] = useState(false);
  const { data: conversations } = useQuery({
    queryKey: ["conversations"],
    queryFn: listConversations,
  });
  const startConversationMutation = useMutation({
    mutationFn: () => createConversation(),
    onSuccess: (conversation) => {
      queryClient.setQueryData(["conversations"], [conversation]);
      navigate(`/app/${conversation.id}`, { replace: true });
    },
  });
  const activeConversation = conversations?.find(
    (conversation) => conversation.id === activeId,
  );

  useEffect(() => {
    if (activeId == null && conversations && conversations.length > 0) {
      navigate(`/app/${conversations[0].id}`, { replace: true });
    }
  }, [activeId, conversations, navigate]);

  useEffect(() => {
    if (
      activeId != null ||
      !autoStartRequested ||
      conversations === undefined ||
      conversations.length > 0 ||
      autoStartConsumed.current
    ) {
      return;
    }

    autoStartConsumed.current = true;
    startConversationMutation.mutate();
  }, [
    activeId,
    autoStartRequested,
    conversations,
    startConversationMutation,
  ]);

  return (
    <div className="shell customer-shell">
      <main className="main">
        <div className="customer-workspace">
          <ConversationRail
            activeId={activeId}
            open={railOpen}
            onClose={() => setRailOpen(false)}
          />
          {railOpen && (
            <div
              className="rail-backdrop"
              aria-hidden="true"
              onClick={() => setRailOpen(false)}
            />
          )}
          <ChatPanel
            conversationId={activeId}
            conversationTitle={activeConversation?.title || "智能客服"}
            onOpenRail={() => setRailOpen(true)}
          />
        </div>
      </main>
    </div>
  );
}

function ConversationRail({
  activeId,
  open,
  onClose,
}: {
  activeId?: number;
  open: boolean;
  onClose: () => void;
}) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { user, logout } = useAuth();
  const [pendingDeleteId, setPendingDeleteId] = useState<number | null>(null);
  const [deleteError, setDeleteError] = useState("");
  const { data: conversations, isLoading } = useQuery({
    queryKey: ["conversations"],
    queryFn: listConversations,
  });

  const createMutation = useMutation({
    mutationFn: () => createConversation(),
    onSuccess: (conv) => {
      queryClient.invalidateQueries({ queryKey: ["conversations"] });
      navigate(`/app/${conv.id}`);
      onClose();
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => deleteConversation(id),
    onSuccess: (_, id) => {
      queryClient.setQueryData(
        ["conversations"],
        (current: typeof conversations) =>
          current?.filter((conversation) => conversation.id !== id),
      );
      setPendingDeleteId(null);
      setDeleteError("");
      if (activeId === id) {
        navigate("/app");
      }
    },
    onError: () => {
      setDeleteError("会话删除失败，请稍后重试");
    },
  });

  function openConversation(id: number) {
    navigate(`/app/${id}`);
    onClose();
  }

  return (
    <aside
      className={`conversation-rail ${open ? "open" : ""}`}
      aria-label="会话列表"
    >
      <div className="rail-header">
        <button
          className="btn btn-primary"
          style={{ flex: 1 }}
          onClick={() => createMutation.mutate()}
        >
          <MessageSquarePlus size={16} /> 新建会话
        </button>
        <button
          type="button"
          className="btn btn-ghost rail-close"
          aria-label="关闭会话列表"
          onClick={onClose}
        >
          <X size={18} />
        </button>
      </div>
      <div className="conversation-list">
        {isLoading && (
          <div className="state-box">
            <div className="spinner" />
          </div>
        )}
        {!isLoading && conversations?.length === 0 && (
          <div className="state-box">暂无历史会话</div>
        )}
        {deleteError && (
          <div className="error-text conversation-delete-error" role="alert">
            {deleteError}
          </div>
        )}
        {conversations?.map((c) => (
          <div
            key={c.id}
            className={`conversation-item ${c.id === activeId ? "active" : ""}`}
          >
            <div
              className="conversation-item-main"
              onClick={() => openConversation(c.id)}
            >
              <div className="conversation-title">
                {c.title || "智能客服"}
              </div>
              <div className="conversation-time">
                {new Date(c.updated_at).toLocaleString()}
              </div>
            </div>
            <button
              className="btn btn-ghost conversation-delete"
              type="button"
              aria-label={`删除会话 ${c.title || "智能客服"}`}
              onClick={(event) => {
                event.stopPropagation();
                setPendingDeleteId(c.id);
                setDeleteError("");
              }}
            >
              <Trash2 size={14} />
            </button>
            {pendingDeleteId === c.id && (
              <div
                className="conversation-delete-confirm"
                onClick={(event) => event.stopPropagation()}
              >
                <span>删除后不再显示该会话</span>
                <div className="conversation-delete-actions">
                  <button
                    className="btn btn-danger"
                    type="button"
                    onClick={() => deleteMutation.mutate(c.id)}
                    disabled={deleteMutation.isPending}
                  >
                    确认删除
                  </button>
                  <button
                    className="btn btn-ghost"
                    type="button"
                    onClick={() => setPendingDeleteId(null)}
                    disabled={deleteMutation.isPending}
                  >
                    取消
                  </button>
                </div>
              </div>
            )}
          </div>
        ))}
      </div>
      <div className="rail-account">
        <div className="conversation-title">{user?.username}</div>
        {user?.role === "admin" && (
          <Link
            className="nav-item"
            to="/admin"
            aria-label="管理控制台"
            onClick={onClose}
          >
            <LayoutGrid size={18} />
            <span>管理控制台</span>
          </Link>
        )}
        <button
          className="btn btn-secondary"
          onClick={() => logout()}
          aria-label="退出登录"
        >
          <LogOut size={16} />
          <span>退出登录</span>
        </button>
      </div>
    </aside>
  );
}

function ChatPanel({
  conversationId,
  conversationTitle,
  onOpenRail,
}: {
  conversationId?: number;
  conversationTitle: string;
  onOpenRail: () => void;
}) {
  const [draft, setDraft] = useState("");
  const [streamingMessages, setStreamingMessages] = useState<
    ChatMessage[] | null
  >(null);
  const [streaming, setStreaming] = useState(false);
  const [streamError, setStreamError] = useState("");
  const [returnOpen, setReturnOpen] = useState(false);
  const streamAbort = useRef<AbortController | null>(null);
  const queryClient = useQueryClient();

  const { data: messages, isLoading } = useQuery({
    queryKey: ["messages", conversationId],
    queryFn: () => listMessages(conversationId!),
    enabled: conversationId != null,
    refetchInterval: conversationId == null || streaming ? false : 3000,
  });

  useEffect(() => {
    return () => {
      streamAbort.current?.abort();
    };
  }, [conversationId]);

  useEffect(() => {
    setStreamingMessages(null);
    setStreaming(false);
    setStreamError("");
  }, [conversationId]);

  async function handleSend() {
    const content = draft.trim();
    if (!content || conversationId == null || streaming) return;

    setDraft("");
    setStreamError("");
    const now = new Date().toISOString();
    const pendingUser: ChatMessage = {
      id: -1,
      role: "user",
      content,
      intent: null,
      sources: null,
      created_at: now,
    };
    const pendingAssistant: ChatMessage = {
      id: -2,
      role: "assistant",
      content: "",
      intent: null,
      sources: null,
      created_at: now,
    };
    setStreamingMessages([pendingUser, pendingAssistant]);
    setStreaming(true);

    const controller = new AbortController();
    streamAbort.current = controller;

    try {
      await sendMessageStream(
        conversationId,
        content,
        (text) => {
          setStreamingMessages((current) =>
            current
              ? current.map((message) =>
                  message.id === -2
                    ? { ...message, content: message.content + text }
                    : message,
                )
              : current,
          );
        },
        controller.signal,
        (text) => {
          setStreamingMessages((current) =>
            current
              ? current.map((message) =>
                  message.id === -2 ? { ...message, content: text } : message,
                )
              : current,
          );
        },
      );

      const fresh = await listMessages(conversationId);
      queryClient.setQueryData(["messages", conversationId], fresh);
      queryClient.invalidateQueries({ queryKey: ["conversations"] });
      setStreamingMessages(null);
    } catch (error) {
      if (!controller.signal.aborted) {
        setStreamError(
          error instanceof Error ? error.message : "发送失败，请稍后重试",
        );
        try {
          const fresh = await listMessages(conversationId);
          queryClient.setQueryData(["messages", conversationId], fresh);
        } catch {
          // Keep the optimistic turn visible when the refetch also fails.
        }
        setStreamingMessages(null);
      }
    } finally {
      if (streamAbort.current === controller) {
        streamAbort.current = null;
      }
      setStreaming(false);
    }
  }

  const actualMessages = messages ?? [];
  const greeting: ChatMessage = {
    id: -100,
    role: "assistant",
    content: WELCOME_MESSAGE,
    intent: null,
    sources: null,
    created_at: new Date().toISOString(),
  };
  const visibleMessages: ChatMessage[] =
    !isLoading &&
    actualMessages.length === 0 &&
    streamingMessages === null
      ? [greeting]
      : [...actualMessages, ...(streamingMessages ?? [])];

  return (
    <section className="chat-panel">
      <header className="chat-header">
        <button
          type="button"
          className="rail-toggle"
          aria-label="打开会话列表"
          onClick={onOpenRail}
        >
          <Menu size={18} />
        </button>
        <strong className="chat-title">{conversationTitle}</strong>
      </header>

      {conversationId == null ? (
        <div className="state-box chat-empty">
          <MessageSquarePlus size={32} className="state-icon" />
          <div>选择或新建一个会话开始咨询</div>
        </div>
      ) : (
        <>
          <div className="suggestions">
            {SUGGESTIONS.map((s) => (
              <button
                key={s}
                className="suggestion"
                onClick={() => setDraft(s)}
              >
                {s}
              </button>
            ))}
          </div>

          <div className="chat-messages" data-testid="chat-messages">
            {isLoading && (
              <div className="state-box">
                <div className="spinner" />
              </div>
            )}
            {visibleMessages.map((m, index) => (
              <Fragment key={m.id}>
                {shouldShowTimeDivider(visibleMessages, index) && (
                  <div className="chat-time-divider">
                    {formatTimeDivider(m.created_at)}
                  </div>
                )}
                {m.id === -2 ? (
                  <div className="message assistant">
                    <div className="message-bubble">
                      {m.content}
                      <span className="stream-cursor" aria-hidden="true" />
                    </div>
                  </div>
                ) : (
                  <MessageBubble
                    message={m}
                    onReturnRequest={() => setReturnOpen(true)}
                  />
                )}
              </Fragment>
            ))}
          </div>

          {streamError && (
            <div className="error-text chat-stream-error" role="alert">
              {streamError}
            </div>
          )}

          <footer className="chat-composer">
            <textarea
              className="input"
              rows={2}
              value={draft}
              placeholder="输入您的问题…"
              aria-label="输入消息"
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  handleSend();
                }
              }}
            />
            <button
              className="btn btn-primary send-btn"
              aria-label="发送"
              onClick={handleSend}
              disabled={!draft.trim() || streaming}
            >
              <Send size={16} />
            </button>
          </footer>
          {returnOpen && conversationId != null && (
            <ReturnRequestModal
              conversationId={conversationId}
              onClose={() => setReturnOpen(false)}
              onSubmitted={() => {
                setReturnOpen(false);
                queryClient.invalidateQueries({
                  queryKey: ["messages", conversationId],
                });
                queryClient.invalidateQueries({ queryKey: ["conversations"] });
              }}
            />
          )}
        </>
      )}
    </section>
  );
}

