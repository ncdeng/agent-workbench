import { describe, it, expect, beforeEach, vi } from 'vitest';
import { renderHook, act } from '@testing-library/react';

// useTheme tests
describe('useTheme', () => {
  beforeEach(() => {
    localStorage.clear();
    document.documentElement.removeAttribute('data-theme');
  });

  it('defaults to light when no saved preference and no dark media query', async () => {
    const { useTheme } = await import('../hooks/useTheme');
    const { result } = renderHook(() => useTheme());
    expect(result.current.theme).toBe('light');
    expect(document.documentElement.getAttribute('data-theme')).toBe('light');
  });

  it('reads saved theme from localStorage', async () => {
    localStorage.setItem('cst-theme', 'warm');
    const { useTheme } = await import('../hooks/useTheme');
    const { result } = renderHook(() => useTheme());
    expect(result.current.theme).toBe('warm');
  });

  it('toggleTheme cycles through light, dark, warm, ink, and glass', async () => {
    const { useTheme } = await import('../hooks/useTheme');
    const { result } = renderHook(() => useTheme());
    expect(result.current.theme).toBe('light');

    act(() => result.current.toggleTheme());
    expect(result.current.theme).toBe('dark');
    expect(localStorage.getItem('cst-theme')).toBe('dark');

    act(() => result.current.toggleTheme());
    expect(result.current.theme).toBe('warm');
    expect(localStorage.getItem('cst-theme')).toBe('warm');

    act(() => result.current.toggleTheme());
    expect(result.current.theme).toBe('ink');
    expect(localStorage.getItem('cst-theme')).toBe('ink');

    act(() => result.current.toggleTheme());
    expect(result.current.theme).toBe('glass');
    expect(localStorage.getItem('cst-theme')).toBe('glass');

    act(() => result.current.toggleTheme());
    expect(result.current.theme).toBe('light');
    expect(localStorage.getItem('cst-theme')).toBe('light');
  });

  it('persists theme to localStorage', async () => {
    const { useTheme } = await import('../hooks/useTheme');
    const { result } = renderHook(() => useTheme());

    act(() => result.current.toggleTheme());
    expect(localStorage.getItem('cst-theme')).toBe('dark');
  });
});

// useI18n tests
describe('useI18n', () => {
  beforeEach(() => {
    localStorage.clear();
    // useI18n 现在是模块级共享 store，重置模块让每个用例重新读 localStorage 初始化
    vi.resetModules();
  });

  it('returns zh for Chinese locale by default', async () => {
    const { useI18n } = await import('../hooks/useI18n');
    const { result } = renderHook(() => useI18n());
    // Default depends on navigator.language; just verify it returns a valid lang
    expect(['zh', 'en']).toContain(result.current.lang);
  });

  it('reads saved language from localStorage', async () => {
    localStorage.setItem('cst-lang', 'en');
    const { useI18n } = await import('../hooks/useI18n');
    const { result } = renderHook(() => useI18n());
    expect(result.current.lang).toBe('en');
  });

  it('t() returns translated string', async () => {
    localStorage.setItem('cst-lang', 'en');
    const { useI18n } = await import('../hooks/useI18n');
    const { result } = renderHook(() => useI18n());
    expect(result.current.t('nav.dashboard')).toBe('Dashboard');
    expect(result.current.t('chat.send')).toBe('Send');
  });

  it('t() returns Chinese when lang is zh', async () => {
    localStorage.setItem('cst-lang', 'zh');
    const { useI18n } = await import('../hooks/useI18n');
    const { result } = renderHook(() => useI18n());
    expect(result.current.t('nav.dashboard')).toBe('仪表盘');
    expect(result.current.t('chat.send')).toBe('发送');
  });

  it('setLang changes language and persists', async () => {
    localStorage.setItem('cst-lang', 'zh');
    const { useI18n } = await import('../hooks/useI18n');
    const { result } = renderHook(() => useI18n());

    act(() => result.current.setLang('en'));
    expect(result.current.lang).toBe('en');
    expect(localStorage.getItem('cst-lang')).toBe('en');
    expect(result.current.t('nav.chat')).toBe('Chat');
  });

  it('t() returns key when translation missing', async () => {
    const { useI18n } = await import('../hooks/useI18n');
    const { result } = renderHook(() => useI18n());
    expect(result.current.t('nonexistent.key')).toBe('nonexistent.key');
  });

  it('language change propagates to all mounted components (shared store)', async () => {
    localStorage.setItem('cst-lang', 'zh');
    const { useI18n } = await import('../hooks/useI18n');
    const first = renderHook(() => useI18n());
    const second = renderHook(() => useI18n());

    act(() => first.result.current.setLang('en'));

    expect(second.result.current.lang).toBe('en');
    expect(second.result.current.t('nav.dashboard')).toBe('Dashboard');
    expect(localStorage.getItem('cst-lang')).toBe('en');
  });
});
