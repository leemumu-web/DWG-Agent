import { useQuery } from '@tanstack/react-query';
import { getRemnantImportBatch } from './api';

const terminal = new Set(['awaiting_confirmation', 'confirmed', 'failed', 'cancelled']);

export function useRemnantBatch(batchId?: number) {
  return useQuery({
    queryKey: ['remnant-import-batch', batchId],
    queryFn: () => getRemnantImportBatch(batchId!),
    enabled: Boolean(batchId),
    refetchInterval: (query) => {
      // 2s 是余料批次的轮询节奏（可感知进度且不压垮接口）；terminal 集合
      // 与后端状态枚举需同步维护。全仓轮询间隔（2s/3s/4s/10s）各页不一，
      // 调整时请说明与后端快照成本的权衡。
      const status = query.state.data?.status;
      return status && terminal.has(status) ? false : 2_000;
    },
    refetchIntervalInBackground: false,
  });
}

