#!/usr/bin/env bash
# delete_infra.sh - DELETES every VM of the project whose name starts with zk, with its disks (the log disks are
# created with auto-delete). Stops all spending on them. Cannot be undone: copy the results back first.
mapfile -t VMS < <(gcloud compute instances list --filter='name~^zk' --format='value(name,zone.basename())')
[ ${#VMS[@]} -gt 0 ] || { echo "no zk* VMs in project $(gcloud config get-value project 2>/dev/null)"; exit 0; }
echo "deleting: $(printf '%s ' "${VMS[@]%%[[:space:]]*}")"
for vm in "${VMS[@]}"; do
  set -- $vm
  gcloud compute instances delete "$1" --zone="$2" --quiet &
done; wait
echo "left (VMs, then disks):"
gcloud compute instances list --filter='name~^zk' --format='value(name)'
gcloud compute disks list --filter='name~^zk' --format='value(name)'
