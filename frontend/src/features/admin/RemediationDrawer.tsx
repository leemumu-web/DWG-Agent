import { useEffect, useState } from 'react';
import axios from 'axios';
import { useMutation } from '@tanstack/react-query';
import { Alert, App, Button, Descriptions, Drawer, Input, Space, Tag, Typography } from 'antd';
import { executeStorageRemediation } from '../../api/data-admin.api';
import type { RemediationPreview, RemediationResult } from '../../types/data-admin';

function formatBytes(value: number) {
  if (!value) return '0 B';
  const units = ['B', 'KiB', 'MiB', 'GiB'];
  const index = Math.min(Math.floor(Math.log(value) / Math.log(1024)), units.length - 1);
  return `${(value / 1024 ** index).toFixed(index ? 1 : 0)} ${units[index]}`;
}

function errorCode(error: unknown) {
  if (!axios.isAxiosError(error)) return undefined;
  return error.response?.data?.error?.code as string | undefined;
}

const ACTION_LABELS: Record<string, string> = {
  restore: '恢复软删除登记',
  register_existing: '补登记现有对象',
  soft_delete_missing: '软删除缺失登记',
  purge_untracked: '永久清理未登记对象',
};

const ACTION_RISKS: Record<string, string> = {
  restore: '将对象仍存在的软删除登记恢复为可用。',
  register_existing: '读取当前对象字节，计算摘要并创建新的 MySQL 文件登记。',
  soft_delete_missing: '将对象已缺失的可用登记标记为软删除。',
  purge_untracked: '永久删除未登记对象；对象字节无法恢复。',
};

export function RemediationDrawer({
  preview,
  open,
  canExecute,
  onClose,
  onExecuted,
}: {
  preview?: RemediationPreview;
  open: boolean;
  canExecute: boolean;
  onClose: () => void;
  onExecuted: (result: RemediationResult) => void;
}) {
  const { message } = App.useApp();
  const [confirmation, setConfirmation] = useState('');
  const [idempotencyKey, setIdempotencyKey] = useState('');
  useEffect(() => {
    if (!preview) return;
    setConfirmation('');
    setIdempotencyKey(globalThis.crypto?.randomUUID?.() ?? `remediation-${Date.now()}`);
  }, [preview]);
  const execute = useMutation({
    mutationFn: () => executeStorageRemediation({
      preview_token: preview!.token,
      idempotency_key: idempotencyKey,
      confirmation_word: confirmation || undefined,
    }),
    onSuccess: (result) => {
      if (result.status === 'succeeded') {
        message.success('处置已完成并写入流转流水');
      } else {
        message.warning(`处置流水状态：${result.status}，请在流转流水中跟进`);
      }
      onExecuted(result);
    },
    onError: (error) => {
      if (errorCode(error) === 'REMEDIATION_PREVIEW_STALE') {
        message.error('目标状态已变化，请重新预检');
        onClose();
        return;
      }
      const code = errorCode(error);
      const messageByCode: Record<string, string> = {
        REMEDIATION_PREVIEW_EXPIRED: '预检已过期，请重新预检',
        REMEDIATION_ALREADY_RESOLVED: '所选异常已被其他操作处置，请刷新列表',
        REMEDIATION_IN_PROGRESS: '同一处置请求仍在执行，请勿重复提交',
        STORAGE_DELETE_FAILED: '对象清理未全部完成，请在流转流水中检查补偿状态',
      };
      message.error(messageByCode[code ?? ''] ?? '处置执行失败，数据库与对象状态未通过校验');
    },
  });
  const confirmationReady = !preview?.confirmation_word
    || confirmation === preview.confirmation_word;

  return <Drawer title="一致性处置预检" open={open} onClose={onClose} size={520} destroyOnHidden>
    {preview && <Space orientation="vertical" size={18} style={{ width: '100%' }}>
      <Alert
        type={preview.action === 'purge_untracked' ? 'warning' : 'info'}
        showIcon
        title="执行前会再次锁定登记并检查对象状态"
        description={ACTION_RISKS[preview.action] ?? preview.risk}
      />
      <Descriptions column={1} bordered size="small" items={[
        { key: 'action', label: '动作', children: <Tag>{ACTION_LABELS[preview.action] ?? preview.action}</Tag> },
        { key: 'count', label: '影响范围', children: `${preview.count} 项` },
        { key: 'bytes', label: '影响字节', children: formatBytes(preview.total_bytes) },
        { key: 'expires', label: '预检失效', children: new Date(preview.expires_at).toLocaleString() },
      ]} />
      {preview.confirmation_word && <div>
        <Typography.Text strong>确认词</Typography.Text>
        <Typography.Paragraph type="secondary">
          永久清理未登记对象需要输入 <Typography.Text code>{preview.confirmation_word}</Typography.Text>
        </Typography.Paragraph>
        <Input
          aria-label="确认词"
          value={confirmation}
          onChange={(event) => setConfirmation(event.target.value)}
          placeholder={preview.confirmation_word}
        />
      </div>}
      {!canExecute && <Alert type="info" showIcon title="审计员仅可预检；请由管理员重新预检并执行。" />}
      <Space>
        <Button onClick={onClose}>取消</Button>
        <Button
          type="primary"
          danger={preview.action === 'purge_untracked'}
          disabled={!canExecute || !confirmationReady || !idempotencyKey}
          loading={execute.isPending}
          onClick={() => execute.mutate()}
        >确认执行</Button>
      </Space>
    </Space>}
  </Drawer>;
}
