# KU-owned AVD workflow

The extension does not clone, create or launch an AVD itself. Start a disposable
KU container with `../scripts/start-ku-environment.sh` or the full
`../scripts/start-simulator.sh` command. Those commands delegate to KU's own CLI,
image entrypoint, emulator script and noVNC script.

KU's emulator initially starts exactly as its image defines. Before extension
initialization or every full reset, the orchestrator runs the same command used
by KU tasks inside the container:

```bash
adb emu avd snapshot load init_state
```

The extension must never run `snapshot save init_state`, edit the embedded AVD,
or commit the disposable container.

## Baseline comparison

With `demo` running, capture the KU state immediately after restoring
`init_state` and before installing extension APKs:

```bash
python3 capture-baseline.py \
  --serial 127.0.0.1:5556 \
  --avd Pixel_8_API_34_x86_64 \
  --snapshot init_state \
  --ku-container ku_simulator_extension_demo_0 \
  --output baseline-manifest.json
```

After installing the six APKs, repeat with `--output expanded-manifest.json`,
then compare:

```bash
python3 compare-manifests.py baseline-manifest.json expanded-manifest.json \
  --output manifest-diff.json
```

The comparison permits only the six extension package names. It fails for any
change to KU image/runtime identity, AVD/snapshot identity, system settings or a
pre-existing package.

`start-overlay.sh` remains only as a compatibility wrapper and delegates to
`start-simulator.sh`; it contains no emulator flags or snapshot implementation.
