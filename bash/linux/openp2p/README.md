# OpenP2P Linux Run Scripts

Run from the repository root, one game at a time. These scripts run the
standard OpenP2P test profile by default:

- 1 episode
- live viewer
- raw action log
- MP4 gameplay recording

```bash
bash/linux/openp2p/run_obstacle_run_3d.sh
bash/linux/openp2p/run_obstacle_run_2d.sh
bash/linux/openp2p/run_last_stand.sh
bash/linux/openp2p/run_monster_shoot.sh
bash/linux/openp2p/run_cue_chase.sh
bash/linux/openp2p/run_scene_escape.sh
bash/linux/openp2p/run_solo_craft.sh
```

To run every game script in this directory once:

```bash
bash/linux/openp2p/run_all.sh
```

Extra `scripts/run_benchmark.py` arguments are passed through after the
standard defaults, so temporary overrides still work:

```bash
bash/linux/openp2p/run_obstacle_run_3d.sh --dry-run --episodes 1
bash/linux/openp2p/run_obstacle_run_3d.sh --host <ip> --port <port>
bash/linux/openp2p/run_obstacle_run_3d.sh --set agents_defaults.extra.url="<ip>:<port>"
```

The scripts pass `--host`, `--port`, and `--live` explicitly. To change the UE
endpoint for one shell session:

```bash
export IP=<ip>
export PORT=<port>
bash/linux/openp2p/run_obstacle_run_3d.sh
```

To run a 5-episode batch in the current shell session:

```bash
export EPISODES=5
bash/linux/openp2p/run_obstacle_run_3d.sh
```

Shared defaults live in:

```text
configs/openp2p/base.yaml
```
