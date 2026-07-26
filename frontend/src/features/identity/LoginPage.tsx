import { useState } from 'react';
import { Button, Form, Input, Typography, App } from 'antd';
import {
  UserOutlined,
  LockOutlined,
  SafetyOutlined,
  ThunderboltOutlined,
  AuditOutlined,
  ApartmentOutlined,
} from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import { login } from '../../shared/auth';
import { describeApiError } from '../../shared/api';
import { useAuthStore } from '../../shared/auth';

interface ApiError {
  response?: {
    status?: number;
    data?: { error?: { code?: string; message?: string } };
  };
  request?: unknown;
  message?: string;
}

/** Map a login failure to a precise, actionable Chinese message.
 *  Mirrors backend error codes from docs/zh/api.md §auth:
 *  - 401 INVALID_CREDENTIALS: bad username/password
 *  - 422: validation (field shape)
 *  - 0/no-response: network or backend down */
function describeError(e: ApiError): string {
  if (e.response) {
    const msg = e.response.data?.error?.message;
    const status = e.response.status;
    if (status === 401) return msg || '账号或密码不正确';
    return describeApiError(e, msg || '登录失败');
  }
  return describeApiError(e, '登录失败，请重试');
}

const HIGHLIGHTS = [
  { icon: <ApartmentOutlined />, title: '项目生产流程', desc: '资料入库后按阶段推进' },
  { icon: <ThunderboltOutlined />, title: '服务器统一处理', desc: '转换、分类、拆板和表格整理' },
  { icon: <AuditOutlined />, title: '过程完整留痕', desc: '输入、结果和关键操作可追溯' },
  { icon: <SafetyOutlined />, title: '账号权限保护', desc: '按岗位控制查看和操作范围' },
];

export function LoginPage() {
  const { message } = App.useApp();
  const navigate = useNavigate();
  const setSession = useAuthStore((s) => s.setSession);
  const [loading, setLoading] = useState(false);

  async function onFinish(values: { username: string; password: string }) {
    setLoading(true);
    try {
      const data = await login(values.username, values.password);
      setSession(data.access_token, data.user);
      message.success(`欢迎回来,${data.user.real_name || data.user.username}`);
      navigate('/dashboard', { replace: true });
    } catch (e) {
      message.error(describeError(e as ApiError));
    } finally {
      setLoading(false);
    }
  }

  return (
    <div style={{ minHeight: '100vh', display: 'flex' }}>
      {/* ── left brand panel ─────────────────────────────────────────────── */}
      <div
        style={{
          flex: '1 1 0',
          minWidth: 0,
          display: 'flex',
          flexDirection: 'column',
          justifyContent: 'space-between',
          padding: '48px 56px',
          background:
            'linear-gradient(135deg, #0b3d91 0%, #1677ff 55%, #13c2c2 100%)',
          color: '#fff',
          position: 'relative',
          overflow: 'hidden',
        }}
      >
        {/* decorative blobs */}
        <div
          style={{
            position: 'absolute',
            top: -120,
            right: -120,
            width: 360,
            height: 360,
            borderRadius: '50%',
            background: 'rgba(255,255,255,0.08)',
          }}
        />
        <div
          style={{
            position: 'absolute',
            bottom: -80,
            left: -80,
            width: 240,
            height: 240,
            borderRadius: '50%',
            background: 'rgba(255,255,255,0.06)',
          }}
        />

        <div style={{ position: 'relative' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <div
              style={{
                width: 44,
                height: 44,
                borderRadius: 10,
                background: 'rgba(255,255,255,0.18)',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                fontSize: 22,
                fontWeight: 700,
              }}
            >
              DW
            </div>
            <Typography.Title level={3} style={{ color: '#fff', margin: 0 }}>
              DWG-Agent
            </Typography.Title>
          </div>
        </div>

        <div style={{ position: 'relative' }}>
          <Typography.Title level={2} style={{ color: '#fff', marginTop: 0 }}>
            企业级 CAD 智能处理平台
          </Typography.Title>
          <Typography.Paragraph style={{ color: 'rgba(255,255,255,0.85)', fontSize: 15, maxWidth: 480 }}>
            接收 DWG/DXF 与工程表格，按任务路由至转换、提取和审核流程，覆盖上传、处理、复核与审计。
          </Typography.Paragraph>

          <div style={{ marginTop: 32, display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16, maxWidth: 480 }}>
            {HIGHLIGHTS.map((h) => (
              <div
                key={h.title}
                style={{
                  background: 'rgba(255,255,255,0.10)',
                  border: '1px solid rgba(255,255,255,0.18)',
                  borderRadius: 10,
                  padding: '14px 16px',
                }}
              >
                <div style={{ fontSize: 18, marginBottom: 6 }}>{h.icon}</div>
                <Typography.Text strong style={{ color: '#fff', display: 'block' }}>
                  {h.title}
                </Typography.Text>
                <Typography.Text style={{ color: 'rgba(255,255,255,0.75)', fontSize: 12 }}>
                  {h.desc}
                </Typography.Text>
              </div>
            ))}
          </div>
        </div>

        <div style={{ position: 'relative', color: 'rgba(255,255,255,0.65)', fontSize: 12 }}>
          生产流程 · 图纸分类 · 整批拆板 · Excel 整理
        </div>
      </div>

      {/* ── right login form ─────────────────────────────────────────────── */}
      <div
        style={{
          flex: '0 0 440px',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          background: '#fff',
          padding: '0 48px',
        }}
      >
        <div style={{ width: '100%', maxWidth: 360 }}>
          <Typography.Title level={2} style={{ marginBottom: 4 }}>
            登录
          </Typography.Title>
          <Typography.Text type="secondary">
            使用平台账号登录；密码变更会立即撤销现有会话。
          </Typography.Text>

          <Form
            layout="vertical"
            onFinish={onFinish}
            autoComplete="on"
            style={{ marginTop: 28 }}
            requiredMark={false}
          >
            <Form.Item
              name="username"
              label="账号"
              rules={[{ required: true, message: '请输入账号' }, { max: 64 }]}
            >
              <Input
                size="large"
                prefix={<UserOutlined />}
                placeholder="用户名 / 工号"
                autoComplete="username"
                autoFocus
              />
            </Form.Item>
            <Form.Item
              name="password"
              label="密码"
              rules={[{ required: true, message: '请输入密码' }]}
            >
              <Input.Password
                size="large"
                prefix={<LockOutlined />}
                placeholder="登录密码"
                autoComplete="current-password"
              />
            </Form.Item>
            <Form.Item style={{ marginBottom: 12 }}>
              <Button
                type="primary"
                htmlType="submit"
                size="large"
                block
                loading={loading}
              >
                登录
              </Button>
            </Form.Item>
          </Form>

          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            <SafetyOutlined /> 登录即创建会话;access_token 30 分钟有效,刷新令牌 14 天
            通过 HttpOnly Cookie 自动续期。
          </Typography.Text>
        </div>
      </div>
    </div>
  );
}
