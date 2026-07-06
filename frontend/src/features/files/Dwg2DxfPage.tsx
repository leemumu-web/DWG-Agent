import { ConversionPage, type ConversionPageProps } from '../../components/ConversionPage';
import { createDxfJob } from '../../api/jobs.api';
import type { Job } from '../../types/job';

const props: ConversionPageProps = {
  fileExt: '.dwg',
  resultExt: '.dxf',
  taskType: 'convert_dwg_to_dxf',
  resultType: 'convert_dwg_to_dxf',
  createJobFn: (fileId: number): Promise<Job> => createDxfJob(fileId),
  title: 'DWG 图纸',
  tagPending: 'DWG',
  tagDone: 'DXF',
  downloadResultLabel: '下载 DXF',
  uploadHint: '自动转换为 DXF 格式',
  acceptExt: '.dwg',
  emptyText: '暂无 DWG 文件',
};

export function Dwg2DxfPage() {
  return <ConversionPage {...props} />;
}
