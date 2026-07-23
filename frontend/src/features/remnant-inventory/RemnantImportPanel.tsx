import { useState } from 'react';
import { App, Button, Card, Space, Typography, Upload } from 'antd';
import { CloudUploadOutlined, InboxOutlined } from '@ant-design/icons';
import type { UploadFile } from 'antd';
import { useMutation } from '@tanstack/react-query';
import { createRemnantImportBatch } from './api';
import { describeRemnantError } from './errors';
import type { RemnantImportBatch } from './types';

interface Props { onCreated: (batch: RemnantImportBatch) => void }

export function RemnantImportPanel({ onCreated }: Props) {
  const { message } = App.useApp();
  const [fileList, setFileList] = useState<UploadFile[]>([]);
  const create = useMutation({
    mutationFn: () => createRemnantImportBatch(fileList.map((item) => item.originFileObj!).filter(Boolean)),
    onSuccess: (batch) => {
      message.success(`已登记 ${batch.total_count} 张图纸`);
      setFileList([]);
      onCreated(batch);
    },
    onError: (error) => message.error(describeRemnantError(error, '批量导入登记失败，请检查图纸格式')),
  });
  return (
    <Card bordered={false} className="remnant-import-card">
      <div className="remnant-section-heading">
        <div>
          <Typography.Title level={4}>导入余料图纸</Typography.Title>
          <Typography.Text type="secondary">支持同时选择 DWG 与 DXF；不接受 ZIP。厚度在解析后由工人填写。</Typography.Text>
        </div>
      </div>
      <Upload.Dragger
        multiple
        accept=".dwg,.dxf"
        fileList={fileList}
        beforeUpload={() => false}
        onChange={({ fileList: next }) => setFileList(next.filter((item) => /\.(dwg|dxf)$/i.test(item.name)))}
      >
        <p className="ant-upload-drag-icon"><InboxOutlined /></p>
        <p className="ant-upload-text">拖入或选择多张实际余料图纸</p>
        <p className="ant-upload-hint">系统会保留原始文件用于受控下载，DWG 转换出的 DXF 仅用于解析和预览。</p>
      </Upload.Dragger>
      <Space style={{ marginTop: 16 }}>
        <Button
          type="primary"
          icon={<CloudUploadOutlined />}
          disabled={!fileList.length}
          loading={create.isPending}
          onClick={() => create.mutate()}
        >批量导入 {fileList.length ? `（${fileList.length}）` : ''}</Button>
        <Typography.Text type="secondary">每张图纸独立显示转换、解析和失败状态</Typography.Text>
      </Space>
    </Card>
  );
}
