"""Record the sandbox desktop, then upload the finalized MP4."""

import asyncio
import signal
from pathlib import Path
from tempfile import TemporaryDirectory

MAX_VIDEO_BYTES = 64 * 1024 * 1024


class Recording:
    def __init__(self):
        self.process = None
        self.directory = None
        self.path = None
        self.name = "Walkthrough"

    async def start(self, name="Walkthrough"):
        if self.process:
            raise ValueError("A recording is already active. Stop it before starting another.")
        self.directory = TemporaryDirectory(prefix="litereview-recording-")
        self.path = Path(self.directory.name) / "walkthrough.mp4"
        self.name = name
        try:
            self.process = await asyncio.create_subprocess_exec(
                "ffmpeg", "-nostdin", "-y", "-loglevel", "error", "-f", "x11grab",
                "-video_size", "1280x900", "-framerate", "12", "-i", ":99",
                "-an", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "25",
                "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-fs", str(MAX_VIDEO_BYTES - 1024 * 1024),
                str(self.path), stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.sleep(0.3)
            if self.process.returncode is not None:
                raise RuntimeError("Screen recording could not start. Check the sandbox desktop and ffmpeg.")
        except BaseException:
            await self.close()
            raise
        return {"recording": True, "name": name}

    async def stop(self, relay):
        if not self.process:
            raise ValueError("No recording is active.")
        ended_early = self.process.returncode is not None
        if not ended_early:
            self.process.send_signal(signal.SIGINT)
            try:
                await asyncio.wait_for(self.process.wait(), 15)
            except TimeoutError:
                await self.close()
                raise RuntimeError("Could not finalize the recording. Record the walkthrough again.") from None
        if not self.path.is_file() or not self.path.stat().st_size:
            await self.close()
            raise RuntimeError("The recording contains no video.")
        # Keep the finalized file on upload failure so stop can retry without losing the walkthrough.
        result = await relay.upload_video(self.path, self.name)
        await self.close()
        return {"recording": False, "artifact_id": result["id"], "mime": "video/mp4",
                "ended_early": ended_early,
                "note": "Recording stopped before requested, possibly at the 64 MB file limit. Check its coverage." if ended_early else ""}

    async def close(self):
        if self.process and self.process.returncode is None:
            self.process.kill()
            await self.process.wait()
        self.process = None
        if self.directory:
            self.directory.cleanup()
        self.directory = self.path = None
