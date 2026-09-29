"""Opt-in, same-live-UE checkpoints for a single LFM VLM episode.

This is not a UE save game. A restart during action execution is ambiguous and
must be rejected; a persisted reply before execution can be reused without API.
Original attempt directories are immutable across resumes. Videos remain
separate segments, explicitly identified in the final result.
"""
from __future__ import annotations

import base64
import copy
import ctypes
from ctypes import wintypes
import hashlib
import io
import json
import math
import os
from pathlib import Path
import shutil
import socket
import subprocess
import time

from PIL import Image, ImageChops, ImageStat


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.pending')
    with temp.open('w', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, path)


def file_hash(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


class RunLock:
    """OS-held lock: released by process death; never remove a stale lock file."""
    def __init__(self, path):
        self.path = Path(path)
        self.stream = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.stream = self.path.open('a+b')
        try:
            if self.path.stat().st_size == 0:
                self.stream.write(b'0')
                self.stream.flush()
            self.stream.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.stream.close()
            self.stream = None
            raise RuntimeError('Another runner owns this checkpoint/batch') from exc
        return self

    def __exit__(self, *args):
        if self.stream is not None:
            self.stream.close()
            self.stream = None


def listener_identity(host, port):
    """Bind resume to the actual local Windows listener, including PID reuse."""
    if os.name != 'nt' or host not in ('127.0.0.1', 'localhost'):
        raise ValueError('Live resume currently requires a local Windows UE')
    class Row(ctypes.Structure):
        _fields_ = [(k, wintypes.DWORD) for k in
                    ('state', 'addr', 'port', 'remote_addr', 'remote_port', 'pid')]
    api = ctypes.WinDLL('iphlpapi', use_last_error=True).GetExtendedTcpTable
    api.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD), wintypes.BOOL,
                    wintypes.ULONG, ctypes.c_int, wintypes.ULONG]
    api.restype = wintypes.DWORD
    size = wintypes.DWORD(0)
    code = api(None, ctypes.byref(size), False, 2, 3, 0)
    if code != 122:
        raise OSError(code, 'Cannot size Windows TCP listener table')
    for _ in range(3):
        buf = ctypes.create_string_buffer(size.value)
        code = api(buf, ctypes.byref(size), False, 2, 3, 0)
        if code == 0: break
        if code != 122: raise OSError(code, 'Cannot read Windows TCP listener table')
    if code: raise OSError(code, 'TCP listener table kept changing')
    count = wintypes.DWORD.from_buffer(buf).value
    rows = (Row * count).from_buffer(buf, ctypes.sizeof(wintypes.DWORD))
    owners = {int(r.pid) for r in rows if socket.ntohs(r.port & 0xffff) == int(port)}
    if len(owners) != 1:
        raise RuntimeError('Expected exactly one UE listener')
    pid = owners.pop()
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle: raise ctypes.WinError(ctypes.get_last_error())
    try:
        kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
        times = [wintypes.FILETIME() for _ in range(4)]
        if not kernel.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
            raise ctypes.WinError(ctypes.get_last_error())
        kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                                    wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
        path = ctypes.create_unicode_buffer(32768)
        chars = wintypes.DWORD(len(path))
        if not kernel.QueryFullProcessImageNameW(handle, 0, path, ctypes.byref(chars)):
            raise ctypes.WinError(ctypes.get_last_error())
        identity = dict(pid=pid, started_ticks=(times[0].dwHighDateTime << 32) | times[0].dwLowDateTime,
                        path=path.value)
    finally:
        kernel.CloseHandle(handle)
    identity['exe_sha256'] = file_hash(identity['path'])
    return identity


def fingerprint(exp, clock_mode):
    from omni_game_arena.models.backends.commercial.router_config import _router_path
    root = Path(__file__).resolve().parents[1]
    files = [Path(p) for p in exp.agent.prompt_skills]
    files += [_router_path()]
    # Include prompt/implementation revisions; only hashes, never credentials.
    files += list((root / 'prompts').rglob('*.txt'))
    files += list((root / 'prompts').rglob('*.py'))
    for folder in ('models', 'adapters', 'benchmark', 'env'):
        files += list((root / folder).rglob('*.py'))
    data = exp.to_dict()
    data.pop('run_id', None)
    data.update(clock_mode=clock_mode, checkpoint_version=1,
                files={str(p.resolve()): file_hash(p) for p in files})
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


def _pack_image(image):
    if image is None:
        return None
    stream = io.BytesIO()
    image.save(stream, format='PNG')
    return base64.b64encode(stream.getvalue()).decode('ascii')


def _unpack_image(value):
    if value is None:
        return None
    image = Image.open(io.BytesIO(base64.b64decode(value)))
    image.load()
    return image


def _pack_obs(obs):
    return dict(obs, image=_pack_image(obs.get('image')))


def _unpack_obs(obs):
    return dict(obs, image=_unpack_image(obs.get('image')))


def probe(env, *, allow_terminal_without_position=False):
    # get_score can swallow a timeout and return cached data. Clear that cache
    # so a failed query cannot pass a resume check with stale coordinates.
    client = env.client
    client.score_payload = None
    client.character_position = None
    client.get_score()
    payload = client.score_payload
    score = payload.get('score') if isinstance(payload, dict) else None
    if (isinstance(score, bool) or not isinstance(score, (int, float))
            or not math.isfinite(score)):
        raise RuntimeError('Fresh UE score/position is required for checkpointing')
    # UE legitimately omits character_position once a dead pawn is destroyed.
    # Only a confirmed game-over checkpoint may lack it; playable states still
    # require coordinates and never substitute cached values.
    if client.character_position is None and not (allow_terminal_without_position and client.game_over):
        raise RuntimeError('Fresh UE score/position is required for checkpointing')
    return dict(score=score, position=client.character_position,
                game_over=bool(client.game_over))


def verify_state(saved, current, old_image, new_image):
    if saved['game_over'] != current['game_over']:
        raise RuntimeError('UE terminal state changed since checkpoint')
    if abs(saved['score'] - current['score']) > 1e-6:
        raise RuntimeError('UE score changed since checkpoint')
    old_position, new_position = saved['position'], current['position']
    if old_position is None or new_position is None:
        if not (old_position is None and new_position is None
                and saved['game_over'] and current['game_over']):
            raise RuntimeError('UE position changed since checkpoint')
        distances = [0.0]
    elif isinstance(old_position, dict) and isinstance(new_position, dict):
        distances = [abs(old_position[k] - new_position[k]) for k in ('x', 'y', 'z')]
    else:
        if len(old_position) != 3 or len(new_position) != 3:
            raise RuntimeError('UE position format changed')
        distances = [abs(a - b) for a, b in zip(old_position, new_position)]
    if max(distances) > 0.05:
        raise RuntimeError('UE position changed since checkpoint')
    if old_image.size != new_image.size:
        raise RuntimeError('UE viewport size changed since checkpoint')
    # Allow small JPEG/temporal-render noise, not a changed camera/scene.
    rms = max(ImageStat.Stat(ImageChops.difference(old_image.convert('RGB'),
                                                 new_image.convert('RGB'))).rms)
    if rms > 2.0:
        raise RuntimeError(f'UE image changed since checkpoint (RMS={rms:.3f})')
    return rms


class EpisodeCheckpoint:
    def __init__(self, path, exp, clock_mode, resume=False):
        if clock_mode != 'lfm' or exp.agent.kind != 'vlm' or exp.params.frame_pack != 'none':
            raise ValueError('Checkpoints require LFM VLM with frame_pack=none')
        self.path = Path(path).resolve()
        self.exp = exp
        self.resume = resume
        self.fingerprint = fingerprint(exp, clock_mode)
        self.identity = listener_identity(exp.env.host, exp.env.port)
        self.data = None
        self.attempts = []
        self.prior_elapsed = 0.0
        self.start_step = 0
        if resume:
            self.data = json.loads(self.path.read_text(encoding='utf-8'))
            if self.data['fingerprint'] != self.fingerprint:
                raise RuntimeError('Checkpoint model/config/skill/route/code changed')
            if self.data['ue_identity'] != self.identity:
                raise RuntimeError('UE restarted or its executable changed; cannot resume this episode')
            if self.data['phase'] not in ('ready', 'action_ready', 'terminal'):
                raise RuntimeError('Action execution is uncertain; refusing to replay it')
            result_path = Path(self.data['run_dir']) / 'result.json'
            if result_path.exists() and json.loads(result_path.read_text(encoding='utf-8'))['status'] == 'ok':
                raise RuntimeError('Episode already completed; skip it instead of resuming')
            self.attempts = list(self.data['attempts'])
            self.prior_elapsed = self.data['env']['elapsed']
            self.start_step = self.data['step']
        elif self.path.exists():
            raise RuntimeError('Checkpoint exists; explicit --resume-checkpoint is required')

    def start_attempt(self, run_dir):
        self.run_dir = str(Path(run_dir).resolve())
        if self.resume:
            shutil.copy2(self.path, Path(run_dir) / 'resumed_checkpoint.json')
        self.attempts.append(self.run_dir)

    def capture(self, phase, env, agent, obs, step, recorder, *, action=None,
                latency=0.0, terminal_info=None):
        if not env.world_paused:
            raise RuntimeError('Cannot checkpoint an unpaused world')
        if agent.recap_blocks or agent.recap_messages or agent.experience:
            raise ValueError('Checkpointing recap/evolution agents is not supported')
        # The initial model observation is historically captured just before
        # pause. Keep that exact input, but guard against the actual paused
        # frame so a timer tick during pause delivery is not a false mismatch.
        guard_image = env._make_observation()['image']
        terminal_without_position = (phase == 'terminal'
                                     and (terminal_info or {}).get('done_reason') == 'game_over')
        self.data = dict(version=1, fingerprint=self.fingerprint, ue_identity=self.identity,
                         run_dir=self.run_dir, attempts=self.attempts, phase=phase,
                         step=step, observation=_pack_obs(obs),
                         state=probe(env, allow_terminal_without_position=terminal_without_position),
                         guard_image=_pack_image(guard_image),
                         history=[[_pack_image(im), text] for im, text in agent._history],
                         last_vlm_response=agent.last_vlm_response,
                         last_action_metadata=agent.last_action_metadata,
                         action=action, act_latency=latency,
                         records=copy.deepcopy(recorder.records), terminal_info=terminal_info,
                         env=dict(step_count=env.step_count, max_score_seen=env.max_score_seen,
                                  elapsed=time.time() - env.start_time), saved_at=time.time())
        atomic_json(self.path, self.data)

    def mark_executing(self):
        self.data['phase'] = 'executing'
        atomic_json(self.path, self.data)

    def restore(self, env, agent, recorder):
        from omni_game_arena.env.client_ue5 import UE5Client
        data = self.data
        env.client = UE5Client(host=env.host, port=env.port,
                              screenshot_quality=env.screenshot_quality)
        env.client.strict_response_matching = True
        if not env.client.connect():
            raise RuntimeError('Cannot reconnect to checkpoint UE')
        env.pause()
        obs = _unpack_obs(data['observation'])
        current = env._make_observation()
        env.client.check_game_over()
        terminal_without_position = (data['phase'] == 'terminal'
                                     and (data.get('terminal_info') or {}).get('done_reason') == 'game_over')
        rms = verify_state(data['state'], probe(env, allow_terminal_without_position=terminal_without_position),
                           _unpack_image(data['guard_image']), current['image'])
        self.resume_image_rms = rms
        env.step_count = data['env']['step_count']
        env.max_score_seen = data['env']['max_score_seen']
        env.start_time = time.time() - data['env']['elapsed']
        agent._history.clear()
        agent._history.extend((_unpack_image(im), text) for im, text in data['history'])
        agent.last_vlm_response = data['last_vlm_response']
        agent.last_action_metadata = data['last_action_metadata']
        recorder.records = copy.deepcopy(data['records'])
        # Prior attempts remain untouched, while the resumed trajectory contains
        # all observations needed by existing metrics/reflection consumers.
        for record in recorder.records:
            name = f"step_{record['step']:04d}.jpg"
            source = Path(data['run_dir']) / name
            if not source.is_file():
                raise RuntimeError(f'Checkpoint trajectory image missing: {source}')
            shutil.copy2(source, Path(recorder.output_dir) / name)
        terminal = Path(data['run_dir']) / 'terminal_observation.jpg'
        if terminal.exists():
            shutil.copy2(terminal, Path(recorder.output_dir) / terminal.name)
        return obs, data['step']

    def metadata(self):
        return dict(path=str(self.path), phase=self.data['phase'] if self.data else None,
                    resumed=self.resume, attempts=list(self.attempts),
                    resume_image_rms=getattr(self, 'resume_image_rms', None),
                    same_live_ue_only=True)

    def video_segments(self, current_video):
        segments = []
        for folder in self.attempts[:-1]:
            result = Path(folder) / 'result.json'
            if result.exists():
                result_data = json.loads(result.read_text(encoding='utf-8'))
                video = result_data.get('video_segment', result_data.get('video'))
                if video:
                    segments.append(video)
            else:
                # A force-killed encoder may leave an incomplete MP4. Do not
                # pretend it was validated or conceal the missing coverage.
                segments.append(dict(path=str(Path(folder) / 'episode.mp4'),
                                     error='Previous process exited without finalizing video'))
        if current_video:
            segments.append(current_video)
        return segments


def decode_video(path):
    """Decode every video frame; hashes also verify lossless segment ordering."""
    import imageio_ffmpeg
    proc = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-nostdin', '-v', 'error',
                           '-xerror', '-i', str(path), '-map', '0:v:0', '-vsync', '0',
                           '-f', 'framemd5', '-'], capture_output=True, text=True,
                          timeout=1800, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if proc.returncode or proc.stderr.strip():
        raise RuntimeError(f'Video decode failed: {path}: {proc.stderr[-1000:]}')
    lines = proc.stdout.splitlines()
    hashes = [line.rsplit(',', 1)[-1].strip() for line in lines if line and not line.startswith('#')]
    size = next((line.split(':', 1)[1].strip() for line in lines if line.startswith('#dimensions')), None)
    if not hashes or not size:
        raise RuntimeError(f'Video has no decodable frames: {path}')
    return dict(frames=len(hashes), dimensions=size, frame_hashes=hashes,
                decoded_sha256=hashlib.sha256('\n'.join(hashes).encode()).hexdigest())


def finalize_video(segments, run_dir):
    """Keep raw pieces, produce one ordered MP4, and fully verify its pixels."""
    import imageio_ffmpeg
    usable, expected, checks, sizes = [], [], [], set()
    for segment in segments:
        if segment.get('error'):
            raise RuntimeError('Incomplete video segment: ' + str(segment))
        # An interruption before the first action has no gameplay to record.
        if segment.get('frames') == 0 and segment.get('start_step') == segment.get('end_step_exclusive'):
            continue
        path = Path(segment['path'])
        decoded = decode_video(path)
        if decoded['frames'] != segment.get('frames'):
            raise RuntimeError(f'Video frame count disagrees with recorder: {path}')
        expected += decoded.pop('frame_hashes')
        sizes.add(decoded['dimensions'])
        usable.append(segment)
        checks.append(dict(path=str(path), file_sha256=file_hash(path), **decoded))
    if not usable or len(sizes) != 1 or len({s['fps'] for s in usable}) != 1:
        raise RuntimeError('Video segments missing or incompatible')
    result = dict(usable[-1])
    if len(usable) > 1:
        concat_path = Path(run_dir) / 'video_segments.ffconcat'
        concat_path.write_text('ffconcat version 1.0\n' + ''.join(
            "file '" + Path(s['path']).resolve().as_posix().replace("'", "'\\''") + "'\n"
            for s in usable), encoding='utf-8')
        target = Path(run_dir) / 'episode_complete.mp4'
        subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-nostdin', '-v', 'error', '-n',
                        '-f', 'concat', '-safe', '0', '-i', str(concat_path), '-c', 'copy',
                        '-movflags', '+faststart', str(target)], check=True, capture_output=True,
                       timeout=1800, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        combined = decode_video(target)
        if combined.pop('frame_hashes') != expected:
            raise RuntimeError('Joined video frames differ from the ordered original segments')
        result.update(path=str(target), frames=combined['frames'])
    else:
        combined = {k: v for k, v in checks[0].items() if k in ('frames', 'dimensions', 'decoded_sha256')}
    result.update(start_step=usable[0].get('start_step'), end_step_exclusive=usable[-1].get('end_step_exclusive'))
    validation = dict(full_decode=True, segment_count=len(usable), frames=len(expected),
                      segments=checks, ordered_frame_hashes_match=True, combined=combined,
                      path=result['path'], file_sha256=file_hash(result['path']))
    atomic_json(Path(run_dir) / 'video_validation.json', validation)
    return result, validation
