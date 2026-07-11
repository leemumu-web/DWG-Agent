import { useCallback } from 'react';
import { Alert, Button, Card, Col, Row, Space, Tag, Typography } from 'antd';
import {
  ProjectOutlined,
  FileOutlined,
  ThunderboltOutlined,
  AuditOutlined,
  ReloadOutlined,
  ClockCircleOutlined,
  CheckCircleFilled,
  SyncOutlined,
  CloseCircleFilled,
  CloudUploadOutlined,
  ArrowRightOutlined,
} from '@ant-design/icons';
import { Link, useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { listProjects } from '../../api/projects.api';
import { listFiles } from '../../api/files.api';
import { listJobs } from '../../api/jobs.api';
import { listPendingReviews } from '../../api/reviews.api';
import { useAuthStore } from '../../stores/auth.store';
import { fmtRelative, StatCard, StatGrid, StatusChip, JOB_STATUS } from '../../components/ui';

const STAGE_LABEL = 'Stage 1 · 生产就绪骨架';

export function DashboardPage() {
  const user = useAuthStore((s) => s.user);
  const navigate = useNavigate();

  const projectsQ = useQuery({ queryKey: ['projects'], queryFn: listProjects, staleTime: 10_000 });
  const filesQ = useQuery({ queryKey: ['files'], queryFn: () => listFiles(), staleTime: 5000 });
  const jobsQ = useQuery({ queryKey: ['jobs'], queryFn: () => listJobs(), staleTime: 3000, refetchInterval: 5000 });
  const reviewsQ = useQuery({ queryKey: ['reviews', 'pending'], queryFn: listPendingReviews, staleTime: 10_000 });

  const refresh = useCallback(() => {
    projectsQ.refetch();
    filesQ.refetch();
    jobsQ.refetch();
    reviewsQ.refetch();
  }, [projectsQ, filesQ, jobsQ, reviewsQ]);

  const jobs = jobsQ.data ?? [];
  const succeeded = jobs.filter((j) => j.status === 'succeeded').length;
  const running = jobs.filter((j) => j.status === 'running' || j.status === 'queued').length;
  const failed = jobs.filter((j) => j.status === 'failed').length;
  const recentJobs = jobs.slice(0, 6);

  const anyError = projectsQ.isError || filesQ.isError || jobsQ.isError;

  return (
    <Space direction="vertical" size="large" style={{ width: '100%' }}>
      {/* greeting */}
      <div className="dashboard-hero">
        <div style={{ position: 'relative', zIndex: 1, display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 16 }}>
          <div>
            <Typography.Title level={2} style={{ margin: 0 }}>
              你好，{user?.real_name || user?.username}
            </Typography.Title>
            <Typography.Text className="dashboard-hero-meta">
              欢迎回到 CAD 智能处理工作台 · {STAGE_LABEL}
            </Typography.Text>
          </div>
          <Button ghost icon={<ReloadOutlined />} onClick={refresh} loading={jobsQ.isFetching}>刷新数据</Button>
        </div>
      </div>

      {anyError && (
        <Alert
          type="warning"
          message="部分数据加载失败"
          description="后端服务可能未完全就绪，部分统计可能为空。请检查后端服务状态后刷新。"
          showIcon
          closable
        />
      )}

      {/* stats */}
      <StatGrid>
        <StatCard label="项目" value={projectsQ.data?.length ?? '—'} icon={<ProjectOutlined />} color="#1677ff" bg="#e6f4ff" hint="已加入的项目总数" />
        <StatCard label="文件" value={filesQ.data?.length ?? '—'} icon={<FileOutlined />} color="#722ed1" bg="#f9f0ff" hint="可访问的文件总数" />
        <StatCard label="任务" value={jobs.length} icon={<ThunderboltOutlined />} color="#faad14" bg="#fffbe6" hint={`成功 ${succeeded} · 运行中 ${running} · 失败 ${failed}`} />
        <StatCard label="待复核" value={reviewsQ.data?.length ?? '—'} icon={<AuditOutlined />} color="#13c2c2" bg="#e6fffb" hint="等待人工复核的结果" />
      </StatGrid>

      <Row gutter={16}>
        {/* recent jobs */}
        <Col xs={24} lg={16}>
          <Card
            title={
              <Space>
                <ClockCircleOutlined />
                <span>近期任务</span>
              </Space>
            }
            extra={<Button type="link" onClick={() => navigate('/jobs')}>查看全部 <ArrowRightOutlined /></Button>}
            styles={{ body: { padding: 0 } }}
          >
            {recentJobs.length === 0 ? (
              <div style={{ padding: '40px 0', textAlign: 'center' }}>
                <ThunderboltOutlined style={{ fontSize: 40, color: '#d9d9d9' }} />
                <p style={{ color: '#bfbfbf', marginTop: 12 }}>暂无任务</p>
                <Link to="/files">
                  <Button type="primary" icon={<CloudUploadOutlined />}>上传 DWG 开始</Button>
                </Link>
              </div>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column' }}>
                {recentJobs.map((j) => {
                  const st = JOB_STATUS[j.status] ?? JOB_STATUS.pending;
                  const icon =
                    j.status === 'succeeded' ? <CheckCircleFilled style={{ color: st.color }} /> :
                    j.status === 'failed' ? <CloseCircleFilled style={{ color: st.color }} /> :
                    (j.status === 'running' || j.status === 'queued') ? <SyncOutlined spin style={{ color: st.color }} /> :
                    <ClockCircleOutlined style={{ color: st.color }} />;
                  return (
                    <div
                      key={j.id}
                      onClick={() => navigate('/jobs')}
                      className="dashboard-job-row"
                    >
                      <span style={{ fontSize: 18, width: 24, textAlign: 'center' }}>{icon}</span>
                      <div style={{ flex: 1, minWidth: 0 }}>
                        <div style={{ fontWeight: 500, color: '#1f1f1f' }}>
                          #{j.id} · {j.task_type}
                        </div>
                        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                          {fmtRelative(j.created_at)}
                        </Typography.Text>
                      </div>
                      <StatusChip style={st} />
                      <Typography.Text type="secondary" style={{ fontSize: 13, width: 48, textAlign: 'right' }}>
                        {j.progress}%
                      </Typography.Text>
                    </div>
                  );
                })}
              </div>
            )}
          </Card>
        </Col>

        {/* quick actions + stage info */}
        <Col xs={24} lg={8}>
          <Space direction="vertical" size={16} style={{ width: '100%' }}>
            <Card title="快捷入口" size="small">
              <Space direction="vertical" size={8} style={{ width: '100%' }}>
                <Link to="/files"><Button block icon={<CloudUploadOutlined />}>上传 DWG 文件</Button></Link>
                <Link to="/projects"><Button block icon={<ProjectOutlined />}>我的项目</Button></Link>
                <Link to="/jobs"><Button block icon={<ThunderboltOutlined />}>任务列表</Button></Link>
                <Link to="/reviews"><Button block icon={<AuditOutlined />}>待复核结果</Button></Link>
              </Space>
            </Card>
            <Card title="当前阶段" size="small">
              <Typography.Paragraph style={{ marginBottom: 8, color: '#595959' }}>
                本机开发骨架已保留 <Typography.Text strong>Agent</Typography.Text>、<Typography.Text strong>DXF</Typography.Text>、
                <Typography.Text strong> CAD Worker</Typography.Text> 边界，但暂不实现内部处理。
              </Typography.Paragraph>
              <Space size={6} wrap>
                <Tag color="green">DXF 管线</Tag>
                <Tag>CAD Worker (Stage 4)</Tag>
                <Tag>Agent (Stage 2)</Tag>
              </Space>
            </Card>
          </Space>
        </Col>
      </Row>
    </Space>
  );
}
