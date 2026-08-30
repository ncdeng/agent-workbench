import { useState, useEffect, useCallback } from 'react';

export type Theme = 'light' | 'dark' | 'warm' | 'ink' | 'glass';

const THEMES: Theme[] = ['light', 'dark', 'warm', 'ink', 'glass'];

function isTheme(value: string | null): value is Theme {
  return value === 'light' || value === 'dark' || value === 'warm' || value === 'ink' || value === 'glass';
}

export function useTheme() {
  const [theme, setThemeState] = useState<Theme>(() => {
    const saved = localStorage.getItem('cst-theme');
    if (isTheme(saved)) return saved;
    return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  });

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme);
    localStorage.setItem('cst-theme', theme);
  }, [theme]);

  const toggleTheme = useCallback(() => {
    setThemeState(prev => THEMES[(THEMES.indexOf(prev) + 1) % THEMES.length]);
  }, []);

  return { theme, toggleTheme };
}
