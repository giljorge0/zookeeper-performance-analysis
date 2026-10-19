#!/bin/bash
# Executed ON a server VM (piped over ssh by gce_backend.py):
#   sudo bash -s -- MYID LOGDISK(balanced|ssd) FORCESYNC(yes|no) IP1 IP2 ... IPN
set -e
MYID="$1"; LOGDISK="$2"; FORCESYNC="$3"; shift 3
ZK=/opt/zookeeper
LOGDIR="/zk-log-$LOGDISK/zklog"
"$ZK/bin/zkServer.sh" stop >/dev/null 2>&1 || true
pkill -f QuorumPeerMain >/dev/null 2>&1 || true
sleep 1
rm -rf /var/lib/zookeeper/* /zk-log-balanced/zklog /zk-log-ssd/zklog
mkdir -p /var/lib/zookeeper "$LOGDIR" /var/log/zookeeper
echo "$MYID" > /var/lib/zookeeper/myid
{
  echo "tickTime=2000"; echo "initLimit=10"; echo "syncLimit=5"
  echo "dataDir=/var/lib/zookeeper"; echo "dataLogDir=$LOGDIR"
  echo "clientPort=2181"; echo "maxClientCnxns=0"
  echo "4lw.commands.whitelist=srvr,stat,mntr,conf,cons,ruok,wchs"
  echo "admin.enableServer=false"
  i=1; for ip in "$@"; do echo "server.$i=$ip:2888:3888"; i=$((i+1)); done
} > "$ZK/conf/zoo.cfg"
[ "$#" -eq 1 ] && sed -i '/^server\./d;/^initLimit/d;/^syncLimit/d' "$ZK/conf/zoo.cfg"   # standalone
echo "export SERVER_JVMFLAGS=\"-Xms1g -Xmx1g -Dzookeeper.forceSync=$FORCESYNC\"" > "$ZK/conf/java.env"
export ZOO_LOG_DIR=/var/log/zookeeper
"$ZK/bin/zkServer.sh" start >/dev/null
