import { Component, type ErrorInfo, type ReactNode } from 'react';

type Props = {
  children: ReactNode;
};

type State = {
  error: Error | null;
};

export class AppErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('CST-Agent UI crashed', error, info);
  }

  render() {
    if (!this.state.error) {
      return this.props.children;
    }

    return (
      <div className="surface p-6 min-h-[360px] flex flex-col justify-center">
        <div className="max-w-2xl">
          <span className="text-[0.78rem] font-semibold uppercase text-danger tracking-[0.12em]">
            UI Error
          </span>
          <h1 className="text-2xl font-bold text-text mt-3">页面运行时出错</h1>
          <p className="text-text-secondary mt-3 leading-7">
            前端遇到了一个未处理异常，但后端仿真任务不一定失败。可以先刷新页面恢复界面，再到 Trace 查看刚才的工具事件。
          </p>
          <pre className="mt-4 max-h-40 overflow-auto rounded-xl border border-border bg-elevated p-4 text-[0.78rem] text-text-muted whitespace-pre-wrap">
            {this.state.error.message || String(this.state.error)}
          </pre>
          <button
            className="btn btn-primary mt-5"
            onClick={() => window.location.reload()}
          >
            重新加载
          </button>
        </div>
      </div>
    );
  }
}
