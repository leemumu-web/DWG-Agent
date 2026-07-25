import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Alert,
  App,
  Button,
  Card,
  Col,
  Empty,
  Form,
  Input,
  List,
  Modal,
  Popconfirm,
  Row,
  Space,
  Table,
  Tabs,
  Tag,
  Typography,
} from 'antd';
import {
  DatabaseOutlined,
  DeleteOutlined,
  EditOutlined,
  PlusOutlined,
  ReloadOutlined,
  SearchOutlined,
  TableOutlined,
} from '@ant-design/icons';

import {
  createMySqlRow,
  deleteMySqlRow,
  getMySqlTable,
  listMySqlRows,
  listMySqlTables,
  updateMySqlRow,
} from '../../api/dataAdmin';
import type { MySqlColumn, MySqlRow, MySqlValue } from '../../types/dataAdmin';

interface Props {
  canManage: boolean;
}

interface EditorState {
  mode: 'create' | 'edit';
  row?: MySqlRow;
}

function displayValue(value: MySqlValue | undefined) {
  if (value === null || value === undefined) return <Typography.Text type="secondary">NULL</Typography.Text>;
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
}

function parseValue(value: unknown, column: MySqlColumn): MySqlValue | undefined {
  if (value === undefined || value === '') return column.nullable ? null : undefined;
  const type = column.type.toUpperCase();
  if (type.includes('JSON')) return JSON.parse(String(value)) as MySqlValue;
  if (/(^|\\W)(INT|DECIMAL|NUMERIC|FLOAT|DOUBLE|REAL|BIGINT|SMALLINT)/.test(type)) {
    const number = Number(value);
    if (!Number.isFinite(number)) throw new Error(`${column.name} 必须是数字`);
    return number;
  }
  if (type.includes('BOOL')) return ['1', 'true', 'yes', 'on'].includes(String(value).toLowerCase());
  return String(value);
}

export function MySqlWorkspace({ canManage }: Props) {
  const { message } = App.useApp();
  const queryClient = useQueryClient();
  const [selected, setSelected] = useState('');
  const [search, setSearch] = useState('');
  const [page, setPage] = useState(1);
  const [editor, setEditor] = useState<EditorState>();
  const [form] = Form.useForm<Record<string, unknown>>();

  const tables = useQuery({
    queryKey: ['data-admin', 'mysql', 'tables'],
    queryFn: listMySqlTables,
  });
  useEffect(() => {
    if (!selected && tables.data?.length) setSelected(tables.data[0].name);
  }, [selected, tables.data]);
  const structure = useQuery({
    queryKey: ['data-admin', 'mysql', 'table', selected],
    queryFn: () => getMySqlTable(selected),
    enabled: Boolean(selected),
  });
  const rows = useQuery({
    queryKey: ['data-admin', 'mysql', 'rows', selected, page],
    queryFn: () => listMySqlRows(selected, { page, page_size: 50 }),
    enabled: Boolean(selected),
  });
  const filteredTables = useMemo(
    () => (tables.data ?? []).filter((table) => table.name.toLowerCase().includes(search.trim().toLowerCase())),
    [search, tables.data],
  );
  const refresh = async () => {
    await queryClient.invalidateQueries({ queryKey: ['data-admin', 'mysql'] });
  };
  const failureArea = tables.isError
    ? '数据表清单'
    : structure.isError
      ? '字段结构'
      : rows.isError
        ? '数据记录'
        : '';
  const createBlocked = (structure.data?.columns ?? []).some(
    (column) => (column.primary_key && !column.autoincrement)
      || (column.required && column.sensitive),
  );
  const save = useMutation({
    mutationFn: async (raw: Record<string, unknown>) => {
      const columns = structure.data?.columns ?? [];
      const values: MySqlRow = {};
      for (const column of columns) {
        if (column.primary_key || column.autoincrement || column.sensitive) continue;
        const parsed = parseValue(raw[column.name], column);
        if (parsed !== undefined) values[column.name] = parsed;
      }
      if (editor?.mode === 'edit' && editor.row) {
        const primaryKey = Object.fromEntries(
          (structure.data?.primary_key ?? []).map((name) => [name, editor.row?.[name] ?? null]),
        ) as MySqlRow;
        return updateMySqlRow(selected, primaryKey, values);
      }
      return createMySqlRow(selected, values);
    },
    onSuccess: async () => {
      setEditor(undefined);
      form.resetFields();
      await refresh();
      message.success('数据库记录已保存');
    },
    onError: (error) => message.error(error instanceof Error ? error.message : '保存失败，请检查字段和约束'),
  });
  const remove = useMutation({
    mutationFn: (row: MySqlRow) => {
      const primaryKey = Object.fromEntries(
        (structure.data?.primary_key ?? []).map((name) => [name, row[name] ?? null]),
      ) as MySqlRow;
      return deleteMySqlRow(selected, primaryKey);
    },
    onSuccess: async () => {
      await refresh();
      message.success('数据库记录已删除');
    },
    onError: () => message.error('删除失败，该记录可能仍被其它表引用'),
  });

  function openEditor(mode: 'create' | 'edit', row?: MySqlRow) {
    setEditor({ mode, row });
    const initial = Object.fromEntries(
      (structure.data?.columns ?? [])
        .filter((column) => !column.sensitive)
        .map((column) => [
          column.name,
          row?.[column.name] === null || row?.[column.name] === undefined
            ? undefined
            : typeof row[column.name] === 'object'
              ? JSON.stringify(row[column.name], null, 2)
              : String(row[column.name]),
        ]),
    );
    form.setFieldsValue(initial);
  }

  const dataColumns = (structure.data?.columns ?? []).map((column) => ({
    title: <Space size={4}>{column.name}{column.primary_key && <Tag color="gold">PK</Tag>}</Space>,
    dataIndex: column.name,
    key: column.name,
    width: 170,
    ellipsis: true,
    render: (value: MySqlValue) => displayValue(value),
  }));
  if (canManage && structure.data?.primary_key.length) {
    dataColumns.push({
      title: '操作' as never,
      dataIndex: '__actions',
      key: '__actions',
      width: 140,
      ellipsis: false,
      render: (_value: MySqlValue, row: MySqlRow) => (
        <Space size={2}>
          <Button type="link" size="small" icon={<EditOutlined />} onClick={() => openEditor('edit', row)}>编辑</Button>
          <Popconfirm title="确定删除这条记录？" description="数据库删除不可撤销。" onConfirm={() => remove.mutate(row)}>
            <Button type="link" danger size="small" icon={<DeleteOutlined />}>删除</Button>
          </Popconfirm>
        </Space>
      ),
    } as never);
  }

  return (
    <Row gutter={[16, 16]} className="mysql-browser">
      <Col xs={24} md={7} lg={6}>
        <Card className="console-table-card mysql-table-list" title="MySQL 表结构">
          <Input
            allowClear
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            prefix={<SearchOutlined />}
            placeholder="查找数据表"
          />
          <List
            loading={tables.isLoading}
            dataSource={filteredTables}
            locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="没有匹配的数据表" /> }}
            renderItem={(table) => (
              <List.Item
                className={selected === table.name ? 'mysql-table-item mysql-table-item--active' : 'mysql-table-item'}
                onClick={() => {
                  setSelected(table.name);
                  setPage(1);
                }}
              >
                <Space><TableOutlined /><span>{table.name}</span></Space>
                <Typography.Text type="secondary">{table.column_count}</Typography.Text>
              </List.Item>
            )}
          />
        </Card>
      </Col>
      <Col xs={24} md={17} lg={18}>
        <Card
          className="console-table-card"
          title={<Space><DatabaseOutlined />{selected || 'MySQL 数据库'}</Space>}
          extra={<Space>
            <Tag color={canManage ? 'processing' : 'default'}>{canManage ? '可增删改查' : '只读'}</Tag>
            {canManage && selected && (
              <Button
                type="primary"
                icon={<PlusOutlined />}
                disabled={createBlocked}
                title={createBlocked ? '该表依赖受保护的主键或敏感必填字段，请使用对应业务页面新增' : undefined}
                onClick={() => openEditor('create')}
              >
                新增记录
              </Button>
            )}
            <Button icon={<ReloadOutlined />} loading={rows.isFetching} onClick={refresh}>刷新</Button>
          </Space>}
        >
          {(tables.isError || structure.isError || rows.isError) && (
            <Alert
              showIcon
              type="error"
              title={`MySQL ${failureArea}加载失败`}
              description="数据库连接可能短暂中断，请重试；持续失败时再检查服务状态和账号权限。"
              action={<Button size="small" onClick={refresh}>重新加载全部数据</Button>}
              style={{ marginBottom: 16 }}
            />
          )}
          {!selected ? (
            <Empty description="请选择数据表" />
          ) : (
            <Tabs
              items={[
                {
                  key: 'rows',
                  label: `数据记录 ${structure.data ? `(${structure.data.row_count})` : ''}`,
                  children: (
                    <Table<MySqlRow>
                      rowKey={(row) => JSON.stringify((structure.data?.primary_key ?? []).map((key) => row[key]))}
                      size="small"
                      loading={rows.isLoading}
                      dataSource={rows.data?.data ?? []}
                      columns={dataColumns}
                      scroll={{ x: Math.max(900, dataColumns.length * 170) }}
                      pagination={{
                        current: page,
                        pageSize: 50,
                        total: rows.data?.pagination.total ?? 0,
                        showSizeChanger: false,
                        onChange: setPage,
                      }}
                    />
                  ),
                },
                {
                  key: 'columns',
                  label: `字段结构 (${structure.data?.columns.length ?? 0})`,
                  children: (
                    <Table<MySqlColumn>
                      rowKey="name"
                      size="small"
                      pagination={false}
                      dataSource={structure.data?.columns ?? []}
                      columns={[
                        { title: '字段', dataIndex: 'name', render: (name, column) => <Space>{name}{column.primary_key && <Tag color="gold">主键</Tag>}{column.sensitive && <Tag color="red">已隐藏</Tag>}</Space> },
                        { title: '类型', dataIndex: 'type' },
                        { title: '可空', dataIndex: 'nullable', width: 90, render: (value) => value ? '是' : '否' },
                        { title: '默认值', dataIndex: 'default', render: (value) => value ?? '—' },
                      ]}
                    />
                  ),
                },
              ]}
            />
          )}
        </Card>
      </Col>
      <Modal
        open={Boolean(editor)}
        title={editor?.mode === 'edit' ? `编辑 ${selected} 记录` : `新增 ${selected} 记录`}
        okText="保存"
        cancelText="取消"
        confirmLoading={save.isPending}
        onCancel={() => {
          setEditor(undefined);
          form.resetFields();
        }}
        onOk={() => form.validateFields().then((values) => save.mutate(values))}
        width={720}
      >
        <Alert
          type="info"
          showIcon
          title="主键、自增字段和敏感字段由系统保护；空值按 NULL 处理。"
          style={{ marginBottom: 16 }}
        />
        <Form form={form} layout="vertical">
          <Row gutter={16}>
            {(structure.data?.columns ?? [])
              .filter((column) => !column.primary_key && !column.autoincrement && !column.sensitive)
              .map((column) => (
                <Col xs={24} md={column.type.toUpperCase().includes('TEXT') || column.type.toUpperCase().includes('JSON') ? 24 : 12} key={column.name}>
                  <Form.Item
                    name={column.name}
                    label={<Space>{column.name}<Typography.Text type="secondary">{column.type}</Typography.Text></Space>}
                    rules={column.required && !['created_at', 'updated_at'].includes(column.name) ? [{ required: true, message: `请输入 ${column.name}` }] : undefined}
                  >
                    {column.type.toUpperCase().includes('TEXT') || column.type.toUpperCase().includes('JSON')
                      ? <Input.TextArea autoSize={{ minRows: 2, maxRows: 6 }} />
                      : <Input placeholder={column.nullable ? '留空为 NULL' : undefined} />}
                  </Form.Item>
                </Col>
              ))}
          </Row>
        </Form>
      </Modal>
    </Row>
  );
}
