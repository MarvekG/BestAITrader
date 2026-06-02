import { useSessionStore } from '../store/useSessionStore';

export function getAuthToken(): string | null {
  return useSessionStore.getState().token;
}

export function setAuthToken(token: string) {
  useSessionStore.setState({ isLoggedIn: true, token });
}

export function clearAuthSession() {
  useSessionStore.getState().clearSession();
}
