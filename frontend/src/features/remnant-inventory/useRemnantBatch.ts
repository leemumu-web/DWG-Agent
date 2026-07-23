import { useQuery } from '@tanstack/react-query';
import { getRemnantImportBatch } from './api';

const terminal = new Set(['awaiting_confirmation', 'confirmed', 'cancelled']);

export function useRemnantBatch(batchId?: number) {
  return useQuery({
    queryKey: ['remnant-import-batch', batchId],
    queryFn: () => getRemnantImportBatch(batchId!),
    enabled: Boolean(batchId),
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status && terminal.has(status) ? false : 2_000;
    },
    refetchIntervalInBackground: false,
  });
}

