import { Navigate, NavLink, Route, Routes } from "react-router-dom";
import {
  BookOpen,
  Inbox,
  LogOut,
  MessagesSquare,
  ScrollText,
} from "lucide-react";
import { useAuth } from "../../auth/AuthContext";
import { HandoffsPage } from "./HandoffsPage";
import { HandoffDetailPage } from "./HandoffDetailPage";
import { KnowledgePage } from "./KnowledgePage";
import { LiveConsolePage } from "./LiveConsolePage";
import { LogsPage } from "./LogsPage";

const navClass = ({ isActive }: { isActive: boolean }) =>
  isActive ? "nav-item active" : "nav-item";

export function AdminApp() {
  const { user, logout } = useAuth();

  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="sidebar-header">
          <div className="brand">管理控制台</div>
          <div className="brand-sub">运营后台</div>
        </div>
        <nav className="nav">
          <NavLink className={navClass} to="/admin/handoffs" aria-label="工单队列">
            <Inbox size={18} />
            <span>工单队列</span>
          </NavLink>
          <NavLink className={navClass} to="/admin/live" aria-label="人工回复">
            <MessagesSquare size={18} />
            <span>人工回复</span>
          </NavLink>
          <NavLink className={navClass} to="/admin/knowledge" aria-label="知识库">
            <BookOpen size={18} />
            <span>知识库</span>
          </NavLink>
          <NavLink className={navClass} to="/admin/logs" aria-label="运行日志">
            <ScrollText size={18} />
            <span>运行日志</span>
          </NavLink>
        </nav>
        <div className="sidebar-footer">
          <div className="conversation-title">{user?.username}</div>
          <button
            className="btn btn-ghost"
            onClick={() => logout()}
            aria-label="退出登录"
          >
            <LogOut size={16} />
            <span>退出登录</span>
          </button>
        </div>
      </aside>
      <main className="main">
        <div className="admin-content">
          <Routes>
            <Route path="handoffs" element={<HandoffsPage />} />
            <Route path="handoffs/:id" element={<HandoffDetailPage />} />
            <Route path="live" element={<LiveConsolePage />} />
            <Route path="knowledge" element={<KnowledgePage />} />
            <Route path="logs" element={<LogsPage />} />
            <Route path="*" element={<Navigate to="/admin/handoffs" replace />} />
          </Routes>
        </div>
      </main>
    </div>
  );
}
