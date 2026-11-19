import { apiFetch } from "./client";

export interface User {
  id: number;
  username: string;
  role: string;
}

export interface LoginResponse {
  token: string;
  user: User;
}

export async function loginRequest(
  username: string,
  password: string,
): Promise<LoginResponse> {
  return apiFetch<LoginResponse>("/api/auth/login", {
    method: "POST",
    body: JSON.stringify({ username, password }),
  });
}

export async function fetchMe(): Promise<User> {
  return apiFetch<User>("/api/auth/me");
}

export async function logoutRequest(): Promise<void> {
  await apiFetch<void>("/api/auth/logout", { method: "POST" });
}
