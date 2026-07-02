import { Button, Upload, message } from 'antd';
import type { UploadProps } from 'antd';
import { uploadDwg } from '../api/files.api';

export function FileUpload({ onUploaded }: { onUploaded?: () => void }) {
  const props: UploadProps = {
    accept: '.dwg',
    showUploadList: false,
    capture: undefined,
    customRequest: async ({ file, onSuccess, onError }) => {
      try {
        await uploadDwg(file as File);
        message.success('上传成功');
        onSuccess?.('ok');
        onUploaded?.();
      } catch (error) {
        message.error('上传失败');
        onError?.(error as Error);
      }
    },
  };
  return <Upload {...props}><Button>上传 DWG</Button></Upload>;
}
