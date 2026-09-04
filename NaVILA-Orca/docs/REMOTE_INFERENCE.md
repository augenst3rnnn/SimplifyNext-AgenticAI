<p align="right"><sub><strong>English</strong> · <a href="REMOTE_INFERENCE_zh.md">中文</a></sub></p>

<a id="remote-inference"></a>

# Option B: remote-inference deployment

This chapter applies only to Option B: the OrcaLab GUI, scene, camera,
low-level control, and navigation loop run on the **OrcaLab client**, while
NaVILA runs on a dedicated **GPU inference server**. If every process runs on
one machine, use [Option A: single-host deployment](GETTING_STARTED.md#option-a-single-host).

```text
OrcaLab client navigation → 127.0.0.1:54321 → SSH tunnel
                         → inference server 127.0.0.1:54321 → NaVILA
```

The complete order is: **install each host → start the remote service → create
the tunnel → run the end-to-end check → prepare the scene and navigate → clean
up**.

## Conventions and security boundary

- Both hosts need Git, Conda, an NVIDIA driver that passes `nvidia-smi`, and an
  Orca_VLN checkout at the same revision.
- Every command in this guide runs from the `NaVILA-Orca` directory. In a full
  checkout, first run `cd /path/to/Orca_VLN/NaVILA-Orca`; for an extracted
  developer kit, enter its root directory.
- Do not run `setup_all.sh` on both hosts or use `doctor.sh` for a split
  deployment. Both scripts expect the two environments on one machine.
- Bind the NaVILA service to `127.0.0.1` on the inference server. Do not expose
  its TCP port publicly. The service has no TLS or authentication of its own;
  only the SSH port needs to be reachable from the client.

## OrcaLab client: install the client environment

Run this from the client's `NaVILA-Orca` directory:

```bash
./scripts/check_nvidia_driver.sh
./scripts/setup_system_deps.sh
./scripts/setup_orcalab_env.sh
```

## Inference server: install and start NaVILA

Install the NaVILA environment and model from the inference server's
`NaVILA-Orca` directory:

```bash
./scripts/check_nvidia_driver.sh
./scripts/setup_navila_env.sh
./scripts/download_navila_model.sh
```

Then start the service and keep its terminal open:

```bash
REMOTE_VLM_PORT="54321"

NAVVLM_HOST="127.0.0.1" \
NAVVLM_PORT="$REMOTE_VLM_PORT" \
./scripts/start_navvlm_server.sh
```

Continue only after the service reports listening on `127.0.0.1:54321`.

## OrcaLab client: create the SSH tunnel

Set the connection values in a client terminal. The IP, account, and ports
below are placeholders:

```bash
mkdir -p "$HOME/.ssh"
chmod 700 "$HOME/.ssh"

SSH_HOST="xx.xx.xx.xx"             # actual inference-server IP
SSH_USER="your-ssh-user"          # remote account
SSH_PORT="22"
LOCAL_VLM_PORT="54321"
REMOTE_VLM_PORT="54321"
SSH_CONTROL_SOCKET="${HOME}/.ssh/orca-vln-%C"
```

### Automatic authentication negotiation (default)

The main command below does not force password or key authentication. OpenSSH
negotiates automatically from the client configuration, SSH agent, and server
policy. If earlier methods do not succeed and both client and server policy
permit interactive password authentication, SSH prompts for the account
password. It moves to the background only after authentication and forwarding
succeed.

```bash
ssh -p "$SSH_PORT" \
  -M -S "$SSH_CONTROL_SOCKET" \
  -f -N -T \
  -o ExitOnForwardFailure=yes \
  -o ServerAliveInterval=30 \
  -o ServerAliveCountMax=3 \
  -L "127.0.0.1:${LOCAL_VLM_PORT}:127.0.0.1:${REMOTE_VLM_PORT}" \
  "${SSH_USER}@${SSH_HOST}"
```

### Explicit PEM key (PEM users only)

> **Use this section only when you have a PEM key that must be specified
> explicitly. If you do not have a PEM key, skip this section and use the main
> command above unchanged.**

First set the PEM key path and permissions:

```bash
SSH_KEY_PATH="/path/to/private-key.pem"
chmod 600 "$SSH_KEY_PATH"
```

Only when using this PEM key, insert the two `+` lines between `ssh -p` and
`-M` in the main command; keep the remaining tunnel options unchanged:

```diff
ssh -p "$SSH_PORT" \
+  -i "$SSH_KEY_PATH" \
+  -o IdentitiesOnly=yes \
  -M -S "$SSH_CONTROL_SOCKET" \
```

## OrcaLab client: run the end-to-end tunnel and NaVILA protocol check

Before navigation, send a NaVILA protocol health request through the client's
local forwarded port:

```bash
./scripts/check_navvlm_endpoint.py \
  --host 127.0.0.1 \
  --port "$LOCAL_VLM_PORT"
```

The check returns success only after receiving the matching protocol response
from the remote NaVILA service. It traverses:

```text
local forwarded port → SSH tunnel → remote loopback port → NaVILA application
```

The health request does not decode images or run model inference.
`ssh -O check` confirms only the SSH master, and `ss` confirms only a local
listener; neither replaces the end-to-end check. The NaVILA server currently
handles requests serially, so run this before navigation. A timeout during an
active navigation run may only mean that the server is busy with inference.

## OrcaLab client: prepare the scene and navigate

Start the OrcaLab GUI:

```bash
./scripts/start_orcalab_gui.sh
```

Follow [Step 1 in the getting-started guide](GETTING_STARTED.md#scene-setup)
to subscribe to and select `VLN_Presentation`, load `factory.json`, and then
choose **Run → Start Simulation → No Simulation Program → Start** in the GUI.
In the client shell that created the tunnel, run:

```bash
./scripts/run_orcalab_scene_locomotion.sh \
  --vlm-host 127.0.0.1 \
  --vlm-port "$LOCAL_VLM_PORT"
```

With the default `LOCAL_VLM_PORT=54321`, the two VLM arguments may be omitted.
Passing them explicitly prevents a mismatch after changing the local port.

## Clean up

After navigation, close only the SSH master created for this project:

```bash
ssh -p "$SSH_PORT" \
  -S "$SSH_CONTROL_SOCKET" \
  -O exit \
  "${SSH_USER}@${SSH_HOST}"
```

Finally, stop NaVILA with `Ctrl+C` in the inference-server terminal.

## Remote-deployment troubleshooting

| Symptom | Check first | Explanation |
| --- | --- | --- |
| Authentication fails without a password prompt | Client SSH configuration, agent, and server authentication policy | The default command negotiates automatically; use `ssh -v` to inspect attempted methods, and never place the password in the command; no prompt is expected when a key or agent succeeds |
| `Address already in use` | `LOCAL_VLM_PORT` | Choose a free client port and pass the same value to both the check script and navigation command |
| End-to-end check reports connection refused, EOF, or reset | Inference service, `REMOTE_VLM_PORT`, and SSH forwarding target | A live SSH master or local listener does not prove that remote NaVILA is reachable |
| End-to-end check times out | Tunnel and remote-service state | Before navigation, this normally indicates a broken path; during navigation, the single-threaded service may be busy with inference |
| Service name or protocol version differs | Actual forwarding target and checkout versions | Use the same checkout version on both hosts and confirm no other service owns the remote port |
