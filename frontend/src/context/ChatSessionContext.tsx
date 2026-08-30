import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type Dispatch,
  type ReactNode,
  type SetStateAction,
} from 'react';
import {
  fetchChatHistory,
  stateClear,
  streamChat,
  type ChatHistoryMessage,
  type ToolEvent,
} from '../api';

export type ChatMessage = ChatHistoryMessage;

type ChatSessionContextValue = {
  messages: ChatMessage[];
  pendingEvents: ToolEvent[];
  sending: boolean;
  hydrated: boolean;
  draft: string;
  setDraft: Dispatch<SetStateAction<string>>;
  sendMessage: (text: string) => Promise<void>;
  clearMessages: () => Promise<void>;
};

const ChatSessionContext = createContext<ChatSessionContextValue | null>(null);

export function ChatSessionProvider({ children }: { children: ReactNode }) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [pendingEvents, setPendingEvents] = useState<ToolEvent[]>([]);
  const [sending, setSending] = useState(false);
  const [hydrated, setHydrated] = useState(false);
  const [draft, setDraft] = useState('');
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchChatHistory()
      .then(history => {
        if (!cancelled) setMessages(history);
      })
      .catch(() => {
        // Hydration failure is non-fatal — start with empty list so the user can still chat.
      })
      .finally(() => {
        if (!cancelled) setHydrated(true);
      });
    return () => {
      cancelled = true;
      abortRef.current?.abort();
    };
  }, []);

  const sendMessage = useCallback(async (text: string) => {
    const trimmed = text.trim();
    if (!trimmed || !hydrated || sending) return;

    setMessages(prev => [...prev, { role: 'user', content: trimmed }]);
    setPendingEvents([]);
    setSending(true);

    const controller = new AbortController();
    abortRef.current = controller;

    const collected: ToolEvent[] = [];
    let replied = false;

    try {
      await streamChat(
        trimmed,
        {
          onToolEvent: (evt) => {
            collected.push(evt);
            setPendingEvents(prev => [...prev, evt]);
          },
          onReply: (content) => {
            replied = true;
            setMessages(prev => [
              ...prev,
              { role: 'assistant', content, toolEvents: collected.length ? [...collected] : undefined },
            ]);
          },
          onError: (msg) => {
            replied = true;
            setMessages(prev => [
              ...prev,
              { role: 'assistant', content: `Error: ${msg}`, toolEvents: collected.length ? [...collected] : undefined },
            ]);
          },
          onDone: () => {
            // Clear in-flight pending events; the per-turn events now live on the assistant message.
            setPendingEvents([]);
          },
        },
        controller.signal,
      );
      if (!replied) {
        setMessages(prev => [
          ...prev,
          { role: 'assistant', content: 'Stream ended without reply', toolEvents: collected.length ? [...collected] : undefined },
        ]);
      }
    } catch (err: any) {
      if (err?.name !== 'AbortError') {
        setMessages(prev => [
          ...prev,
          { role: 'assistant', content: `Request failed: ${err?.message || String(err)}`, toolEvents: collected.length ? [...collected] : undefined },
        ]);
      }
    } finally {
      setSending(false);
      abortRef.current = null;
    }
  }, [hydrated, sending]);

  const clearMessages = useCallback(async () => {
    abortRef.current?.abort();
    setMessages([]);
    setPendingEvents([]);
    setSending(false);
    await stateClear();
  }, []);

  const value = useMemo<ChatSessionContextValue>(() => ({
    messages,
    pendingEvents,
    sending,
    hydrated,
    draft,
    setDraft,
    sendMessage,
    clearMessages,
  }), [messages, pendingEvents, sending, hydrated, draft, sendMessage, clearMessages]);

  return <ChatSessionContext.Provider value={value}>{children}</ChatSessionContext.Provider>;
}

export function useChatSession() {
  const value = useContext(ChatSessionContext);
  if (!value) {
    throw new Error('useChatSession must be used within ChatSessionProvider');
  }
  return value;
}
