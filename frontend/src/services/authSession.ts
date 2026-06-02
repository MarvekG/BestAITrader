import { useSessionStore } from '../store/useSessionStore';

const LEGACY_TOKEN_KEY = 'token';

export function getAuthToken(): string | null {
  return useSessionStore.getState().token;
}

export function setAuthToken(token: string) {
  useSessionStore.setState({ isLoggedIn: true, token });
  localStorage.removeItem(LEGACY_TOKEN_KEY);
}

export function clearAuthSession() {
  useSessionStore.getState().clearSession();
  localStorage.removeItem(LEGACY_TOKEN_KEY);
}
