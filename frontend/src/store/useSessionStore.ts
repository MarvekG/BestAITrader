import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import { Session, sessionApi } from '../api/session';

interface SessionState {
  sessions: Session[];
  activeSession: Session | null;
  loading: boolean;
  isLoggedIn: boolean;
  token: string | null;
  // Shared UI State
  selectedPrice: number | null;

  fetchSessions: () => Promise<void>;
  createSession: (code: string, name?: string, frequency?: string, strategy?: string) => Promise<Session>;
  setActiveSession: (session: Session) => void;
  setLoggedIn: (status: boolean) => void;
  setToken: (token: string | null) => void;
  setSelectedPrice: (price: number | null) => void;
  clearActiveSession: () => void;
  clearSession: () => void;
}

export const useSessionStore = create<SessionState>()(
  persist(
    (set) => ({
      sessions: [],
      activeSession: null,
      loading: false,
      isLoggedIn: false,
      token: null,
      selectedPrice: null,

      fetchSessions: async () => {
        set({ loading: true });
        try {
          const sessions = await sessionApi.list({ status: 'active' });
          set({ sessions });
        } finally {
          set({ loading: false });
        }
      },

      createSession: async (code, name, frequency, strategy) => {
        const session = await sessionApi.create({
          stock_code: code,
          stock_name: name || 'Unknown Stock',
          trading_frequency: frequency!,
          trading_strategy: strategy!
        });
        set(state => ({ sessions: [session, ...state.sessions] }));
        return session;
      },

      setActiveSession: (session) => set({ activeSession: session }),
      setLoggedIn: (status) => set({ isLoggedIn: status }),
      setToken: (token) => set({ token }),
      setSelectedPrice: (price) => set({ selectedPrice: price }),
      clearActiveSession: () => set({ activeSession: null, selectedPrice: null }),
      clearSession: () => set({ activeSession: null, isLoggedIn: false, token: null, selectedPrice: null }),
    }),
    {
      name: 'session-storage',
      partialize: (state) => ({
        activeSession: state.activeSession,
        isLoggedIn: state.isLoggedIn,
        token: state.token,
      }),
    }
  )
);
