#!/bin/bash
# Runs as root on every boot of the load-generator VM.
if [ ! -f /var/lib/client_ready ]; then
  apt-get update -y
  DEBIAN_FRONTEND=noninteractive apt-get install -y openjdk-17-jdk-headless maven git unzip \
      python3 python3-numpy python3-scipy python3-matplotlib netcat-openbsd sysstat tmux
  DEBIAN_FRONTEND=noninteractive apt-get install -y bpfcc-tools bpftrace || true
  touch /var/lib/client_ready
fi
