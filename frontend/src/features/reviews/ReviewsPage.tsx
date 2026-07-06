import { useMemo, useState } from 'react';
import {
  App,
  Button,
  Descriptions,
  Drawer,
  Empty,
  Form,
  Input,
  Progress,
  Radio,
  Space,
  Table,
  Tag,
  Tooltip,
  Typography,
} from 'antd';
import {
  ReloadOutlined,
  AuditOutlined,
  CheckOutlined,
  CloseOutlined,
  EditOutlined,
  SearchOutlined,
  EyeOutlined,
  SafetyCertificateOutlined,
} from '@ant-design/icons';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import {
  listPendingReviews,
  listResultReviews,
  submitReview,
  type ReviewRecord,
} from '../../api/reviews.api';
import type { AnalysisResult } from '../../types/result';
import {
  fmtConfidence,
  fmtDateTime,
  fmtRelative,
  PageHeader,
  StatCard,
  StatGrid,
} from '../../components/ui';

type Decision = 'approved' | 'rejected' | 'needs_revision';

const DECISION_META: Record<Decision, { label: string; color: string; icon: React.ReactNode }> = {
  approved: { label: '批准', color: 'success', icon: <CheckOutlined /> },
  rejected: { label: '驳回', color: 'error', icon: <CloseOutlined /> },
  needs_revision: { label: '需修改', color: 'warning', icon: <EditOutlined /> },
};

function confidenceStatus(c?: number | null): 'success' | 'exception' | 'active' {
  if (c == null) return 'active';
  if (c >= 0.9) return 'success';
  if (c < 0.6) return 'exception';
  return 'active';
}

export function ReviewsPage() {
  const { message } = App.useApp();
  const queryClient = useQueryClient();
  const reviewsQ = useQuery({ queryKey: ['reviews', 'pending'], queryFn: listPendingReviews });

  const [search, setSearch] = useState('');
  const [detail, setDetail] = useState<AnalysisResult | null>(null);
  const [history, setHistory] = useState<ReviewRecord[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [reviewForm] = Form.useForm();
  const [submitting, setSubmitting] = useState(false);

  const results = reviewsQ.data ?? [];

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return results;
    return results.filter(
      (r) => r.result_type.toLowerCase().includes(q) || String(r.id).includes(q) || String(r.job_id).includes(q),
    );
  }, [results, search]);

  const lowConfidence = results.filter((r) => r.confidence != null && r.confidence < 0.6).length;
  const highConfidence = results.filter((r) => r.confidence != null && r.confidence >= 0.9).length;

  async function loadHistory(r: AnalysisResult) {
    setDetail(r);
    setHistoryLoading(true);
    try {
      setHistory(await listResultReviews(r.id));
    } catch {
      setHistory([]);
    }
    setHistoryLoading(false);
  }

  async function handleSubmit(v: { decision: Decision; comment?: string }) {
    if (!detail) return;
    setSubmitting(true);
    try {
      await submitReview(detail.id, v);
      message.success(`已${DECISION_META[v.decision].label}`);
      queryClient.invalidateQueries({ queryKey: ['reviews', 'pending'] });
      // Refresh history, then close if no longer pending
      const h = await listResultReviews(detail.id);
      setHistory(h);
      setDetail(null);
      reviewForm.resetFields();
    } catch (e: unknown) {
      message.error(e instanceof Error ? e.message : '提交失败');
    }
    setSubmitting(false);
  }

  const columns = [
    { title: '#', dataIndex: 'id', width: 60, align: 'center' as const },
    {
      title: '任务', dataIndex: 'job_id', width: 90,
      render: (v: number) => <Typography.Text code>#{v}</Typography.Text>,
    },
    {
      title: '结果类型', dataIndex: 'result_type',
      render: (v: string) => <Tag color="purple">{v}</Tag>,
    },
    {
      title: '置信度', dataIndex: 'confidence', width: 200,
      render: (c?: number | null) => (
        <Space size={8}>
          <Progress
            percent={c != null ? Math.round(c * 100) : 0}
            size="small"
            style={{ width: 100, margin: 0 }}
            status={confidenceStatus(c)}
            format={() => fmtConfidence(c)}
          />
          {c != null && c < 0.6 && <Tag color="error">低</Tag>}
          {c != null && c >= 0.9 && <Tag color="success">高</Tag>}
        </Space>
      ),
    },
    {
      title: '状态', dataIndex: 'status', width: 110,
      render: (v: string) => <Tag color="warning">{v === 'need_review' ? '待复核' : v}</Tag>,
    },
    {
      title: '生成时间', dataIndex: 'created_at', width: 150,
      render: (v: string) => (
        <Tooltip title={fmtDateTime(v)}>
          <Typography.Text type="secondary" style={{ fontSize: 13 }}>{fmtRelative(v)}</Typography.Text>
        </Tooltip>
      ),
    },
    {
      title: '操作', width: 90, align: 'center' as const,
      render: (_: unknown, r: AnalysisResult) => (
        <Button type="primary" size="small" icon={<EyeOutlined />} onClick={() => loadHistory(r)}>
          复核
        </Button>
      ),
    },
  ];

  return (
    <>
      <PageHeader
        title="待复核结果"
        subtitle="对低置信度分析结果进行人工复核"
        extra={
          <Space>
            <Input
              allowClear
              prefix={<SearchOutlined />}
              placeholder="搜索结果类型 / ID"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              style={{ width: 240 }}
            />
            <Button icon={<ReloadOutlined />} onClick={() => reviewsQ.refetch()} loading={reviewsQ.isFetching} />
          </Space>
        }
      />

      <StatGrid>
        <StatCard label="待复核" value={results.length} icon={<AuditOutlined />} color="#faad14" bg="#fffbe6" />
        <StatCard label="高置信度 (≥90%)" value={highConfidence} icon={<SafetyCertificateOutlined />} color="#52c41a" bg="#f6ffed" />
        <StatCard label="低置信度 (<60%)" value={lowConfidence} icon={<AuditOutlined />} color="#ff4d4f" bg="#fff2f0" />
      </StatGrid>

      <Table
        rowKey="id"
        dataSource={filtered}
        columns={columns}
        loading={reviewsQ.isLoading}
        size="middle"
        pagination={{ pageSize: 15, showSizeChanger: true, showTotal: (t) => `共 ${t} 条待复核` }}
        locale={{ emptyText: <Empty description="暂无待复核结果 🎉" /> }}
        style={{ background: '#fff', borderRadius: 10 }}
      />

      <Drawer
        title={detail ? `复核结果 #${detail.id}` : '结果复核'}
        open={detail !== null}
        onClose={() => { setDetail(null); setHistory([]); reviewForm.resetFields(); }}
        width={560}
        loading={historyLoading}
      >
        {detail && (
          <>
            <Descriptions column={1} size="small" bordered style={{ marginBottom: 20 }}>
              <Descriptions.Item label="结果 ID">{detail.id}</Descriptions.Item>
              <Descriptions.Item label="所属任务"><Typography.Text code>#{detail.job_id}</Typography.Text></Descriptions.Item>
              {detail.drawing_id && <Descriptions.Item label="所属图纸"><Typography.Text code>#{detail.drawing_id}</Typography.Text></Descriptions.Item>}
              <Descriptions.Item label="结果类型"><Tag color="purple">{detail.result_type}</Tag></Descriptions.Item>
              <Descriptions.Item label="置信度">
                <Space>
                  <Progress
                    percent={detail.confidence != null ? Math.round(detail.confidence * 100) : 0}
                    size="small"
                    style={{ width: 120, margin: 0 }}
                    status={confidenceStatus(detail.confidence)}
                    format={() => fmtConfidence(detail.confidence)}
                  />
                  {detail.confidence != null && detail.confidence < 0.6 && <Tag color="error">低于阈值</Tag>}
                </Space>
              </Descriptions.Item>
              <Descriptions.Item label="生成时间">{fmtDateTime(detail.created_at)}</Descriptions.Item>
            </Descriptions>

            {/* review history */}
            <Typography.Title level={5}>审核历史 ({history.length})</Typography.Title>
            {history.length === 0 ? (
              <Empty description="暂无审核记录" image={Empty.PRESENTED_IMAGE_SIMPLE} style={{ marginBottom: 20 }} />
            ) : (
              <div style={{ marginBottom: 20 }}>
                {history.map((h) => {
                  const meta = DECISION_META[h.decision as Decision] ?? { label: h.decision, color: 'default', icon: null };
                  return (
                    <div key={h.id} style={{ padding: '10px 0', borderBottom: '1px solid #f5f5f5' }}>
                      <Space style={{ marginBottom: 4 }}>
                        <Tag color={meta.color}>{meta.icon} {meta.label}</Tag>
                        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                          审核人 #{h.reviewer_id ?? '?'} · {fmtRelative(h.created_at)}
                        </Typography.Text>
                      </Space>
                      {h.comment && <div style={{ color: '#595959', fontSize: 13 }}>{h.comment}</div>}
                    </div>
                  );
                })}
              </div>
            )}

            {/* submit review */}
            <Typography.Title level={5}>提交复核</Typography.Title>
            <Form
              layout="vertical"
              form={reviewForm}
              onFinish={handleSubmit}
              initialValues={{ decision: 'approved' }}
            >
              <Form.Item name="decision" label="决定" rules={[{ required: true }]}>
                <Radio.Group buttonStyle="solid">
                  <Radio.Button value="approved"><CheckOutlined /> 批准</Radio.Button>
                  <Radio.Button value="needs_revision"><EditOutlined /> 需修改</Radio.Button>
                  <Radio.Button value="rejected"><CloseOutlined /> 驳回</Radio.Button>
                </Radio.Group>
              </Form.Item>
              <Form.Item name="comment" label="备注">
                <Input.TextArea rows={3} placeholder="审核说明（选填）" maxLength={1000} showCount />
              </Form.Item>
              <Form.Item style={{ marginBottom: 0 }}>
                <Space>
                  <Button type="primary" htmlType="submit" loading={submitting}>提交</Button>
                  <Button onClick={() => { setDetail(null); reviewForm.resetFields(); }}>取消</Button>
                </Space>
              </Form.Item>
            </Form>
          </>
        )}
      </Drawer>
    </>
  );
}
