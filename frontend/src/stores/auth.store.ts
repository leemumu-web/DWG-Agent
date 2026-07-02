import { create } from 'zustand';
import type { User } from '../types/user';

interface AuthState {
  accessToken: string | null;
  user: User | null;
  setSession: (token: string, user: User) => void;
  clearSession: () => void;
}

const savedToken = localStorage.getItem('dwg_access_token');
const savedUser = localStorage.getItem('dwg_user');

export const useAuthStore = create<AuthState>((set) => ({
  accessToken: savedToken,
  user: savedUser ? JSON.parse(savedUser) : null,
  setSession: (token, user) => {
    localStorage.setItem('dwg_access_token', token);
    localStorage.setItem('dwg_user', JSON.stringify(user));
    set({ accessToken: token, user });
  },
  clearSession: () => {
    localStorage.removeItem('dwg_access_token');
    localStorage.removeItem('dwg_user');
    set({ accessToken: null, user: null });
  },
}));
