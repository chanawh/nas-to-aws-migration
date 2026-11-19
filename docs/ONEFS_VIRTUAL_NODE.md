# OneFS virtual node in VMware Workstation Pro

This is the source side of the migration. The target side (LocalStack /
moto) is in `README.md`. This file is the source side only, start to
finish, so the whole run is a real PowerScale node talking to a real
(emulated) AWS, not a synthetic directory pretending to be a NAS.

```
Windows host
  ├─ VMware Workstation Pro
  │    └─ OneFS Simulator VM (bridged IP, e.g. 192.168.1.50)
  │         └─ SMB shares: finance, it-support, it-operations, operations
  ├─ Mount root: C:\nas2aws\onefs\<share>  (junctions to the SMB shares)
  ├─ Python sync engine (boto3) reads the mount root
  └─ LocalStack in Docker on :4566  (or moto on :5000)
       └─ S3 buckets nas2aws-migration-<share>
```

## 1. Download the OneFS Simulator

From Dell's trial downloads page, "PowerScale OneFS simulator", free
for non-production use. The current package is OneFS 9.12, 477 MB,
released August 2025, and it contains the simulator VM and the
installation guide. Download it on the Windows host that runs
VMware Workstation Pro.

Requirements, from Dell's download page for that package:
- RAM: minimum 2 GB per virtual node, 6 GB recommended per node.
  Give this single node 8 GB if the host can spare it; OneFS is
  happiest with headroom, and the migration is I/O, not CPU.
- Processor: VT-capable (Intel VT-x / AMD-V), enabled in BIOS.
  VMware will refuse to power the VM on without it.
- Disk: 100 GB per virtual node for a fully populated virtual
  cluster. For this lab, 100 GB thin-provisioned is fine; the
  dataset is 1.16 MB.
- Host OS: Microsoft Windows (recommended) or Ubuntu 20.04.
- Virtual infrastructure: VMware Workstation, Fusion, Player, or
  ESXi 6.7 / 7.0.3. Workstation Pro is the path in this guide.

License: the base simulator runs without a key for non-production
use. Advanced features need a free 90-day evaluation license from
your Dell account representative or systems engineer. This migration
does not need advanced features - SMB, local users, and SmartQuotas
are in the base - so do not chase a license key first.

## 2. Import it into VMware Workstation Pro

1. In Workstation Pro: File > Open, pick the simulator `.ovf` /
   `.ova` from the download package. Accept the defaults, point the
   storage at a disk with 100 GB free.
2. Before powering on, edit the VM settings:
   - Memory: 8 GB (6 GB minimum recommended, 2 GB absolute minimum).
   - Processors: 2 vCPU minimum, 4 if the host has them.
   - Network adapter: **Bridged**. This is the step that breaks
     most first attempts. NAT hides the node behind the host, and
     Host-only hides it from everything else. Bridged puts the node
     on the same LAN as the host, with its own IP, so the host, the
     sync engine, and LocalStack can all reach it. If the host is on
     Wi-Fi, pick the Wi-Fi adapter in the bridged settings, not
     Automatic, if the first attempt gets no IP.
   - Hard disk: leave the simulator's disks as imported. Do not
     add a second datastore; a single virtual node needs no extra.
3. Power on. The first boot takes several minutes. Do not interrupt
   it; OneFS is formatting its virtual disks.

## 3. First-boot configuration (the wizard)

On the VM console, OneFS runs its configuration wizard. The exact
prompts follow the installation guide in the download package; the
values that matter for this project are:

- Cluster name: `nas2aws-onefs` (any name, record it in
  `onefs.yaml`).
- Node IP: a static IP on the host LAN, e.g. `192.168.1.50`.
  Static, not DHCP. The mount scripts, the Terraform, and the
  migration all point at this IP, and a DHCP lease change mid-run
  breaks the sync. Write it down.
- Netmask / gateway / DNS: the LAN's own values.
- Admin password: set the `root` password. This is the credential
  the Platform API check and the SMB mounts use. Do not reuse a
  production password; this is a lab node on a bridged LAN.
- SmartConnect zone: leave the default for a single node. There is
  one node, so there is nothing to load-balance.

When the wizard finishes, the node is reachable at
`https://<ONEFS_IP>:8080` (WebUI) and over SSH as `root`.

Verify from the host, before anything else:

```powershell
Test-NetConnection 192.168.1.50 -Port 445
Test-NetConnection 192.168.1.50 -Port 8080
```

Both must succeed. If 445 fails, the node is not on the bridged
network, or the Windows firewall is blocking outbound SMB. If 8080
fails, the wizard has not finished, or the node is still booting.

## 4. Create the shares, permissions, and quotas on OneFS

SSH to the node as root and run the setup script in this repo:

```bash
ssh root@192.168.1.50 'sh -s' < scripts/onefs/setup_onefs.sh
```

What it does, per share (`finance`, `it-support`,
`it-operations`, `operations`):
- `mkdir -p /ifs/data/<share>`
- `isi smb shares create <share> --path=/ifs/data/<share> --create-path --browsable=true`
  (Dell CLI guide form; the share path must be under `/ifs`.)
- `isi smb shares permission modify <share> --wellknown Everyone`
  Lab only. Everyone gets full access, because the migration's ACL
  story lives in `acl.json` and in the S3 object tags, not in OneFS
  permissions for this lab.
- SmartQuota, hard and enforced: 2 GB on finance and it-support,
  1 GB on it-operations and operations, via
  `isi quota quotas create --path=/ifs/data/<share> --type=directory --hard-threshold=<size> --enforced=yes`

Verify on the node:

```bash
isi smb shares list
isi quota quotas list
```

Or do the same steps in the WebUI: Protocols > Windows Sharing
(SMB) > SMB Shares, and File System > SmartQuotas. The share name
and path are what the migration cares about, not which tool made
them. If a CLI command fails on your OneFS version, use the WebUI
and keep going.

## 5. Mount the shares on the host

Windows (the Workstation Pro host):

```powershell
.\scripts\mount_onefs.ps1 -OneFSHost 192.168.1.50 -MountRoot C:\nas2aws\onefs
```

It prompts for the root password, maps each share to a drive
letter (F:, G:, H:, I:), then creates a directory junction from
`C:\nas2aws\onefs\<share>` to that drive. The sync engine then sees
one tree, `C:\nas2aws\onefs\finance` and so on.

Linux host:

```bash
ONEFS_HOST=192.168.1.50 bash scripts/mount_onefs.sh /mnt/onefs
```

That needs `cifs-utils` (`sudo apt install cifs-utils`) and mounts
each share with SMB 3.0.

Test the mount before seeding: `dir C:\nas2aws\onefs\finance`
should list an empty directory, not an error. An empty directory
means the mount worked and the share is empty; an error means the
mount did not.

## 6. Check the node from the migration side

From `python/`, with the project venv active:

```bash
python -m migrate_s3.cli check-onefs --onefs-host 192.168.1.50
```

It checks TCP 445 (SMB), 8080 (Platform API), and 22 (SSH), and,
if `ONEFS_PASSWORD` is set, calls the Platform API for the cluster
name and OneFS version. Expected: `check-onefs: PASS`. If it fails,
fix the node before syncing; the sync command fails fast on the
same check when `--onefs-host` is given, instead of failing halfway
through a copy.

## 7. Seed the dataset onto OneFS

Generate the dataset locally, then copy it onto the mounted shares:

```bash
cd python
python -m migrate_s3.cli generate --root /tmp/nas2aws-source
python -m migrate_s3.cli seed-onefs --source /tmp/nas2aws-source --mount-root C:\nas2aws\onefs
```

Expected: 6 files in finance, 6 in it-support, 7 in it-operations,
5 in operations, 24 total. The `users.csv`, `acl.json`, and
`quotas.json` files are copied to the mount root itself, not onto
OneFS; they are migration metadata, not OneFS data. The share files
land on OneFS, through SMB, exactly as a user's files would.

Confirm on the node or in File Explorer that the files are there,
then run the sync in OneFS mode. The mount root is the source now.

## 8. Run the migration (OneFS -> AWS emulator)

Start the AWS emulator first. LocalStack, on the same host:

```bash
docker compose up -d
```

Then Terraform, then sync and verify, all with the OneFS source:

```bash
cd terraform
terraform init
terraform apply -var="endpoint_url=http://localhost:4566"
cd ../python
python -m migrate_s3.cli sync --source C:\nas2aws\onefs --source-mode onefs --onefs-host 192.168.1.50 --endpoint-url http://localhost:4566
python -m migrate_s3.cli verify --source C:\nas2aws\onefs --source-mode onefs --onefs-host 192.168.1.50 --endpoint-url http://localhost:4566
```

Or the one-command moto demo, which now accepts a OneFS source:
set `ONEFS_MOUNT_ROOT` in `scripts/run_local_demo.sh` to your mount
root and it will sync from OneFS instead of a generated directory.
The default demo still uses a local directory, so it runs anywhere.

Expected: 24 files uploaded, verify PASS on all 4 shares. The
`--source-mode onefs` flag validates the mount root first (metadata
files present, every share mounted and non-empty) and checks the
node is reachable before any copy starts.

## 9. Teardown

Order matters. Unmount and destroy in this order:

```bash
# 1. Copy the evidence first: the report output and the verify PASS line.
# 2. Destroy the AWS side.
cd terraform
terraform destroy -var="endpoint_url=http://localhost:4566"
docker compose down -v
# 3. Unmount the OneFS shares.
.\scripts\unmount_onefs.ps1 -MountRoot C:\nas2aws\onefs
# 4. Only then, power off or suspend the OneFS VM in Workstation Pro.
#    Suspending keeps the cluster for the next run; powering off and
#    deleting the VM is the full destroy.
```

Do not power off the OneFS VM while a sync is running, and do not
delete the VM while the shares are still mounted. The mount will
hang, and the sync will fail on the next file it tries to read.

## Troubleshooting

- **Node has no IP / host cannot ping it.** Network is not
  Bridged. Change the VM network adapter to Bridged, pick the
  physical adapter explicitly if the host is on Wi-Fi, and reboot
  the VM. NAT and Host-only will not work for this run.
- **Port 445 closed, 8080 open.** SMB is not enabled, or the host
  firewall is blocking outbound SMB. Check the WebUI Protocols >
  SMB, and Windows Defender Firewall on the host.
- **Platform API returns 401.** Wrong root password, or the
  password has expired. Log into the WebUI as root to confirm it.
- **Mount succeeds but the share looks empty after seeding.**
  The seed copied to the mount root on the host disk, not through
  the mount. That happens if `seed-onefs` ran before the mount, or
  the junction points at an empty drive. Unmount, remount, check
  `dir` on the share, then re-seed.
- **Sync is slow.** A single virtual node on a laptop disk is
  slow, and SMB 3.0 over a bridged adapter is not fast storage.
  The lab is 1.16 MB, so slow here means seconds, not minutes. If
  it takes minutes, the node is starved for RAM; give it 8 GB.
- **OneFS asks for a license.** The base simulator does not need
  one for SMB, users, and quotas. If a feature you clicked needs
  one, that feature is not part of this migration; go back to the
  base path in this guide.
- **"Single node, no protection" warnings.** Expected. A
  one-node virtual cluster has no data protection. That is fine
  for a lab; it is why the dataset is synthetic and the source is
  also on the host.

## What was run live, and what was not

- The OneFS virtual node itself was NOT run in this VM. This VM
  has no VMware Workstation Pro and no OneFS Simulator download,
  and OneFS cannot run here. The integration was tested against a
  local directory standing in for the mount root, with the same
  layout, the same 24 files, and the same sync/verify path.
- `check-onefs` was tested against an unreachable host (it fails
  cleanly with the bridged-network hint, exit 1) and against a
  local mock of the Platform API in the unit tests.
- The mount scripts were not run live here (no OneFS node, no
  SMB server to mount). They are written from the `net use` /
  `mount -t cifs` forms and print every command with `--dry-run`
  before running it, so the first live run on your machine is
  still yours to confirm, command by command.
- The AWS emulator side was run live against moto, as in `README.md`.
