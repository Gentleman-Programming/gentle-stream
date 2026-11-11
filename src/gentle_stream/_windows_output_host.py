"""Standard-library-only owned Windows sink supervisor.

Private framed stdin: !cII (D/P/R, positive sequence, payload bytes), followed
by at most 8192 bytes of float32 stereo PCM for D only. Stdout is !cI ACKs.
P acknowledges only after the owned native child and its writer have stopped.
No protocol field names an executable, path, PID, or shell command.
"""
import queue
import struct
import subprocess
import sys
import threading

HEADER = struct.Struct('!cII')
ACK = struct.Struct('!cI')
MAX_PCM = 8192


def _read(source, size):
    data = bytearray()
    while len(data) < size:
        part = source.read(size - len(data))
        if not part:
            if not data:
                return None
            raise ValueError('truncated output frame')
        data.extend(part)
    return bytes(data)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 2 or not argv[1].isdigit() or not 100 <= int(argv[1]) <= 192000:
        print('Windows audio: expected FFplay path and sample rate', file=sys.stderr)
        return 1
    output_lock = threading.Lock()
    process = writer = pending = None
    errors = []
    cancelled = threading.Event()

    def native_error(message):
        code = process.poll() if process is not None else None
        return f'{message}; FFplay exit status {code}' if code is not None else message

    def acknowledge(op, sequence):
        with output_lock:
            sys.stdout.buffer.write(ACK.pack(op, sequence))
            sys.stdout.buffer.flush()

    def start_sink():
        nonlocal process, writer, pending
        process = subprocess.Popen(
            [argv[0], '-hide_banner', '-loglevel', 'warning', '-nostats', '-nodisp', '-autoexit',
             '-window_title', 'gentle-stream-windows-audio', '-f', 'f32le', '-ar', argv[1],
             '-ch_layout', 'stereo', '-probesize', '32', '-analyzeduration', '1', '-i', 'pipe:0'],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=sys.stderr, bufsize=0)
        pending = queue.Queue(maxsize=4)
        cancelled.clear()
        errors.clear()
        # Capture this generation; a resumed sink never inherits old PCM.
        child, work = process, pending

        def write_pcm():
            try:
                while (item := work.get()) is not None:
                    sequence, chunk = item
                    data = memoryview(chunk)
                    while data and not cancelled.is_set():
                        count = child.stdin.write(data)
                        if not count:
                            raise BrokenPipeError('native output closed')
                        data = data[count:]
                    if not cancelled.is_set():
                        acknowledge(b'D', sequence)
            except (OSError, ValueError) as error:
                if not cancelled.is_set():
                    errors.append(error)
                    acknowledge(b'E', sequence)
            finally:
                child.stdin.close()

        writer = threading.Thread(target=write_pcm, daemon=True, name='native-audio-write')
        writer.start()

    def cut_sink():
        nonlocal process, writer, pending
        if process is None:
            return
        cancelled.set()
        # Never wait for a blocked pipe writer before terminating its owned sink.
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                process.kill()  # Still the same native Popen handle, never a global PID search.
                process.wait(timeout=1)
        try:
            pending.put_nowait(None)
        except queue.Full:
            pass
        writer.join(timeout=1)
        if writer.is_alive():
            raise OSError('native audio writer did not stop')
        process = writer = pending = None

    try:
        start_sink()
        previous = 0
        while (raw := _read(sys.stdin.buffer, HEADER.size)) is not None:
            op, sequence, size = HEADER.unpack(raw)
            if (op not in (b'D', b'P', b'R') or sequence <= previous
                    or size > MAX_PCM or (op != b'D' and size != 0)
                    or (op == b'D' and (size == 0 or size % 8))):
                raise ValueError('invalid output frame')
            previous = sequence
            if op == b'D':
                chunk = _read(sys.stdin.buffer, size)
                if chunk is None:
                    raise ValueError('truncated PCM frame')
                if process is None or process.poll() is not None or errors:
                    raise OSError(native_error('native output unavailable'))
                pending.put_nowait((sequence, chunk))
            elif op == b'P':
                if errors or (process is not None and process.poll() is not None):
                    raise OSError(native_error('native output failed before pause'))
                cut_sink()
                acknowledge(op, sequence)
            else:
                if process is None:
                    start_sink()
                elif process.poll() is not None or errors:
                    raise OSError(native_error('native output unavailable'))
                acknowledge(op, sequence)
        if process is None:
            return 0
        pending.put(None, timeout=1)
        writer.join(timeout=1)
        if writer.is_alive() or errors:
            raise OSError(native_error('native audio writer stalled or failed'))
        return process.wait(timeout=1)
    except (OSError, ValueError, queue.Full, subprocess.TimeoutExpired) as error:
        print(f'Windows audio: {error}', file=sys.stderr)
        return 1
    finally:
        cut_sink()


if __name__ == '__main__':
    sys.exit(main())
