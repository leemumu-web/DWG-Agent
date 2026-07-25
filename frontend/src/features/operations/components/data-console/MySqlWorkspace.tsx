import { useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import { Alert, Button, Card, Descriptions, Space, Tag, Typography } from 'antd';
import { DatabaseOutlined, ExportOutlined } from '@ant-design/icons';

import { createMySqlConsoleSession } from '../../api/dataAdmin';

interface Props {
  canManage: boolean;
}

export function MySqlWorkspace({ canManage }: Props) {
  const [consoleUrl, setConsoleUrl] = useState<string | null>(null);
  const session = useMutation({
    mutationFn: createMySqlConsoleSession,
    onSuccess: ({ url }) => setConsoleUrl(url),
  });

  return (
    <div className="database-workspace">
      <Card className="console-table-card">
        <div className="database-workspace-intro">
          <div className="database-workspace-icon"><DatabaseOutlined /></div>
          <div>
            <Typography.Title level={4}>MySQL 数据库</Typography.Title>
            <Typography.Paragraph type="secondary">
              查看库、表、字段、索引和数据记录。数据库管理器只连接业务库 dwg_agent。
            </Typography.Paragraph>
          </div>
          <Tag color={canManage ? 'processing' : 'default'}>
            {canManage ? '可增删改查' : '只读检查'}
          </Tag>
        </div>
        <Descriptions
          size="small"
          column={{ xs: 1, sm: 2, lg: 3 }}
          items={[
            { key: 'engine', label: '引擎', children: 'MySQL 8.4' },
            { key: 'database', label: '数据库', children: <Typography.Text code>dwg_agent</Typography.Text> },
            { key: 'scope', label: '权限范围', children: canManage ? '业务库完整操作' : '查询与结构检查' },
          ]}
        />
        <Space wrap style={{ marginTop: 20 }}>
          <Button
            type="primary"
            icon={<DatabaseOutlined />}
            loading={session.isPending}
            onClick={() => session.mutate()}
          >
            {consoleUrl ? '重新连接' : '打开数据库管理器'}
          </Button>
          {consoleUrl && (
            <Button href={consoleUrl} target="_blank" icon={<ExportOutlined />}>
              新窗口打开
            </Button>
          )}
        </Space>
        {session.isError && (
          <Alert
            style={{ marginTop: 16 }}
            type="error"
            showIcon
            title="数据库管理会话创建失败"
            description="请重新登录后再试；若仍失败，请检查 CloudBeaver 服务。"
          />
        )}
      </Card>
      {consoleUrl && (
        <Card className="console-table-card database-frame-card" title="数据库结构与数据">
          <iframe title="MySQL 数据库管理器" src={consoleUrl} className="database-frame" />
        </Card>
      )}
    </div>
  );
}
