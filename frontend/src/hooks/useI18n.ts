import { useCallback, useMemo, useSyncExternalStore } from 'react';

export type Lang = 'zh' | 'en';

const translations: Record<string, Record<Lang, string>> = {
  // NavBar
  'nav.dashboard': { zh: '仪表盘', en: 'Dashboard' },
  'nav.chat': { zh: '对话', en: 'Chat' },
  'nav.trace': { zh: '观测', en: 'Trace' },
  'nav.mission': { zh: '当前目标', en: 'Mission' },
  'nav.subtitle': { zh: '自然语言到 CST 仿真的可观测工作台', en: 'Observable NL-to-CST simulation workbench' },

  // Dashboard project bar
  'dash.connected': { zh: 'CST 已连接', en: 'CST Connected' },
  'dash.offline': { zh: 'CST 未连接', en: 'CST Offline' },
  'dash.demoMode': { zh: 'Demo mode', en: 'Demo mode' },
  'dash.liveMode': { zh: 'Live CST', en: 'Live CST' },
  'dash.livePending': { zh: 'Live CST 待连接', en: 'Live CST pending' },
  'dash.reconnect': { zh: '重新连接', en: 'Reconnect' },
  'dash.reconnecting': { zh: '连接中...', en: 'Connecting...' },
  'dash.simulate': { zh: '运行仿真', en: 'Simulate' },
  'dash.simulating': { zh: '仿真中...', en: 'Running...' },
  'dash.readS11': { zh: '读取 S11', en: 'Read S11' },
  'dash.reading': { zh: '读取中...', en: 'Reading...' },
  'dash.optimize': { zh: '优化一轮', en: 'Optimize' },
  'dash.optimizing': { zh: '优化中...', en: 'Optimizing...' },
  'dash.rollback': { zh: '回退最优', en: 'Rollback best' },
  'dash.clear': { zh: '清空', en: 'Clear' },
  'dash.projectPath': { zh: '工程路径', en: 'Project path' },
  'dash.noProject': { zh: '尚未绑定 CST 工程', en: 'No CST project bound yet' },
  'dash.refreshFailed': { zh: 'Dashboard 数据刷新失败', en: 'Dashboard refresh failed' },
  'dash.cleared': { zh: '对话和运行状态已清空', en: 'Conversation and runtime state cleared' },

  // Dashboard metric cards
  'dash.target': { zh: '目标', en: 'Target' },
  'dash.bestMetric': { zh: '最优指标', en: 'Best Metric' },
  'dash.modelAssets': { zh: '模型资产', en: 'Model Assets' },
  'dash.agentTrace': { zh: 'Agent 轨迹', en: 'Agent Trace' },
  'dash.calls': { zh: '次调用', en: 'calls' },
  'dash.failed': { zh: '次失败', en: 'failed' },
  'dash.params': { zh: '个参数', en: 'parameters' },

  // Dashboard workflow rail
  'dash.step.task': { zh: 'Planner', en: 'Planner' },
  'dash.step.model': { zh: 'Executor', en: 'Executor' },
  'dash.step.simulate': { zh: 'Tool Calling', en: 'Tool Calling' },
  'dash.step.optimize': { zh: 'Simulation', en: 'Simulation' },
  'dash.step.evidence': { zh: 'Result', en: 'Result' },

  // Dashboard S11 panel
  'dash.s11.title': { zh: 'S11 曲线', en: 'S11 Curve' },
  'dash.s11.subtitle': { zh: '仿真结果', en: 'Simulation Result' },
  'dash.s11.noData': { zh: '暂无 S11 数据', en: 'No S11 data yet' },
  'dash.s11.loading': { zh: '正在加载...', en: 'Loading...' },
  'dash.s11.minimum': { zh: '最小值', en: 'Minimum' },
  'dash.s11.target': { zh: '目标频点', en: 'Target' },
  'dash.s11.points': { zh: '数据点', en: 'Points' },
  'dash.s11.ptsLabel': { zh: 'CST 数据点', en: 'CST data points' },

  // Dashboard evidence panel
  'dash.evidence.title': { zh: 'Trace / Evidence', en: 'Trace / Evidence' },
  'dash.evidence.subtitle': { zh: '可观测执行过程', en: 'Observable execution' },
  'dash.evidence.strategy': { zh: '上次策略', en: 'Last strategy' },
  'dash.evidence.round': { zh: '轮次', en: 'Round' },
  'dash.evidence.best': { zh: '最优轮', en: 'Best' },
  'dash.evidence.memory': { zh: '记忆', en: 'Memory' },
  'dash.evidence.tools': { zh: '工具', en: 'Tools' },
  'dash.evidence.recent': { zh: '最近工具事件', en: 'Recent Tool Events' },
  'dash.evidence.noEvents': { zh: '暂无工具事件', en: 'No tool events yet' },

  // Chat
  'chat.workspace': { zh: 'CST 任务工作区', en: 'CST Task Workspace' },
  'chat.running': { zh: '执行中', en: 'Running' },
  'chat.ready': { zh: '就绪', en: 'Ready' },
  'chat.loading': { zh: '加载中', en: 'Loading' },
  'chat.demoMode': { zh: '演示模式', en: 'Demo mode' },
  'chat.notRun': { zh: '尚未执行', en: 'Not run yet' },
  'chat.fillDemo': { zh: '填入演示 Prompt', en: 'Use demo prompt' },
  'chat.emptyTitle': { zh: '用自然语言驱动 CST 建模与仿真', en: 'Drive CST modeling and simulation with natural language' },
  'chat.emptyHint': { zh: '选择一个演示请求，或直接描述你要创建的天线。', en: 'Pick a demo request or describe the antenna you want to create.' },
  'chat.placeholder': { zh: '输入消息，例如：创建一个 9.4GHz Rogers5880 矩形贴片天线...', en: 'Type a message, e.g. create a 9.4GHz Rogers5880 rectangular patch...' },
  'chat.send': { zh: '发送', en: 'Send' },
  'chat.thinking': { zh: 'Agent 正在规划和调用工具...', en: 'Agent is planning and calling tools...' },
  'chat.toolEvents': { zh: '工具轨迹', en: 'Tool trace' },
  'chat.hydrating': { zh: '正在加载历史...', en: 'Loading history...' },
  'chat.refresh': { zh: '刷新', en: 'Refresh' },
  'chat.noS11': { zh: '暂无 S11 数据', en: 'No S11 data available' },
  'chat.noFarfield': { zh: '暂无远场数据', en: 'No farfield data available' },
  'chat.minS11': { zh: '最小 S11', en: 'Min S11' },
  'chat.minFreq': { zh: '最小频率', en: 'Min Freq' },
  'chat.bw': { zh: '带宽 @ -10dB', en: 'BW @ -10dB' },
  'chat.targetS11': { zh: '目标 S11', en: 'Target S11' },
  'chat.peakGain': { zh: '峰值增益', en: 'Peak Gain' },
  'chat.dataPoints': { zh: '个远场数据点', en: 'farfield data points' },
};

function initialLang(): Lang {
  const saved = localStorage.getItem('cst-lang');
  if (saved === 'zh' || saved === 'en') return saved;
  return navigator.language.startsWith('zh') ? 'zh' : 'en';
}

// 模块级共享 store：语言是全局状态。此前每个组件各持一份 useState，
// 切换语言只会重渲染 NavBar 自己，已挂载页面不会跟随切换。
let currentLang: Lang = initialLang();
const listeners = new Set<() => void>();

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

function getSnapshot(): Lang {
  return currentLang;
}

function setLanguage(l: Lang): void {
  localStorage.setItem('cst-lang', l);
  if (l === currentLang) return;
  currentLang = l;
  listeners.forEach(listener => listener());
}

export function useI18n() {
  const lang = useSyncExternalStore(subscribe, getSnapshot);

  const t = useCallback((key: string): string => {
    return translations[key]?.[lang] ?? key;
  }, [lang]);

  return useMemo(() => ({ lang, setLang: setLanguage, t }), [lang, t]);
}
