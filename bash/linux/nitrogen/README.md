# NitroGen Linux Run Scripts

Run from the repository root, one game at a time. These scripts run the
standard NitroGen test profile by default:

- 1 episode
- live viewer
- raw action log
- MP4 gameplay recording
- no NitroGen server reset between episodes

```bash
bash/linux/nitrogen/run_obstacle_run_3d.sh
bash/linux/nitrogen/run_obstacle_run_2d.sh
bash/linux/nitrogen/run_last_stand.sh
bash/linux/nitrogen/run_monster_shoot.sh
bash/linux/nitrogen/run_cue_chase.sh
bash/linux/nitrogen/run_scene_escape.sh
bash/linux/nitrogen/run_solo_craft.sh
```

To run every game script in this directory once:

```bash
bash/linux/nitrogen/run_all.sh
```

Extra `scripts/run_benchmark.py` arguments are passed through after the
standard defaults, so temporary overrides still work:

```bash
bash/linux/nitrogen/run_obstacle_run_3d.sh --dry-run --episodes 1
bash/linux/nitrogen/run_obstacle_run_3d.sh --host <ip> --port <port>
bash/linux/nitrogen/run_obstacle_run_3d.sh --set agents_defaults.extra.url="<ip>:<port>"
```

The scripts pass `--host`, `--port`, and `--live` explicitly. To change the UE
endpoint for one shell session:

```bash
export IP=<ip>
export PORT=<port>
bash/linux/nitrogen/run_obstacle_run_3d.sh
```

To run a 5-episode batch in the current shell session:

```bash
export EPISODES=5
bash/linux/nitrogen/run_obstacle_run_3d.sh
```

Shared defaults live in:

```text
configs/nitrogen/base.yaml
```
