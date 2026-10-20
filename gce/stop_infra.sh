#!/usr/bin/env bash
# stop_infra.sh - STOPS the project's VMs whose name starts with zk (zkclient, zk1..zk5, zkm*) without deleting
# them: no CPU charges, the disks are kept (and still billed). start_infra.sh resumes them; internal IPs stay the same.
mapfile -t VMS < <(gcloud compute instances list --filter='name~^zk' --format='value(name,zone.basename())')
[ ${#VMS[@]} -gt 0 ] || { echo "no zk* VMs in project $(gcloud config get-value project 2>/dev/null)"; exit 0; }
for vm in "${VMS[@]}"; do
  set -- $vm
  gcloud compute instances ${ACTION:-stop} "$1" --zone="$2" --quiet &
done; wait
gcloud compute instances list --filter='name~^zk' --format='table(name,zone.basename(),status)'
