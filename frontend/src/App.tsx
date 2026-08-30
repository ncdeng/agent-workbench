import { Suspense, lazy, useEffect, useLayoutEffect, useState } from 'react';
import { BrowserRouter, Routes, Route, NavLink, Navigate, useLocation } from 'react-router-dom';
import {
  Activity,
  Brush,
  Droplets,
  Flame,
  Languages,
  LayoutDashboard,
  Menu,
  MessageSquareText,
  Moon,
  Sun,
  X,
} from 'lucide-react';
import { useTheme } from './hooks/useTheme';
import { useI18n } from './hooks/useI18n';
import { ChatSessionProvider } from './context/ChatSessionContext';
import { AppErrorBoundary } from './components/AppErrorBoundary';

const Dashboard = lazy(() => import('./pages/Dashboard').then(module => ({ default: module.Dashboard })));
const Chat = lazy(() => import('./pages/Chat').then(module => ({ default: module.Chat })));
const Trace = lazy(() => import('./pages/Trace').then(module => ({ default: module.Trace })));

function RouteFallback() {
  return (
    <div className="surface min-h-[420px] grid place-items-center text-text-muted text-sm">
      Loading workspace...
    </div>
  );
}

function ScrollToTop() {
  const { pathname } = useLocation();
  useEffect(() => {
    if ('scrollRestoration' in window.history) {
      window.history.scrollRestoration = 'manual';
    }
  }, []);
  useLayoutEffect(() => {
    window.scrollTo({ top: 0, left: 0, behavior: 'auto' });
    const frame = window.requestAnimationFrame(() => window.scrollTo({ top: 0, left: 0, behavior: 'auto' }));
    return () => window.cancelAnimationFrame(frame);
  }, [pathname]);
  return null;
}

type NavItem = {
  to: string;
  icon: React.ReactNode;
  label: string;
};

function Sidebar({ items, onClose }: { items: NavItem[]; onClose?: () => void }) {
  const { theme, toggleTheme } = useTheme();
  const { lang, setLang } = useI18n();

  const cycleLang = () => setLang(lang === 'zh' ? 'en' : 'zh');

  const themeMeta = {
    light: { icon: <Sun size={16} />, label: lang === 'zh' ? '浅色' : 'Light' },
    dark: { icon: <Moon size={16} />, label: lang === 'zh' ? '深色' : 'Dark' },
    warm: { icon: <Flame size={16} />, label: lang === 'zh' ? '暖色' : 'Warm' },
    ink: { icon: <Brush size={16} />, label: lang === 'zh' ? '水墨' : 'Ink' },
    glass: { icon: <Droplets size={16} />, label: lang === 'zh' ? '水璃' : 'Glass' },
  }[theme];

  return (
    <div className={`relative flex h-full flex-col bg-sidebar-bg text-sidebar-text overflow-hidden ${theme === 'ink' ? 'ink-sidebar-ambient' : ''}`}>
      <div className="flex items-center justify-center xl:justify-start gap-3 px-4 xl:px-5 py-4 border-b border-sidebar-accent sidebar-glass">
        <span className="w-9 h-9 grid place-items-center rounded-[10px] bg-primary text-text-on-primary font-extrabold text-sm tracking-tight shadow-sm">
          CA
        </span>
        <span className="hidden xl:block font-bold text-sidebar-text-active text-base tracking-tight truncate">
          CST-Agent
        </span>
      </div>

      <nav className="flex-1 overflow-y-auto px-2 xl:px-3 py-5 space-y-1">
        {items.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            onClick={onClose}
            className={({ isActive }) =>
              `group relative flex items-center justify-center xl:justify-start gap-3 px-3 py-3 rounded-xl text-sm font-medium transition-all duration-200 ${
                isActive
                  ? 'bg-sidebar-accent text-sidebar-text-active shadow-sm'
                  : 'text-sidebar-text hover:text-sidebar-text-active hover:bg-sidebar-accent/60'
              }`
            }
          >
            {({ isActive }) => (
              <>
                <span
                  className={`absolute left-0 top-1/2 -translate-y-1/2 w-1.5 h-1.5 rounded-full bg-primary transition-all duration-200 ${
                    isActive ? 'opacity-100 translate-x-0' : 'opacity-0 -translate-x-1'
                  }`}
                />
                <span className="opacity-80 group-hover:opacity-100 transition-opacity">{item.icon}</span>
                <span className="hidden xl:inline">{item.label}</span>
              </>
            )}
          </NavLink>
        ))}
      </nav>

      <div className="p-2 xl:p-3 border-t border-sidebar-accent grid grid-cols-1 xl:grid-cols-2 gap-2">
        <button
          onClick={cycleLang}
          className="btn btn-ghost justify-center xl:justify-start text-sidebar-text hover:text-sidebar-text-active hover:bg-sidebar-accent"
          title={lang === 'zh' ? 'Switch to English' : '切换到中文'}
        >
          <Languages size={16} />
          <span className="hidden xl:inline">{lang === 'zh' ? 'EN' : '中'}</span>
        </button>
        <button
          onClick={toggleTheme}
          className="btn btn-ghost justify-center xl:justify-start text-sidebar-text hover:text-sidebar-text-active hover:bg-sidebar-accent"
          aria-label={`Theme: ${themeMeta.label}`}
        >
          {themeMeta.icon}
          <span className="hidden xl:inline">{themeMeta.label}</span>
        </button>
      </div>
    </div>
  );
}

function AppShell() {
  const { t } = useI18n();
  const [mobileOpen, setMobileOpen] = useState(false);

  const navItems: NavItem[] = [
    { to: '/dashboard', icon: <LayoutDashboard size={18} />, label: t('nav.dashboard') },
    { to: '/chat', icon: <MessageSquareText size={18} />, label: t('nav.chat') },
    { to: '/trace', icon: <Activity size={18} />, label: t('nav.trace') },
  ];

  return (
    <div className="min-h-screen bg-bg flex">
      {/* Desktop sidebar */}
      <aside className="hidden md:flex w-[72px] xl:w-[220px] flex-shrink-0 fixed inset-y-0 left-0 z-40 transition-[width] duration-200">
        <Sidebar items={navItems} />
      </aside>

      {/* Mobile header */}
      <header className="md:hidden fixed top-0 left-0 right-0 z-50 h-14 bg-surface border-b border-border flex items-center justify-between px-4">
        <div className="flex items-center gap-2.5">
          <span className="w-8 h-8 grid place-items-center rounded-lg bg-primary text-white font-extrabold text-xs">
            CA
          </span>
          <span className="font-bold text-text text-sm">CST-Agent</span>
        </div>
        <button
          onClick={() => setMobileOpen(prev => !prev)}
          className="inline-flex w-10 h-10 items-center justify-center rounded-lg border border-border bg-elevated text-text shadow-sm transition-colors hover:border-primary/40 hover:bg-primary-subtle"
          aria-label="Toggle menu"
          aria-expanded={mobileOpen}
          aria-controls="mobile-navigation"
        >
          {mobileOpen ? <X size={24} strokeWidth={2.5} /> : <Menu size={24} strokeWidth={2.5} />}
        </button>
      </header>

      {/* Mobile drawer */}
      {mobileOpen && (
        <div className="md:hidden fixed inset-0 z-40 pt-14">
          <div className="absolute inset-0 bg-black/40" onClick={() => setMobileOpen(false)} />
          <div id="mobile-navigation" className="absolute left-0 top-14 bottom-0 w-[min(86vw,280px)] bg-sidebar-bg">
            <Sidebar items={navItems} onClose={() => setMobileOpen(false)} />
          </div>
        </div>
      )}

      {/* Main content */}
      <main className="flex-1 md:ml-[72px] xl:ml-[220px] min-w-0 min-h-screen pt-14 md:pt-0 transition-[margin] duration-200">
        <div className="workspace-frame">
          <AppErrorBoundary>
            <Suspense fallback={<RouteFallback />}>
              <Routes>
                <Route path="/" element={<Navigate to="/dashboard" replace />} />
                <Route path="/dashboard" element={<Dashboard />} />
                <Route path="/chat" element={<Chat />} />
                <Route path="/trace" element={<Trace />} />
              </Routes>
            </Suspense>
          </AppErrorBoundary>
        </div>
      </main>
    </div>
  );
}

export function App() {
  return (
    <BrowserRouter>
      <ScrollToTop />
      <ChatSessionProvider>
        <AppShell />
      </ChatSessionProvider>
    </BrowserRouter>
  );
}
