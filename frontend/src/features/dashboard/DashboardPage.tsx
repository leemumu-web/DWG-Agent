import { useCallback, useMemo } from 'react';
import {
  Alert,
  Button,
  Card,
  Collapse,
  Empty,
  Progress,
  Space,
  Steps,
  Table,
  Typography,
} from 'antd';
import {
  ApartmentOutlined,
  ArrowRightOutlined,
  CheckCircleOutlined,
  ClockCircleOutlined,
  ExclamationCircleOutlined,
  FileDoneOutlined,
  FolderOpenOutlined,
  InfoCircleOutlined,
  PlusOutlined,
  ProjectOutlined,
  ReloadOutlined,
  SafetyCertificateOutlined,
  ScissorOutlined,
  TagsOutlined,
} from '@ant-design/icons';
import { Link, useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';

import { useAuthStore } from '../../shared/auth';
import {
  fmtDateTime,
  StatCard,
  StatGrid,
  statusOf,
  StatusChip,
} from '../../shared/components';
import {
  listWorkflows,
  listWorkflowTemplates,
  WORKFLOW_STATUS,
  type WorkflowRun,
} from '../workflows';

const FALLBACK_STAGE_NAMES: Record<string, string> = {
  source_intake: '资料入库',
  dxf_classification: '图纸分类',
  drawing_processing: '整批拆板',
  excel_stage1: 'Excel 整理',
};

const OPERATION_STEPS = [
  {
    title: '建立生产项目',
    description: '填写项目编号和名称，系统建立一条可追踪的生产流程。',
    icon: <ProjectOutlined />,
  },
  {
    title: '资料入库',
    description: '上传一份 Tekla 原始 Excel 和一个 DWG 图纸文件夹，由服务器统一转换 DXF。',
    icon: <FolderOpenOutlined />,
  },
  {
    title: '分类与拆板',
    description: '先核对分类结果，再对确认的 BH、BOX 图纸执行整批拆板。',
    icon: <ScissorOutlined />,
  },
  {
    title: 'Excel 整理',
    description: '使用冻结输入和正式拆板结果生成整理表与 part 表，并下载单个 Excel。',
    icon: <FileDoneOutlined />,
  },
];

function ManualList({ title, items }: { title: string; items: string[] }) {
  return (
    <section className="dashboard-manual-list">
      <Typography.Text strong>{title}</Typography.Text>
      <ol>
        {items.map((item) => <li key={item}>{item}</li>)}
      </ol>
    </section>
  );
}

const MANUAL_SECTIONS = [
  {
    key: 'prepare',
    label: '1. 建立项目与准备资料',
    children: (
      <div className="dashboard-manual-panel">
        <Alert
          type="info"
          showIcon
          message="目标：先把项目身份和本批原始资料对准，避免后续结果串项目。"
        />
        <div className="dashboard-manual-grid">
          <ManualList title="准备什么" items={[
            '一份 Tekla 构件零件清单原表，使用 .xls 或 .xlsx；不要把系统以前生成的整理表、part 表当成新输入。',
            '一个完整的 DWG 图纸文件夹。文件夹里可以有子目录，但一次只能选择一个最外层文件夹。',
            '项目编号和项目名称。二者应与图纸标题栏、排版任务单一致；一个生产项目只对应一批资料。',
          ]} />
          <ManualList title="怎么做" items={[
            '点击“新建生产项目”，填写项目编号和项目名称，确认无误后创建。',
            '进入项目后先看页面顶部的当前阶段；只有“资料入库”阶段可以上传或清空本批输入。',
            '同一批资料中途换人操作时，直接进入原生产项目继续，不要另建同名项目。',
          ]} />
          <ManualList title="必须核对" items={[
            '项目编号、项目名称、Excel 文件名和 DWG 文件夹名是否属于同一工程。',
            'Excel 第一张工作表是否确实是 Tekla 原表；多工作表文件会只取第一张，其余页被忽略并提前提醒。',
            '若选错项目或资料，必须在输入冻结前清空并重传；冻结后不要靠重新上传覆盖。',
          ]} />
        </div>
      </div>
    ),
  },
  {
    key: 'intake',
    label: '2. 资料上传与入库冻结',
    children: (
      <div className="dashboard-manual-panel">
        <Alert
          type="warning"
          showIcon
          message="一个项目只上传一份 Tekla 原始表；DWG 必须按完整文件夹上传。"
        />
        <div className="dashboard-manual-grid">
          <ManualList title="上传顺序" items={[
            '先上传 Excel。页面显示“已校验”后，再选择 DWG 文件夹；上传和下载大批文件时以页面进度条为准。',
            '文件夹超过 1000 个文件时，只接收浏览器列出的前 1000 个，其余文件不上传并显示红色警告；此时要先确认被忽略的文件是否属于本批。',
            '文件夹中的非 DWG 文件会单独提示。确认忽略后，只有 DWG 进入生产输入。',
            '点击转换后由服务器把 DWG 转成 DXF；不要在此阶段人工上传自己转换的 DXF。',
          ]} />
          <ManualList title="冻结前核对" items={[
            'Excel 数量必须为 1；DWG 接收数量应与本次实际要处理的图纸数量一致。',
            '逐项查看“服务器处理”：所有需要生产的 DWG 都应完成转换并出现对应 DXF，失败项必须先处理。',
            '多工作表、忽略非 DWG、超过 1000 个文件等提醒必须读完，不能只看“上传成功”。',
            '数量和文件身份都正确后才点击冻结。冻结表示后续阶段只认这一份服务器清单。',
          ]} />
          <ManualList title="遇到问题" items={[
            '旧版二进制 .xls 可以直接上传；若提示内容无法识别，应回到 Tekla 重新导出原表，不能仅改扩展名。',
            '明确指出某张图纸缺失或损坏时，先补齐源文件，不要连续点击转换或冻结。',
            '网络中断后先刷新状态；已有上传进度和服务器登记仍在时，不要整批重复上传。',
          ]} />
        </div>
      </div>
    ),
  },
  {
    key: 'classification',
    label: '3. 图纸分类与数量核对',
    children: (
      <div className="dashboard-manual-panel">
        <Alert
          type="info"
          showIcon
          message="原始 DWG 只负责留档；输入冻结以后，分类、拆板和 Excel 交接都使用服务器生成并登记的 DXF。"
        />
        <div className="dashboard-manual-grid">
          <ManualList title="怎么做" items={[
            '进入“图纸分类”阶段后启动分类，等待任务完成；处理中可以离开页面，返回后刷新即可恢复真实状态。',
            '按分类文件夹查看 BH、BOX、PL 和其他类型；“待确认”“无法读取”不是自动通过结果。',
            '需要线下判断的图纸可以按类别下载，但不要把人工改过的文件偷偷替换服务器分类结果。',
          ]} />
          <ManualList title="数量怎么对" items={[
            '分类输入总数应等于各分类结果、待确认项和无法读取项的合计，不能只看 BH、BOX 数量。',
            '用本阶段“下载全部 DXF”复核本批分类后的完整原图集合；它不应混入拆板成品或报告文件。',
            '发现数量少于冻结的 DWG/DXF 数量时，先查待确认和无法读取，不要直接进入拆板。',
          ]} />
          <ManualList title="何时继续" items={[
            '确认自动分类的类型与图纸标题栏一致，并明确知道哪些图纸将转人工处理后，再完成本阶段。',
            '分类结果缺文件时返回资料入库或重新形成分类结果；任务列表不能替代生产项目中的阶段操作。',
          ]} />
        </div>
      </div>
    ),
  },
  {
    key: 'split',
    label: '4. BH、BOX 整批拆板',
    children: (
      <div className="dashboard-manual-panel">
        <Alert
          type="warning"
          showIcon
          message="自动拆板只接收分类账本中明确可处理的 BH、BOX；其他类型保留原图并列出原因。"
        />
        <div className="dashboard-manual-grid">
          <ManualList title="系统怎样处理" items={[
            '点击整批拆板后，服务器一次读取当前分类清单；同批图纸只建立一个正式处理尝试，不因一张图失败而反复重跑整批。',
            '每张自动通过的图纸必须同时形成原长版和余量增长版，并经过独立重开校验；少任何一项都不算正式结果。',
            '一张图无法证明可拆时，只把该图列为未形成正式结果，其余合格图纸继续处理。',
          ]} />
          <ManualList title="结果怎么对" items={[
            '核对“输入图纸、正式配对、未形成正式结果”三类数量；正式配对数加未形成结果数应覆盖本次拆板输入。',
            '正式结果下载包只包含“原长”和“余量增长后短文件”两个文件夹；两个文件夹中的图纸应一一成对。',
            '另行下载本批未自动处理的图纸，用于线下处理；它们不能混入正式拆板结果或 Excel 自动交接。',
          ]} />
          <ManualList title="不要盲目重试" items={[
            '提示分类图纸缺失时，返回图纸分类阶段核对并重新确认，不能在任务列表直接重试拆板。',
            '提示某张图纸结构不满足自动拆板条件时，下载该图纸线下处理；不要靠重复提交碰运气。',
            '只要至少有一组完整正式结果，本阶段可以带着明确的未处理清单进入 Excel；一组都没有时不得继续。',
          ]} />
        </div>
      </div>
    ),
  },
  {
    key: 'excel',
    label: '5. Excel 整理与重量核验',
    children: (
      <div className="dashboard-manual-panel">
        <Alert
          type="success"
          showIcon
          message="Excel 阶段只读取冻结原表和本次正式拆板交接，页面不允许临时换文件。"
        />
        <div className="dashboard-manual-grid dashboard-manual-grid--excel">
          <ManualList title="计算与查询规则" items={[
            '板材统一按 7.85 计算；扁钢只查五金手册数据库。螺栓、螺套和 TT 不查手册，比重和理论重量留空。',
            'D 系列必须同时看材质：HPB、Q235B、Q355B 查圆钢，HRB 查螺纹钢；查询时用直径数字，表中仍保留 D8 这类原规格。',
            'PIP、PD 不查手册，按圆管公式计算比重；外径、壁厚不合法时留空并报告。',
            '其他已确认型材只查其对应手册类别；查不到写红色“查无”，同一键存在不同重量写红色“冲突”，都需要人工确认。',
          ]} />
          <ManualList title="重量怎样核验" items={[
            '普通零件先比较“表单重”和“理单重”的物理口径，再核对数量、总数、表总重和理总重是否按同一数量链放大。',
            'BH、BOX、BT 拆板后的腹板与翼板重量要合并后再与原型材单重比较，不能只拿承载源重量的腹板行单独比较。',
            'BH 按 1 块腹板加 2 块翼板，BOX 按 2 块腹板加 2 块翼板，BT 按 1 块腹板加 1 块翼板核对父件理论重量。',
            '净重大于毛重、源单重乘数量对不上总重、拆板子板合计不守恒，属于必须停下来核对的物理矛盾。',
          ]} />
          <ManualList title="成表后检查" items={[
            '最终下载是一个 Excel 文件；整理表和 part 表中的计算数据保留公式痕迹，没有依据的单元格保持空白，不补零。',
            '“类型”只允许 BH腹、BH翼、BOX腹、BOX翼、BT腹、BT翼，其他零件留空。相同型号但参数不同的 part 不能合并。',
            '处理报告只列核心问题和明确需要人工操作的事项；没有问题时表内写“无”。',
            '红色“查无”“冲突”、输入缺字段和重量硬矛盾必须逐项处理；普通几何偏差要结合图纸与原表判断。',
          ]} />
        </div>
      </div>
    ),
  },
  {
    key: 'delivery',
    label: '6. 下载交付与异常处理',
    children: (
      <div className="dashboard-manual-panel">
        <Alert
          type="info"
          showIcon
          message="每完成一个阶段只会解锁下一阶段，不会自动跳转；先核验本阶段，再主动点击阶段轨道继续。"
        />
        <div className="dashboard-manual-grid">
          <ManualList title="下载交付" items={[
            '拆板正式结果按成对文件夹下载；本批原图、未自动处理图纸和正式成品分开保存，不能混放后再回传。',
            'Excel 整理阶段只下载单个最终 Excel，不把内部处理副本、校验报告或后端记录当作交付文件。',
            '大批量下载看字节进度；浏览器显示完成并保存到本地后，再打开文件抽查数量和内容。',
          ]} />
          <ManualList title="错误处理顺序" items={[
            '先读页面给出的中文原因和处理建议，再刷新当前项目状态，确认服务器是否已经受理。',
            '文件明确缺失、格式不符或图纸不满足条件时，先修源数据；不要连续重复点击。',
            '服务器暂时不可用时保留项目编号、任务编号和请求编号交给管理员；页面不会显示后端日志。',
          ]} />
          <ManualList title="当前能力边界" items={[
            '资料入库、图纸分类、BH/BOX 拆板和 Excel 第一阶段是当前可操作生产能力。',
            'Excel 第二阶段、CAM 工作包、Windows CAM、结果接纳和交付归档显示“等待上线”时，不提供模拟执行或虚假完成按钮。',
            '数据管理台用于查看任务和已登记文件；正常生产操作始终回到所属生产项目完成。',
          ]} />
        </div>
      </div>
    ),
  },
];

export function DashboardPage() {
  const user = useAuthStore((state) => state.user);
  const navigate = useNavigate();
  const workflowsQ = useQuery({
    queryKey: ['workflows', 'production-dashboard'],
    queryFn: () => listWorkflows({
      page: 1,
      page_size: 6,
      workflow_type: 'linux_production',
    }),
    refetchInterval: (query) => (
      query.state.data?.data.some((workflow) => workflow.status === 'running')
        ? 4000
        : false
    ),
  });
  const templatesQ = useQuery({
    queryKey: ['workflow-templates'],
    queryFn: listWorkflowTemplates,
    staleTime: 60_000,
  });

  const refresh = useCallback(() => {
    void workflowsQ.refetch();
    void templatesQ.refetch();
  }, [templatesQ, workflowsQ]);
  const stageNames = useMemo(() => {
    const production = templatesQ.data?.find(
      (template) => template.code === 'linux_production',
    );
    return new Map(production?.stages.map((stage) => [stage.code, stage.name]) ?? []);
  }, [templatesQ.data]);
  const summary = workflowsQ.data?.summary;
  const workflows = workflowsQ.data?.data ?? [];

  const columns = [
    {
      title: '生产项目',
      key: 'project',
      minWidth: 250,
      render: (_: unknown, workflow: WorkflowRun) => (
        <div className="dashboard-production-project">
          <Typography.Text strong>
            {workflow.project_code ?? `项目 ${workflow.project_id}`}
          </Typography.Text>
          <Typography.Text>{workflow.project_name ?? workflow.name}</Typography.Text>
        </div>
      ),
    },
    {
      title: '流程状态',
      dataIndex: 'status',
      width: 120,
      render: (status: string) => (
        <StatusChip style={statusOf(WORKFLOW_STATUS, status)} />
      ),
    },
    {
      title: '当前步骤',
      dataIndex: 'current_stage',
      width: 180,
      render: (stage?: string | null) => (
        stage
          ? stageNames.get(stage) ?? FALLBACK_STAGE_NAMES[stage] ?? '等待后续处理'
          : '尚未开始'
      ),
    },
    {
      title: '完成进度',
      dataIndex: 'progress',
      width: 160,
      render: (progress: number) => <Progress percent={progress} size="small" />,
    },
    {
      title: '最近更新',
      dataIndex: 'updated_at',
      width: 170,
      render: (value: string) => fmtDateTime(value),
    },
    {
      title: '操作',
      key: 'action',
      width: 116,
      align: 'right' as const,
      render: (_: unknown, workflow: WorkflowRun) => (
        <Button
          type="link"
          onClick={() => navigate(`/workflows/${workflow.id}`)}
        >
          继续处理 <ArrowRightOutlined />
        </Button>
      ),
    },
  ];

  return (
    <Space orientation="vertical" size="large" style={{ width: '100%' }}>
      <section className="dashboard-hero dashboard-production-hero">
        <div className="dashboard-production-hero__copy">
          <Typography.Text className="dashboard-production-hero__eyebrow">
            生产流程总览
          </Typography.Text>
          <Typography.Title level={2}>
            你好，{user?.real_name || user?.username}，这里是生产工作台
          </Typography.Title>
          <Typography.Paragraph>
            从项目资料入库开始，按顺序完成图纸分类、整批拆板和 Excel 整理。每一步只使用上一
            步确认后的服务器数据，避免文件夹之间人工传错。
          </Typography.Paragraph>
        </div>
        <Space wrap className="dashboard-production-hero__actions">
          <Button ghost icon={<ReloadOutlined />} onClick={refresh} loading={workflowsQ.isFetching}>
            刷新项目
          </Button>
          <Link to="/workflows?new=1">
            <Button type="primary" size="large" icon={<PlusOutlined />}>
              新建生产项目
            </Button>
          </Link>
        </Space>
      </section>

      {workflowsQ.isError && (
        <Alert
          type="error"
          showIcon
          message="生产项目加载失败"
          description="当前没有执行任何生产操作。请确认服务器连接后重新刷新；已有项目数据不会因此改变。"
          action={<Button onClick={refresh}>重新加载</Button>}
        />
      )}

      <StatGrid>
        <StatCard
          label="生产项目"
          value={summary?.total ?? '—'}
          icon={<ApartmentOutlined />}
          color="#0f5d66"
          bg="#e9f8f7"
          hint="当前账号可查看的项目"
        />
        <StatCard
          label="正在处理"
          value={summary?.running ?? '—'}
          icon={<ReloadOutlined spin={(summary?.running ?? 0) > 0} />}
          color="#2563eb"
          bg="#eff6ff"
          hint="服务器正在执行的流程"
        />
        <StatCard
          label="等待操作"
          value={summary?.waiting ?? '—'}
          icon={<ClockCircleOutlined />}
          color="#b45309"
          bg="#fff8e8"
          hint="需要上传、确认或继续执行"
        />
        <StatCard
          label="已经完成"
          value={summary?.completed ?? '—'}
          icon={<CheckCircleOutlined />}
          color="#047857"
          bg="#ecfdf5"
          hint="流程已完成并可交付"
        />
      </StatGrid>

      <Card
        className="dashboard-production-list"
        title={<Space><ApartmentOutlined /><span>最近生产项目</span></Space>}
        extra={<Link to="/workflows">查看全部项目 <ArrowRightOutlined /></Link>}
        styles={{ body: { padding: workflows.length ? 0 : 24 } }}
      >
        {workflows.length ? (
          <Table<WorkflowRun>
            rowKey="id"
            columns={columns}
            dataSource={workflows}
            loading={workflowsQ.isLoading}
            pagination={false}
            scroll={{ x: 1000 }}
            onRow={(workflow) => ({
              onDoubleClick: () => navigate(`/workflows/${workflow.id}`),
            })}
          />
        ) : (
          <Empty description="还没有生产项目">
            <Link to="/workflows?new=1">
              <Button type="primary" icon={<PlusOutlined />}>创建第一个生产项目</Button>
            </Link>
          </Empty>
        )}
      </Card>

      <div className="dashboard-production-guide">
        <Card
          title={<Space><TagsOutlined /><span>先看这里：一批资料怎样走完</span></Space>}
          className="dashboard-production-guide__steps"
        >
          <Steps direction="vertical" size="small" current={-1} items={OPERATION_STEPS} />
        </Card>
        <Card title="开工前请核对" className="dashboard-production-guide__checklist">
          <ul>
            <li>每个生产项目只上传一份 Tekla 原始 Excel；多工作表文件只处理第一张并给出提醒。</li>
            <li>图纸以 DWG 文件夹入库，服务器转换成 DXF 后，后续阶段只使用服务器 DXF。</li>
            <li>单次文件夹最多接收 1000 个文件；超过时页面会明确提示本次实际接收范围。</li>
            <li>拆板只自动处理分类明确的 BH、BOX；其他类型保留原图并给出人工处理提示。</li>
            <li>阶段完成后只解锁下一步，不会自动跳转；请先核对数量和错误提示再继续。</li>
          </ul>
          <Link to="/workflows">
            <Button block icon={<ApartmentOutlined />}>进入生产流程</Button>
          </Link>
        </Card>
      </div>

      <Card
        className="dashboard-production-manual"
        title={<Space><SafetyCertificateOutlined /><span>生产操作手册</span></Space>}
        extra={<Typography.Text type="secondary">按阶段展开，做完一项核对一项</Typography.Text>}
      >
        <div className="dashboard-production-manual__legend">
          <span><InfoCircleOutlined /> 蓝色：当前步骤的做法与范围</span>
          <span><ExclamationCircleOutlined /> 黄色：继续前必须确认</span>
          <span><CheckCircleOutlined /> 绿色：结果核验规则</span>
        </div>
        <Collapse
          accordion
          defaultActiveKey={['prepare']}
          className="dashboard-production-manual__collapse"
          items={MANUAL_SECTIONS}
        />
      </Card>
    </Space>
  );
}
