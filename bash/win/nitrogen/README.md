# NitroGen Windows Run Scripts

Run from the repository root, one game at a time. These scripts run the
standard NitroGen test profile by default:

- 1 episode
- live viewer
- raw action log
- MP4 gameplay recording
- no NitroGen server reset between episodes

```cmd
bash\win\nitrogen\run_obstacle_run_3d.cmd
bash\win\nitrogen\run_obstacle_run_2d.cmd
bash\win\nitrogen\run_last_stand.cmd
bash\win\nitrogen\run_monster_shoot.cmd
bash\win\nitrogen\run_cue_chase.cmd
bash\win\nitrogen\run_scene_escape.cmd
bash\win\nitrogen\run_solo_craft.cmd
```

To run every game cmd in this directory once:

```cmd
bash\win\nitrogen\run_all.cmd
```

Extra `scripts\run_benchmark.py` arguments are passed through after the
standard defaults, so temporary overrides still work:

```cmd
bash\win\nitrogen\run_obstacle_run_3d.cmd --dry-run --episodes 1
bash\win\nitrogen\run_obstacle_run_3d.cmd --host <ip> --port <port>
bash\win\nitrogen\run_obstacle_run_3d.cmd --set agents_defaults.extra.url="<ip>:<port>"
```

The scripts pass `--host`, `--port`, and `--live` explicitly. To change the UE
endpoint for one cmd session:

```cmd
set IP=<ip>
set PORT=<port>
bash\win\nitrogen\run_obstacle_run_3d.cmd
```

To run a 5-episode batch in the current cmd session:

```cmd
set EPISODES=5
bash\win\nitrogen\run_obstacle_run_3d.cmd
```

Shared defaults live in:

```text
configs\nitrogen\base.yaml
```
