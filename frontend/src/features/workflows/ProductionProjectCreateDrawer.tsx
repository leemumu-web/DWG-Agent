import { useEffect } from 'react';
import {
  CheckCircleOutlined,
  CloudServerOutlined,
  CloudUploadOutlined,
  ProjectOutlined,
} from '@ant-design/icons';
import {
  Button,
  Drawer,
  Form,
  Input,
  Space,
  Steps,
  Typography,
} from 'antd';

import type { ProductionProjectCreatePayload } from './workflows.api';

interface Props {
  open: boolean;
  pending: boolean;
  codeError?: string;
  onClose: () => void;
  onCodeChange: () => void;
  onSubmit: (payload: ProductionProjectCreatePayload) => void;
}

export function ProductionProjectCreateDrawer({
  open,
  pending,
  codeError,
  onClose,
  onCodeChange,
  onSubmit,
}: Props) {
  const [form] = Form.useForm<ProductionProjectCreatePayload>();

  useEffect(() => {
    if (!open) form.resetFields();
  }, [form, open]);

  useEffect(() => {
    form.setFields([{ name: 'code', errors: codeError ? [codeError] : [] }]);
  }, [codeError, form]);

  return (
    <Drawer
      title="新建生产项目"
      open={open}
      onClose={onClose}
      width={560}
      closable={!pending}
      maskClosable={!pending}
      destroyOnHidden
    >
      <Form
        className="production-create-form"
        form={form}
        layout="vertical"
        requiredMark={false}
        onFinish={(values) => onSubmit({
          code: values.code.trim().toUpperCase(),
          name: values.name.trim(),
          description: values.description?.trim() || undefined,
        })}
      >
        <section className="production-create-hero" aria-label="创建引导">
          <Typography.Text className="production-create-eyebrow">
            项目 / 一条完整流程
          </Typography.Text>
          <Typography.Title level={4}>一个项目，一条完整生产流程</Typography.Title>
          <Typography.Paragraph>
            创建后立即建立并启动唯一工作流，从资料入库持续推进到交付归档。
          </Typography.Paragraph>
        </section>

        <Steps
          className="production-create-steps"
          size="small"
          current={pending ? 1 : 0}
          responsive
          items={[
            { title: '填写项目资料' },
            { title: '建立完整流程' },
            { title: '上传生产文件夹' },
          ]}
        />

        <div className="production-create-checklist" aria-label="文件准备清单">
          <span><CheckCircleOutlined /> 单个 Excel + 一个 DWG 文件夹</span>
          <span><CloudServerOutlined /> DXF 由服务器统一生成</span>
        </div>

        <div className="production-project-create__identity">
          <div>
            <Typography.Text type="secondary">项目身份</Typography.Text>
            <Typography.Title level={5}>编号贯穿文件、任务与交付归档</Typography.Title>
          </div>
          <ProjectOutlined />
        </div>

        <Form.Item
          name="code"
          label="项目编号"
          normalize={(value: string) => value?.toUpperCase()}
          rules={[
            { required: true, message: '请输入项目编号' },
            {
              pattern: /^[A-Za-z0-9_-]+$/,
              message: '只能使用字母、数字、下划线和连字符',
            },
            { max: 64, message: '项目编号不能超过 64 个字符' },
          ]}
          extra="使用现场已有项目编号；创建后不可与其他项目重复。"
        >
          <Input
            className="production-project-code-input"
            placeholder="例如 P-2026-001"
            autoFocus
            disabled={pending}
            onChange={onCodeChange}
          />
        </Form.Item>

        <Form.Item
          name="name"
          label="项目名称"
          rules={[
            { required: true, message: '请输入项目名称' },
            { max: 128, message: '项目名称不能超过 128 个字符' },
          ]}
        >
          <Input placeholder="例如 一号厂房主结构" disabled={pending} />
        </Form.Item>

        <Form.Item name="description" label="项目说明">
          <Input.TextArea
            rows={4}
            maxLength={1000}
            showCount
            placeholder="可填写生产范围、交付要求或现场说明"
            disabled={pending}
          />
        </Form.Item>

        <div className="production-create-actions">
          <Typography.Text type="secondary">
            项目与工作流原子创建；任一步失败都不会留下空项目。
          </Typography.Text>
          <Space className="production-create-action-buttons" wrap>
            <Button disabled={pending} onClick={onClose}>取消</Button>
            <Button
              htmlType="submit"
              type="primary"
              icon={<CloudUploadOutlined />}
              loading={pending}
            >
              创建项目并进入工作流
            </Button>
          </Space>
        </div>
      </Form>
    </Drawer>
  );
}
