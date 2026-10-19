#!/usr/bin/env bash
# start_infra.sh - starts the stopped VMs again. (Internal IPs stay the same.)
REGION="${REGION:-europe-west1}"
for spec in zkclient:b zk1:b zk2:b zk3:b zk4:b zk5:b zkm1:b zkm2:c zkm3:d zkm4:b zkm5:c; do
  gcloud compute instances ${ACTION:-start} "${spec%%:*}" --zone="$REGION-${spec##*:}" --quiet &
done; wait
