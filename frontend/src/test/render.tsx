import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import type { ReactElement, ReactNode } from "react";
import { AuthProvider } from "../auth/AuthContext";

export function renderWithProviders(
  ui: ReactElement,
  { initialEntries = ["/"] }: { initialEntries?: string[] } = {},
) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  function Wrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={queryClient}>
        <AuthProvider>
          <MemoryRouter initialEntries={initialEntries}>{children}</MemoryRouter>
        </AuthProvider>
      </QueryClientProvider>
    );
  }
  return render(ui, { wrapper: Wrapper });
}
