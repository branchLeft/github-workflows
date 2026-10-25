import * as hcloud from '@pulumi/hcloud';

export const server = new hcloud.Server('mail-server', {});
export const firewall = new hcloud.Firewall('mail-firewall', {});
