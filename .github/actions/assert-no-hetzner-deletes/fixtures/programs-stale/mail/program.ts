import * as hcloud from '@pulumi/hcloud';

export const server = new hcloud.Server('mail-server', {});
// The firewall moved elsewhere; only its name, hcloud.Firewall, is left here.
