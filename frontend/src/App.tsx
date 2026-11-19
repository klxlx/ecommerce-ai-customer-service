import { Navigate, Route, Routes } from "react-router-dom";
import { RequireAdmin } from "./auth/RequireAdmin";
import { RequireAuth } from "./auth/RequireAuth";
import { LoginPage } from "./routes/login/LoginPage";
import { CustomerApp } from "./routes/customer/CustomerApp";
import { AdminApp } from "./routes/admin/AdminApp";

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route element={<RequireAuth />}>
        <Route path="/app/*" element={<CustomerApp />} />
      </Route>
      <Route element={<RequireAdmin />}>
        <Route path="/admin/*" element={<AdminApp />} />
      </Route>
      <Route path="/" element={<Navigate to="/app" replace />} />
      <Route path="*" element={<Navigate to="/app" replace />} />
    </Routes>
  );
}
