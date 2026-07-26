import { CheckCircleOutlined } from '@ant-design/icons';

import { isWaitingLaunchStage, WORKFLOW_STATUS } from './model/workflowPresentation';
import type { WorkflowStage, WorkflowStageCapability } from './workflow';

export function stageStateLabel(stage: WorkflowStage): string {
  return WORKFLOW_STATUS[stage.status]?.label ?? stage.status;
}

export function WorkflowStageRail({
  stages,
  capabilities,
  currentCode,
  selectedCode,
  onSelect,
}: {
  stages: WorkflowStage[];
  capabilities: Map<string, WorkflowStageCapability>;
  currentCode?: string | null;
  selectedCode?: string | null;
  onSelect: (stageCode: string) => void;
}) {
  const currentStage = stages.find((stage) => stage.stage_code === currentCode);
  return (
    <nav className="workflow-stage-rail" aria-label="生产阶段">
      <div className="workflow-stage-rail__heading">
        <span>PRODUCTION ROUTE</span>
        <strong>{stages.length} 个阶段</strong>
      </div>
      <ol>
        {stages.map((stage) => {
          const capability = capabilities.get(stage.stage_code);
          const current = stage.stage_code === currentCode;
          const selected = stage.stage_code === selectedCode;
          const completed = ['succeeded', 'skipped'].includes(stage.status);
          const waitingLaunch = isWaitingLaunchStage(stage.stage_code);
          const locked = Boolean(
            currentStage
            && stage.sequence > currentStage.sequence
            && !completed,
          );
          return (
            <li
              key={stage.id}
              className={[
                current ? 'is-current' : '',
                selected ? 'is-selected' : '',
                completed ? 'is-complete' : '',
                stage.status === 'failed' ? 'is-failed' : '',
                waitingLaunch ? 'is-waiting-launch' : '',
                locked ? 'is-locked' : '',
              ].filter(Boolean).join(' ')}
            >
              <button
                type="button"
                aria-current={current ? 'step' : undefined}
                aria-pressed={selected}
                onClick={() => onSelect(stage.stage_code)}
              >
                <span className="workflow-stage-rail__index">
                  {completed
                    ? <CheckCircleOutlined />
                    : String(stage.sequence).padStart(2, '0')}
                </span>
                <span className="workflow-stage-rail__copy">
                  <strong>{stage.name}</strong>
                  <small>
                    {stageStateLabel(stage)}
                    {current ? ' · 当前阶段' : ''}
                  </small>
                  {stage.status === 'skipped'
                    && stage.output_json?.reason === 'no_split_candidates' && (
                    <small>本批无可拆板图纸，已正常进入下一步</small>
                  )}
                  {waitingLaunch ? (
                    <small>等待上线</small>
                  ) : capability && capability.implementation_status !== 'implemented' && (
                    <small>
                      {capability.implementation_status === 'external'
                        ? '外部节点'
                        : '接口预留'}
                    </small>
                  )}
                </span>
              </button>
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
