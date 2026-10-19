#!/usr/bin/env bash
# stop_infra.sh - STOPS the VMs without deleting them (no CPU charges; disks kept). Use start_infra.sh to resume.
REGION="${REGION:-europe-west1}"
for spec in zkclient:b zk1:b zk2:b zk3:b zk4:b zk5:b zkm1:b zkm2:c zkm3:d zkm4:b zkm5:c; do
  gcloud compute instances ${ACTION:-stop} "${spec%%:*}" --zone="$REGION-${spec##*:}" --quiet &
done; wait
