import { Outlet, useLocation, useNavigate } from 'react-router-dom';
import { Tabs } from 'antd';
import { FileExcelOutlined, SwapOutlined, UndoOutlined, TableOutlined } from '@ant-design/icons';

const tabs = [
  {
    key: '/files/dwg2dxf',
    label: (
      <span>
        <SwapOutlined style={{ marginRight: 6, transform: 'rotate(90deg)' }} />
        DWG → DXF
      </span>
    ),
  },
  {
    key: '/files/dxf2dwg',
    label: (
      <span>
        <UndoOutlined style={{ marginRight: 6 }} />
        DXF → DWG
      </span>
    ),
  },
  {
    key: '/files/dxf2excel',
    label: (
      <span>
        <TableOutlined style={{ marginRight: 6 }} />
        DXF → Excel
      </span>
    ),
  },
  {
    key: '/files/excel-final',
    label: (
      <span>
        <FileExcelOutlined style={{ marginRight: 6 }} />
        Excel → 零件清单
      </span>
    ),
  },
];

export function FilesLayout() {
  const navigate = useNavigate();
  const location = useLocation();

  const activeKey = tabs.some((t) => t.key === location.pathname)
    ? location.pathname
    : tabs[0].key;

  return (
    <>
      <div className="pipeline-tabs">
        <Tabs
          activeKey={activeKey}
          onChange={(key) => navigate(key)}
          items={tabs}
        />
      </div>

      <Outlet />
    </>
  );
}
