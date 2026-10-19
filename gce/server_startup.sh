#!/bin/bash
# Runs as root on every boot of a ZooKeeper server VM (GCE startup-script).
if [ ! -f /opt/zookeeper/bin/zkServer.sh ]; then
  apt-get update -y
  DEBIAN_FRONTEND=noninteractive apt-get install -y openjdk-17-jre-headless netcat-openbsd curl sysstat
  DEBIAN_FRONTEND=noninteractive apt-get install -y bpfcc-tools bpftrace || true
  DEBIAN_FRONTEND=noninteractive apt-get install -y "linux-headers-$(uname -r)" || true
  curl -fsSL -o /tmp/zk.tgz https://archive.apache.org/dist/zookeeper/zookeeper-3.9.3/apache-zookeeper-3.9.3-bin.tar.gz
  mkdir -p /opt/zookeeper /var/lib/zookeeper /var/log/zookeeper
  tar -xzf /tmp/zk.tgz -C /opt/zookeeper --strip-components=1
fi
for d in balanced ssd; do
  dev=/dev/disk/by-id/google-zk-log-$d; mnt=/zk-log-$d
  mkdir -p "$mnt"
  blkid "$dev" >/dev/null 2>&1 || mkfs.ext4 -F "$dev"
  mountpoint -q "$mnt" || mount -o discard,defaults "$dev" "$mnt"
done
touch /var/lib/zk_ready
