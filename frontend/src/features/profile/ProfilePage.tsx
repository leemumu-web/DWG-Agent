import { useEffect } from 'react';
import { App, Avatar, Button, Card, Col, Descriptions, Form, Input, Row, Space, Tag, Typography } from 'antd';
import { UserOutlined, MailOutlined, EditOutlined, LockOutlined, SafetyOutlined, IdcardOutlined } from '@ant-design/icons';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { changePassword } from '../../api/auth.api';
import { updateSelf } from '../../api/users.api';
import { useAuthStore } from '../../stores/auth.store';
import { PageHeader, roleColor, StatusChip, statusOf, USER_STATUS } from '../../components/ui';

export function ProfilePage() {
  const { message } = App.useApp();
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const user = useAuthStore((s) => s.user);
  const setSession = useAuthStore((s) => s.setSession);
  const clearSession = useAuthStore((s) => s.clearSession);

  const [profileForm] = Form.useForm();
  const [passwordForm] = Form.useForm();

  useEffect(() => {
    if (user) profileForm.setFieldsValue({ real_name: user.real_name, email: user.email ?? '' });
  }, [user, profileForm]);

  const profileMut = useMutation({
    mutationFn: (v: { real_name?: string; email?: string }) => updateSelf(v),
    onSuccess: (updated) => {
      // Preserve token; refresh user in store + cache.
      const token = useAuthStore.getState().accessToken;
      if (token) setSession(token, updated);
      queryClient.invalidateQueries({ queryKey: ['users'] });
      queryClient.invalidateQueries({ queryKey: ['auth', 'me'] });
      message.success('资料已更新');
    },
    onError: (e: unknown) => message.error(e instanceof Error ? e.message : '更新失败'),
  });

  const passwordMut = useMutation({
    mutationFn: (v: { current_password: string; new_password: string }) =>
      changePassword(v.current_password, v.new_password),
    onSuccess: () => {
      message.success('密码已修改，请重新登录');
      passwordForm.resetFields();
      clearSession();
      navigate('/login', { replace: true });
    },
    onError: (e: unknown) => message.error(e instanceof Error ? e.message : '修改失败'),
  });

  if (!user) return null;

  const initial = (user.real_name || user.username).slice(0, 1).toUpperCase();

  return (
    <>
      <PageHeader title="个人中心" subtitle="管理你的资料与登录凭证" />
      <Row gutter={16}>
        <Col xs={24} lg={10}>
          <Card styles={{ body: { padding: 28 } }} style={{ borderRadius: 12, textAlign: 'center' }}>
            <Avatar size={80} style={{ background: '#1677ff', fontSize: 32, marginBottom: 16 }}>{initial}</Avatar>
            <Typography.Title level={4} style={{ margin: 0 }}>{user.real_name}</Typography.Title>
            <Typography.Text type="secondary">@{user.username}</Typography.Text>
            <div style={{ margin: '16px 0' }}>
              <StatusChip style={statusOf(USER_STATUS, user.status)} />
            </div>
            <Space size={6} wrap style={{ justifyContent: 'center' }}>
              {user.roles.map((r) => (
                <Tag key={r.code} color={roleColor(r.code)}>{r.name || r.code}</Tag>
              ))}
            </Space>
            <Descriptions column={1} size="small" style={{ textAlign: 'left', marginTop: 24 }} colon={false}>
              <Descriptions.Item label={<span><IdcardOutlined /> 工号</span>}>{user.employee_no || '—'}</Descriptions.Item>
              <Descriptions.Item label={<span><MailOutlined /> 邮箱</span>}>{user.email || '—'}</Descriptions.Item>
            </Descriptions>
          </Card>
        </Col>

        <Col xs={24} lg={14}>
          <Space direction="vertical" size={16} style={{ width: '100%' }}>
            <Card title={<span><EditOutlined /> 编辑资料</span>} style={{ borderRadius: 12 }}>
              <Form layout="vertical" form={profileForm} onFinish={(v) => profileMut.mutate(v)} requiredMark={false}>
                <Form.Item name="real_name" label="姓名" rules={[{ required: true, message: '请输入姓名' }, { max: 64 }]}>
                  <Input prefix={<UserOutlined />} />
                </Form.Item>
                <Form.Item name="email" label="邮箱" rules={[{ type: 'email', message: '邮箱格式不正确' }]}>
                  <Input prefix={<MailOutlined />} />
                </Form.Item>
                <Form.Item style={{ marginBottom: 0 }}>
                  <Button type="primary" htmlType="submit" loading={profileMut.isPending}>保存</Button>
                </Form.Item>
              </Form>
            </Card>

            <Card title={<span><LockOutlined /> 修改密码</span>} style={{ borderRadius: 12 }}>
              <Typography.Text type="secondary" style={{ fontSize: 12, display: 'block', marginBottom: 12 }}>
                <SafetyOutlined /> 修改后全部现有会话失效，需要重新登录。
              </Typography.Text>
              <Form layout="vertical" form={passwordForm} onFinish={(v) => passwordMut.mutate(v)} requiredMark={false}>
                <Form.Item name="current_password" label="当前密码" rules={[{ required: true, message: '请输入当前密码' }]}>
                  <Input.Password placeholder="当前登录密码" />
                </Form.Item>
                <Form.Item name="new_password" label="新密码" rules={[
                  { required: true, message: '请输入新密码' },
                  { min: 12, message: '至少 12 位' },
                ]} extra="至少 12 位，须含大写、小写、数字">
                  <Input.Password placeholder="如 NewSecurePass123" />
                </Form.Item>
                <Form.Item name="confirm_password" label="确认新密码" dependencies={['new_password']} rules={[
                  { required: true, message: '请再次输入' },
                  ({ getFieldValue }) => ({
                    validator(_, value) {
                      if (!value || getFieldValue('new_password') === value) return Promise.resolve();
                      return Promise.reject(new Error('两次输入不一致'));
                    },
                  }),
                ]}>
                  <Input.Password placeholder="再次输入新密码" />
                </Form.Item>
                <Form.Item style={{ marginBottom: 0 }}>
                  <Button type="primary" htmlType="submit" loading={passwordMut.isPending}>修改密码</Button>
                </Form.Item>
              </Form>
            </Card>
          </Space>
        </Col>
      </Row>
    </>
  );
}
