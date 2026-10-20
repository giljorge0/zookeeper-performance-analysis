#!/usr/bin/env bash
# start_infra.sh - starts the stopped zk* VMs again (internal IPs stay the same, so gce/cluster.env stays valid).
ACTION=start exec bash "$(dirname "$0")/stop_infra.sh"
