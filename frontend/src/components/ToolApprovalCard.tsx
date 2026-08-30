import { useState } from 'react';
import { AlertTriangle } from 'lucide-react';
import { decideToolApproval, type ToolApprovalRequest } from '../api';
import { Button } from './Button';

export function ToolApprovalCard({ request }: { request: ToolApprovalRequest }) {
  const [status, setStatus] = useState<'pending' | 'executed' | 'failed' | 'rejected' | 'error'>('pending');
  const [error, setError] = useState('');

  const decide = async (decision: 'approve' | 'reject') => {
    try {
      const result = await decideToolApproval(request.requestId, decision);
      setStatus(
        result.rejected
          ? 'rejected'
          : result.approved && result.executed
            ? 'executed'
            : result.approved
              ? 'failed'
              : 'error',
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setStatus('error');
    }
  };

  return (
    <div className="mt-3 rounded-xl border border-warning/30 bg-warning-subtle p-3 text-sm">
      <div className="flex items-center gap-2 font-semibold text-warning">
        <AlertTriangle size={16} />
        高风险工具等待确认
      </div>
      <div className="mt-2 text-text-secondary">
        <span className="font-mono text-text">{request.toolName}</span>
        <pre className="mt-2 max-h-32 overflow-auto whitespace-pre-wrap rounded-lg bg-surface p-2 text-xs text-text-secondary">
          {request.preview || '(no preview)'}
        </pre>
      </div>
      {status === 'pending' ? (
        <div className="mt-3 flex gap-2">
          <Button variant="danger" onClick={() => decide('approve')}>批准一次</Button>
          <Button variant="ghost" onClick={() => decide('reject')}>拒绝</Button>
        </div>
      ) : (
        <p className="mt-3 text-text-secondary">
          {status === 'executed' && '已按确认时的精确参数执行一次。'}
          {status === 'failed' && '调用已获批并执行，但工具返回失败；如需重试必须重新确认。'}
          {status === 'rejected' && '已拒绝该调用。'}
          {status === 'error' && `审批失败：${error || 'unknown error'}`}
        </p>
      )}
    </div>
  );
}
