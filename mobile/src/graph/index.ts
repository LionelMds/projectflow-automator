import type { Gateways } from '../core/gateways';
import { graph } from './client';
import { graphDrive } from './drive';
import { graphMail } from './mail';
import { graphPlanner } from './planner';
import { graphWorkbooks } from './workbook';

export const graphGateways: Gateways = {
  kind: 'graph',
  async me() {
    const u = await graph<{ id: string; displayName?: string; mail?: string; userPrincipalName?: string }>('GET', '/me?$select=id,displayName,mail,userPrincipalName');
    return { id: u.id, displayName: u.displayName ?? '', email: u.mail || u.userPrincipalName || '' };
  },
  drive: graphDrive,
  workbooks: graphWorkbooks,
  planner: graphPlanner,
  mail: graphMail,
};
