# Running a 3-node ZooKeeper ensemble with Docker

This guide starts a 3-server ZooKeeper ensemble on one machine using plain `docker run`,
then checks that it elects a leader, replicates writes and survives a server failure.

Docker Compose is deliberately **not** used: the project brief forbids it, because it does not
carry over to the multi-machine setup of Stage II.

```
                     your machine
 ┌───────────────────────────────────────────────────────┐
 │  Docker network "zknet"                                │
 │  ──────┬──────────────┬──────────────┬──────────       │
 │     ┌──┴──┐        ┌──┴──┐        ┌──┴──┐             │
 │     │ zk1 │        │ zk2 │        │ zk3 │             │
 │     └──┬──┘        └──┬──┘        └──┬──┘             │
 │        │ :2181        │ :2181        │ :2181          │
 └────────┼──────────────┼──────────────┼────────────────┘
      localhost:2181  localhost:2182  localhost:2183
```

---

## 1. Prerequisites

- Docker installed and running:
  ```
  docker --version
  docker run --rm hello-world
  ```
- If you get `permission denied`, add yourself to the `docker` group, then log out and back in:
  ```
  sudo usermod -aG docker $USER
  ```
- Optional, for the monitoring section: `nc` (package `netcat-openbsd`) and `curl`.

---

## 2. Get the image

```
docker pull zookeeper:3.9
docker run --rm zookeeper:3.9 zkServer.sh version
```

- Always use a fixed tag such as `3.9`, not `latest`. Everyone in the group and every
  experiment run should use the same version.
- `--rm` deletes the container once the command finishes.

---

## 3. Create the network

```
docker network create zknet
```

The containers need a network **you create yourself**. On it, Docker resolves container names
(`zk1`, `zk2`, …) to IPs, so the servers can find each other by name. Docker's default
network does not do this, and the ensemble would never form.

(Docker Compose creates such a network automatically, e.g. `lab2_default` in PSIA lab 2.
Without Compose, you do it by hand.)

---

## 4. Define the server list

```
SERVERS="server.1=zk1:2888:3888;2181 server.2=zk2:2888:3888;2181 server.3=zk3:2888:3888;2181"
echo "$SERVERS"
```

Each entry describes one ensemble member:

```
server.1 = zk1 : 2888 : 3888 ; 2181
   │        │     │      │      └─ client port (clients connect here)
   │        │     │      └──────── leader election port
   │        │     └─────────────── quorum port (leader ↔ followers: PROPOSAL / ACK / COMMIT)
   │        └───────────────────── hostname = container name
   └────────────────────────────── server ID (must match ZOO_MY_ID)
```

> ⚠️ **Before `2181` there must be a semicolon `;`, not a colon `:`.**
> With a colon, the server exits immediately and its log shows `Unrecognised peertype: 2181`.

Notes:
- The double quotes are required: the list contains spaces and `;`.
- There must be no spaces around `=`.
- This is a bash variable, stored only in the current terminal. In a new terminal, set it again.

---

## 5. Start the servers

```
docker run -d --name zk1 --network zknet -p 2181:2181 -e ZOO_MY_ID=1 -e ZOO_SERVERS="$SERVERS" zookeeper:3.9
docker run -d --name zk2 --network zknet -p 2182:2181 -e ZOO_MY_ID=2 -e ZOO_SERVERS="$SERVERS" zookeeper:3.9
docker run -d --name zk3 --network zknet -p 2183:2181 -e ZOO_MY_ID=3 -e ZOO_SERVERS="$SERVERS" zookeeper:3.9
```

| Flag | Meaning |
|---|---|
| `-d` | Run in the background |
| `--name zk1` | Container name. Also its hostname on `zknet`, so it must match the server list |
| `--network zknet` | Attach to the network from step 3 |
| `-p 2181:2181` | `laptop port:container port`, so clients on your machine can connect. Each server needs a different laptop port |
| `-e ZOO_MY_ID=1` | This server's ID. Must be unique and match its `server.N` entry |
| `-e ZOO_SERVERS="$SERVERS"` | Full member list. The same on every server |
| `zookeeper:3.9` | Image and version |

Check that all three are running:
```
docker ps
```
All three should show `Up`. If one shows `Exited`, see [Troubleshooting](#9-troubleshooting).

---

## 6. Verify the ensemble

### 6.1 Roles: one leader, two followers
```
for i in 1 2 3; do echo -n "zk$i: "; docker exec zk$i zkServer.sh status 2>&1 | grep Mode; done
```
Expected output (which server is leader may differ):
```
zk1: Mode: follower
zk2: Mode: leader
zk3: Mode: follower
```
If you see `Error contacting service`, wait a few seconds: the election may still be in progress.

### 6.2 Replication: write on one server, read on another
```
docker exec zk1 zkCli.sh -server zk1:2181 create /hello world
docker exec zk1 zkCli.sh -server zk3:2181 get /hello
```
Near the end of the second command's output you should see `world`.

The write went to zk1, a follower, which forwarded it to the leader. The leader proposed it to the
followers, and they logged it and sent ACKs. With a majority of ACKs, the leader sent COMMIT, and
every server applied the write. That is why zk3 returns it.

### 6.3 Fault tolerance: kill the leader

Replace `zk2` below with your current leader.

1. **Stop the leader:**
   ```
   docker stop zk2
   ```
2. **Check a new leader was elected:**
   ```
   for i in 1 2 3; do echo -n "zk$i: "; docker exec zk$i zkServer.sh status 2>&1 | grep -E "Mode|Error"; done
   ```
   The stopped server returns an error, which is expected. One of the other two is now `leader`.
3. **See how long the election took:**
   ```
   docker logs zk1 2>&1 | grep "LEADER ELECTION TOOK"
   docker logs zk3 2>&1 | grep "LEADER ELECTION TOOK"
   ```
   The last line shows the most recent election time.
4. **Check the data survived and writes still work.** 2 of 3 servers is still a majority:
   ```
   docker exec zk1 zkCli.sh -server zk1:2181 get /hello
   docker exec zk1 zkCli.sh -server zk1:2181 set /hello again
   ```
5. **Bring the old leader back.** It rejoins as a **follower** and catches up:
   ```
   docker start zk2
   docker exec zk2 zkServer.sh status
   docker exec zk1 zkCli.sh -server zk2:2181 get /hello      # prints: again
   ```

### 6.4 Losing the majority (optional)
Stop two servers. With 1 of 3 left there is no majority, so the last server **stops serving**:
ZooKeeper chooses consistency over availability.
```
docker stop zk1 zk2
docker exec zk3 zkServer.sh status        # error: not serving
docker start zk1 zk2                      # majority is back; the ensemble recovers
```

---

## 7. Monitoring and debugging

### Logs
```
docker logs zk1                 # full log
docker logs -f zk1              # follow it live (Ctrl+C to stop following)
docker logs zk1 2>&1 | grep -E "LEADING|FOLLOWING|LOOKING|LEADER ELECTION TOOK"
```
In a Java error, the useful part is the **exception message** above the `at ...` lines.

### Four-letter-word commands
Short text commands sent to a server's client port. Only `srvr` is enabled by default. To enable
more, add this to `docker run`:
```
-e ZOO_4LW_COMMANDS_WHITELIST="srvr,stat,mntr,conf,cons,ruok,wchs"
```
From your machine, through the published ports:
```
echo srvr | nc localhost 2181      # overview: latency, zxid, mode, node count
echo mntr | nc localhost 2181      # all metrics as key/value pairs
```
Shortcut to show who is leader and whether all servers are at the same zxid:
```
for p in 2181 2182 2183; do echo "port $p: $(echo srvr | nc localhost $p | grep -E 'Mode|Zxid' | tr '\n' ' ')"; done
```

### AdminServer (HTTP, JSON)
Each server exposes HTTP commands on port 8080 inside the container. Publish it on a free laptop
port, e.g. `-p 8081:8080` for zk1, `8082` for zk2, and so on:
```
curl -s localhost:8081/commands            # list of commands
curl -s localhost:8081/commands/leader     # is this server the leader?
curl -s localhost:8081/commands/monitor    # same data as mntr
curl -s localhost:8081/commands/zabstate   # Zab phase: ELECTION / DISCOVERY / SYNCHRONIZATION / BROADCAST
```

### Client commands
```
docker exec -it zk1 zkCli.sh -server zk1:2181
  ls /             # list znodes
  get /hello       # read
  set /hello x     # write
  stat /hello      # metadata: mzxid (zxid of last change), version, ...
  config           # ensemble membership
  quit
```

---

## 8. Clean up
```
docker rm -f zk1 zk2 zk3
docker network rm zknet
```
`-f` stops the containers before deleting them. Remove the network last: that only works once no
containers are attached to it.

---

## 9. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `Conflict. The container name "/zk1" is already in use` | A container with that name still exists, possibly a stopped one. `docker ps` hides stopped containers; `docker ps -a` shows them | Read its logs first (`docker logs zk1`), then `docker rm zk1` |
| Container shows `Exited (2)` | ZooKeeper rejected its config | `docker logs zk1` and look for the `ConfigException` message |
| `Unrecognised peertype: 2181` | `:2181` instead of `;2181` in the server list | Fix `SERVERS`, then `docker rm -f` and re-run **all** servers. Containers keep the value they were created with |
| All servers stay `LOOKING` / `Error contacting service` | They can't reach each other: missing `--network zknet`, or names in the list don't match container names | Check `docker network inspect zknet` and `docker exec zk1 cat /conf/zoo.cfg` |
| `Bind for 0.0.0.0:2181 failed: port is already allocated` | Another process or container uses that laptop port | Use a different laptop port, e.g. `-p 2191:2181` |
| `container ... is not running` from `docker exec` | You ran `exec` against a stopped container | `docker start <name>` |
| `permission denied` talking to Docker | Your user isn't in the `docker` group | See [Prerequisites](#1-prerequisites) |

To see the config a server is actually using:
```
docker exec zk1 cat /conf/zoo.cfg
docker exec zk1 cat /data/myid
```

---

## 10. Notes on `docker exec`

`docker exec <container> <program> [args]` runs one extra program inside a **running** container.
It runs the program **directly, without a shell**, so there is no need to prefix it with `bash`:
```
docker exec zk1 zkServer.sh status
```

Use a shell only when you need one:
- **Interactive session inside the container:**
  ```
  docker exec -it zk1 bash
  ```
  Then type commands; `exit` leaves the shell and the container keeps running.
- **Pipes, redirects, `&&`, `cd`, `*` or `$VAR` evaluated inside the container:** wrap the command
  in `bash -c`, with **single quotes**:
  ```
  docker exec zk1 bash -c 'ls /data/version-2/*'
  docker exec zk1 bash -c 'echo $HOSTNAME'
  ```
  Without the quotes, your own terminal handles those symbols before Docker runs anything.

`-it` (interactive + terminal) is needed for interactive programs such as `bash` or the `zkCli.sh`
shell. Leave it out in scripts.

---

## Quick reference

| Command | Purpose |
|---|---|
| `docker pull zookeeper:3.9` | Download the image |
| `docker network create zknet` | Create the network |
| `docker run -d --name zkN ...` | Start server N |
| `docker ps` / `docker ps -a` | Running containers / all containers |
| `docker exec zkN zkServer.sh status` | Role of server N |
| `docker exec -it zk1 zkCli.sh -server zkN:2181` | Client shell connected to server N |
| `docker logs zkN` | Server N's log |
| `docker stop zkN` / `docker start zkN` | Simulate a crash / recover |
| `docker rm -f zk1 zk2 zk3` | Delete the servers |
| `docker network rm zknet` | Delete the network |
