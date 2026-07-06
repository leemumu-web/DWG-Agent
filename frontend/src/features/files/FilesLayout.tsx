import { Outlet, useLocation, useNavigate } from 'react-router-dom';
import { Tabs } from 'antd';
import { SwapOutlined, UndoOutlined } from '@ant-design/icons';

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
];

export function FilesLayout() {
  const navigate = useNavigate();
  const location = useLocation();

  const activeKey = tabs.some((t) => t.key === location.pathname)
    ? location.pathname
    : tabs[0].key;

  return (
    <>
      <Tabs
        activeKey={activeKey}
        onChange={(key) => navigate(key)}
        items={tabs}
        style={{ marginBottom: 12, marginTop: -8 }}
      />

      <Outlet />
    </>
  );
}
