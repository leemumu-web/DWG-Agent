export const API_BASE = process.env.PLAYWRIGHT_API_BASE_URL
  ?? process.env.PLAYWRIGHT_FRONTEND_BASE_URL
  ?? 'http://127.0.0.1:8080';
export const ADMIN_USERNAME = process.env.PLAYWRIGHT_ADMIN_USERNAME ?? 'admin';
export const ADMIN_PASSWORD = process.env.PLAYWRIGHT_ADMIN_PASSWORD ?? 'SuperAdminPass1';

function enabled(name: string): boolean {
  return (process.env[name] ?? 'true').trim().toLowerCase() !== 'false';
}

export const DXF_PIPELINE_ENABLED = enabled('PLAYWRIGHT_DXF_PIPELINE_ENABLED');
export const DXF2DWG_PIPELINE_ENABLED = enabled('PLAYWRIGHT_DXF2DWG_PIPELINE_ENABLED');
export const EXCEL_FINAL_PIPELINE_ENABLED = enabled('PLAYWRIGHT_EXCEL_FINAL_PIPELINE_ENABLED');
