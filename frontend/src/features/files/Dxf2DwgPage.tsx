import { ConversionPage, type ConversionPageProps } from '../../components/ConversionPage';

const props: ConversionPageProps = {
  fileExt: '.dxf',
  resultExt: '.dwg',
  taskType: 'convert_dxf_to_dwg',
  resultType: 'convert_dxf_to_dwg',
  title: 'DXF 图纸',
  tagPending: 'DXF',
  tagDone: 'DWG',
  downloadResultLabel: '下载 DWG',
  uploadHint: '自动转换为 DWG 格式',
  acceptExt: '.dxf',
  emptyText: '暂无 DXF 文件',
};

export function Dxf2DwgPage() {
  return <ConversionPage {...props} />;
}
