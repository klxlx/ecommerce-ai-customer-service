import { useEffect, useRef, useState } from "react";
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
import {
  createConversation,
  deleteConversation,
  listConversations,
  listMessages,
  sendMessage,
  type AfterSaleContext,
  type ChatMessage,
} from "../../api/chat";

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
  const queryClient = useQueryClient();

  const { data: messages, isLoading } = useQuery({
    queryKey: ["messages", conversationId],
    queryFn: () => listMessages(conversationId!),
    enabled: conversationId != null,
    refetchInterval: conversationId == null ? false : 3000,
  });

  const sendMutation = useMutation({
    mutationFn: (content: string) => sendMessage(conversationId!, content),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["messages", conversationId] });
      queryClient.invalidateQueries({ queryKey: ["conversations"] });
    },
  });

  function handleSend() {
    const content = draft.trim();
    if (!content || conversationId == null) return;
    setDraft("");
    sendMutation.mutate(content);
  }

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
            {!isLoading && messages?.length === 0 && (
              <div className="state-box">
                你好，我是智能客服，有什么可以帮您？
              </div>
            )}
            {messages?.map((m) => (
              <MessageBubble key={m.id} message={m} />
            ))}
            {sendMutation.isPending && (
              <div className="message assistant">
                <div className="message-bubble">
                  <div className="spinner" />
                </div>
              </div>
            )}
          </div>

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
              disabled={!draft.trim() || sendMutation.isPending}
            >
              <Send size={16} />
            </button>
          </footer>
        </>
      )}
    </section>
  );
}

export function MessageBubble({ message }: { message: ChatMessage }) {
  const isUser = message.role === "user";
  return (
    <div className={`message ${message.role}`}>
      {message.role === "admin" && (
        <div className="message-meta">人工客服</div>
      )}
      <div className="message-bubble">{message.content}</div>
      {!isUser && message.after_sale && (
        <AfterSalePanel afterSale={message.after_sale} />
      )}
      {!isUser && message.sources && message.sources.length > 0 && (
        <div className="sources" data-testid="message-sources">
          {message.sources.map((s, i) => (
            <span key={i} className="source-chip">
              {s.title}
            </span>
          ))}
        </div>
      )}
      {!isUser && message.intent === "handoff" && (
        <div className="handoff-notice">已转接人工客服</div>
      )}
    </div>
  );
}

function AfterSalePanel({ afterSale }: { afterSale: AfterSaleContext }) {
  return (
    <section
      className="after-sale-panel"
      role="region"
      aria-label={`订单 ${afterSale.order_no} 售后信息`}
    >
      <div className="after-sale-heading">
        <strong>订单 {afterSale.order_no}</strong>
        <span>{afterSale.service_type}</span>
      </div>

      {afterSale.status === "has_records" && (
        <>
          {afterSale.order_status && (
            <div className="after-sale-order-state">
              订单状态：{afterSale.order_status}
            </div>
          )}
          <div className="after-sale-records">
            {afterSale.records.map((record) => (
              <article className="after-sale-record" key={record.id}>
                <div className="after-sale-record-top">
                  <strong>{record.service_type}</strong>
                  <span className="after-sale-status">{record.status}</span>
                </div>
                {record.refund_amount && (
                  <div className="after-sale-amount">
                    退款金额：¥{record.refund_amount}
                  </div>
                )}
                <div>原因：{record.reason}</div>
                <div className="after-sale-time">
                  申请时间：{formatAfterSaleTime(record.created_at)}
                </div>
              </article>
            ))}
          </div>
        </>
      )}

      {afterSale.status === "no_records" && (
        <div className="after-sale-empty">
          {afterSale.order_status && (
            <div>订单状态：{afterSale.order_status}</div>
          )}
          <div>暂无售后记录</div>
          <div className="after-sale-guidance">
            可核对订单详情，或转人工客服进一步核实。
          </div>
        </div>
      )}

      {afterSale.status === "not_available" && (
        <div className="after-sale-empty">
          未查询到可展示的订单或售后信息
        </div>
      )}

      {afterSale.status === "query_error" && (
        <div className="after-sale-empty">
          <div>售后查询暂时不可用</div>
          <div className="after-sale-guidance">已转接人工客服核实</div>
        </div>
      )}
    </section>
  );
}

function formatAfterSaleTime(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleString("zh-CN", {
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
      });
}
