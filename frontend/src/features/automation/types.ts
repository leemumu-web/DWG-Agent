export interface AgentRun {
  id: number;
  session_id: string;
  task: string;
  status: string;
  answer?: string | null;
}
