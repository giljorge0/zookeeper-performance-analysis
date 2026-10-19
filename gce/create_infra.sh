#!/usr/bin/env bash
# create_infra.sh - run on YOUR machine (gcloud installed and logged in).
# Creates: zkclient (load generator), zk1..zk5 in one zone (group z1),
#          zkm1..zkm5 spread over three zones (group z3). Each server gets
#          two extra log disks (pd-balanced, pd-ssd). Writes gce/cluster.env.
# WITH_Z3=0 skips the three-zone group (needed only for the zone-placement factor): 6 VMs instead of 11.
# Override sizes if your CPU quota is small:  SERVER_TYPE=e2-standard-2 CLIENT_TYPE=e2-standard-4 ./create_infra.sh
set -uo pipefail
cd "$(dirname "$0")"
command -v gcloud >/dev/null || { echo "gcloud is not installed"; exit 1; }
[ -n "$(gcloud config get-value project 2>/dev/null)" ] || { echo "no project set: run gcloud config set project YOUR_PROJECT_ID"; exit 1; }
REGION="${REGION:-europe-west1}"; ZA="$REGION-b"; ZB="$REGION-c"; ZC="$REGION-d"
SERVER_TYPE="${SERVER_TYPE:-n2-standard-2}"; CLIENT_TYPE="${CLIENT_TYPE:-n2-standard-8}"
IMG=(--image-family=debian-12 --image-project=debian-cloud)

gcloud services enable compute.googleapis.com >/dev/null
gcloud compute firewall-rules create zk-internal --network=default --allow=tcp,udp,icmp \
   --source-ranges=10.128.0.0/9 >/dev/null 2>&1 || true

mk_server() {  # name zone
  gcloud compute instances create "$1" --zone="$2" --machine-type="$SERVER_TYPE" "${IMG[@]}" \
    --boot-disk-size=20GB --boot-disk-type=pd-balanced \
    --create-disk="name=$1-logbal,size=20GB,type=pd-balanced,device-name=zk-log-balanced,auto-delete=yes" \
    --create-disk="name=$1-logssd,size=20GB,type=pd-ssd,device-name=zk-log-ssd,auto-delete=yes" \
    --metadata-from-file=startup-script=server_startup.sh >/dev/null
}
echo "creating VMs (a few minutes)..."
gcloud compute instances create zkclient --zone="$ZA" --machine-type="$CLIENT_TYPE" "${IMG[@]}" \
  --boot-disk-size=50GB --boot-disk-type=pd-balanced --metadata-from-file=startup-script=client_startup.sh >/dev/null &
for i in 1 2 3 4 5; do mk_server "zk$i" "$ZA" & done
Z3=("$ZA" "$ZB" "$ZC" "$ZA" "$ZB")
if [ "${WITH_Z3:-1}" = "1" ]; then
  for i in 1 2 3 4 5; do mk_server "zkm$i" "${Z3[$((i-1))]}" & done
fi
wait

zone_of() { case "$1" in zkm1|zkm4) echo "$ZA";; zkm2|zkm5) echo "$ZB";; zkm3) echo "$ZC";; *) echo "$ZA";; esac; }
echo "waiting for the install scripts to finish (first ssh also creates your ssh key)..."
VMS="zkclient zk1 zk2 zk3 zk4 zk5"; [ "${WITH_Z3:-1}" = "1" ] && VMS="$VMS zkm1 zkm2 zkm3 zkm4 zkm5"
for vm in $VMS; do
  marker=/var/lib/zk_ready; [ "$vm" = zkclient ] && marker=/var/lib/client_ready
  for try in $(seq 1 60); do
    gcloud compute ssh "$vm" --zone="$(zone_of "$vm")" --quiet --command "test -f $marker" >/dev/null 2>&1 && break
    sleep 10
  done
  echo "  $vm ready (or timed out: check with gcloud compute ssh $vm)"
done

USERNAME=$(gcloud compute ssh zkclient --zone="$ZA" --quiet --command whoami 2>/dev/null | tr -d '\r')
ip_of() { gcloud compute instances describe "$1" --zone="$(zone_of "$1")" --format='get(networkInterfaces[0].networkIP)'; }
Z1=""; for i in 1 2 3 4 5; do Z1+="$(ip_of zk$i) "; done
Z3S=""; [ "${WITH_Z3:-1}" = "1" ] && for i in 1 2 3 4 5; do Z3S+="$(ip_of zkm$i) "; done
cat > cluster.env <<ENV
SSH_USER="$USERNAME"
Z1_IPS="${Z1% }"
Z3_IPS="${Z3S% }"
ENV
echo "wrote gce/cluster.env:"; cat cluster.env
